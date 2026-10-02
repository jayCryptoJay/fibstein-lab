"""A final period is consumed before evaluation, and never exported as performance data.

These are application-level research controls, not a sandbox against an owner
editing Python, SQLite or cached market data directly.
"""
import gzip, json, re, uuid
from datetime import date, datetime, timedelta, timezone
from math import isfinite
from pathlib import Path
from . import registry
from .config import Config


def bounds(c):
    # Include exactly the warmup load_pair can read, not just the trading dates.
    warmup = max(c.htf_ema*4*1.5/24, c.ema_slow*c.timeframe*2/1440, 3)
    start = datetime.combine(c.start, datetime.min.time(), timezone.utc) - timedelta(days=warmup)
    end = datetime.combine(c.end, datetime.min.time(), timezone.utc)
    return start.isoformat(), end.isoformat()


def instant(value):
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


def overlaps(a, b, c, d): return instant(a) < instant(d) and instant(c) < instant(b)


def research_access(c, path=None):
    """Commit access before any ordinary job reads data; failed jobs also contaminate a period."""
    start, end = bounds(c)
    research_range(start, end, path)


def research_range(start, end, path=None):
    with registry.session(path) as db:
        db.execute('BEGIN IMMEDIATE')
        for row in db.execute('SELECT window_start,window_end FROM holdout_windows UNION ALL SELECT window_start,window_end FROM holdout_ledger'):
            if overlaps(start, end, row['window_start'], row['window_end']):
                raise ValueError('Research range (including warmup) overlaps a locked final holdout.')
        db.execute('INSERT INTO research_access (window_start,window_end) VALUES (?,?)', (start, end))


def import_access(candles, funding=None, path=None):
    import io
    import pandas as pd
    from .data import normalize
    f = normalize(pd.read_csv(io.StringIO(candles)))
    research_range(f.index.min().isoformat(), (f.index.max() + pd.Timedelta(minutes=1)).isoformat(), path)
    if funding:
        timestamps = pd.to_datetime(pd.read_csv(io.StringIO(funding))['timestamp'], utc=True, format='mixed')
        if not timestamps.empty:
            research_range(timestamps.min().isoformat(), (timestamps.max() + pd.Timedelta(microseconds=1)).isoformat(), path)


def lock(key, start, end, path=None, results_dir=None):
    """Freeze the latest fold's training winner, never a winner chosen from test results."""
    start = date.fromisoformat(start).isoformat(); end = date.fromisoformat(end).isoformat()
    if end <= start: raise ValueError('Holdout end must follow start (exclusive).')
    engine = registry.engine_fingerprint(); version = registry.strategy_version(key, engine)
    with registry.session(path) as db:
        db.execute('BEGIN IMMEDIATE')
        row = registry.require(db, key)
        if row['status'] == 'archived': raise ValueError('Reopen the strategy before locking a holdout.')
        if registry.sync(db, results_dir or registry.ROOT/'workspace'/'runs')['unreadable']:
            raise ValueError('Unreadable saved runs prevent proving that this period is fresh.')
        e = registry.evidence(db, key)
        if not e or e['engine_sha256'] != engine: raise ValueError('Current-engine walk-forward evidence is required.')
        if start < e['config']['end']: raise ValueError('Final holdout must follow the entire development period.')
        if db.execute('SELECT 1 FROM holdout_windows WHERE strategy_version=?', (version,)).fetchone():
            raise ValueError('This strategy version already has a locked holdout; it cannot be replaced.')
        # Records predating access logging must also disqualify already-inspected dates.
        ranges = list(db.execute('SELECT window_start,window_end FROM research_access'))
        ranges += list(db.execute('SELECT window_start,window_end FROM holdout_windows UNION ALL SELECT window_start,window_end FROM holdout_ledger'))
        for study in db.execute('SELECT config FROM studies'):
            a, b = bounds(Config.model_validate_json(study['config'])); ranges.append({'window_start': a, 'window_end': b})
        if any(overlaps(start, end, r['window_start'], r['window_end']) for r in ranges):
            raise ValueError('This period has already been accessed or reserved. Choose fresh dates.')
        if not re.fullmatch(r'[a-zA-Z0-9_-]+', e['id']): raise ValueError('Invalid evidence run identifier.')
        with gzip.open(Path(results_dir or registry.ROOT/'workspace'/'runs')/f'{e["id"]}.json.gz', 'rt') as f: result = json.load(f)
        if any(result.get(k) != e[k] for k in ('id', 'config', 'engine_sha256', 'kind', 'metrics')):
            raise ValueError('Saved evidence does not match the registry.')
        folds = result.get('folds') or []; fold = folds[-1] if folds else {}
        selected = fold.get('selected')
        eligible = [r for r in fold.get('training_ranking', []) if r.get('eligible')]
        if selected is None or not eligible or eligible[0]['parameters'] != selected:
            raise ValueError('The latest fold must have an eligible training winner; cash folds cannot be deployed.')
        c = Config.model_validate({**e['config'], **selected})
        if c.strategy != key: raise ValueError('Training selection changed the strategy identity.')
        frozen = {**c.model_dump(mode='json'), 'start': start, 'end': end}
        wid = uuid.uuid4().hex
        registry.ensure_version(db, key, engine)
        db.execute('INSERT INTO holdout_windows VALUES (?,?,?,?,?,?,?,?,?)',
                   (wid, key, version, engine, e['id'], start, end, json.dumps(frozen), registry.now()))
        registry.note(db, key, 'holdout_locked', f'Locked {wid}, {start} to {end}, version {version}, evidence {e["id"]}.')
        return {'id': wid, 'strategy': key, 'strategy_version': version, 'start': start, 'end': end, 'status': 'locked'}


def claim(wid, path=None):
    """The transaction commits before data access, so crashes and concurrent callers cannot retry."""
    with registry.session(path) as db:
        db.execute('BEGIN IMMEDIATE')
        found = db.execute('SELECT * FROM holdout_windows WHERE id=?', (wid,)).fetchone()
        if not found: raise ValueError('Unknown locked holdout.')
        row = dict(found); key = row['strategy']
        if registry.require(db, key)['status'] == 'archived': raise ValueError('An archived strategy cannot consume a holdout.')
        if row['engine_sha256'] != registry.engine_fingerprint(): raise ValueError('Code changed after locking; this version cannot be evaluated.')
        if db.execute('SELECT 1 FROM holdout_ledger WHERE window_id=? OR strategy_version=?', (wid, row['strategy_version'])).fetchone():
            raise ValueError('Holdout attempt already consumed, including failed or interrupted attempts.')
        g = registry.gates(db, key)
        if g['evidence_run'] != row['evidence_run']: raise ValueError('Evidence changed after locking; this holdout cannot be evaluated.')
        blocking = [x['id'] for x in g['gates'] if x['id'] != 'final_holdout' and x['status'] != 'pass']
        if blocking: raise ValueError('Holdout remains locked. Required gates: ' + ', '.join(blocking))
        c = Config.model_validate_json(row['config'])
        rid = uuid.uuid4().hex; created = registry.now()
        db.execute('INSERT INTO holdout_ledger (strategy,engine_sha256,window_start,window_end,run_id,passed,created,strategy_version,window_id,status,evidence_run) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                   (key, row['engine_sha256'], row['window_start'], row['window_end'], rid, 0, created, row['strategy_version'], wid, 'running', row['evidence_run']))
        registry.record_run(db, {'id': rid, 'kind': 'holdout', 'created': created, 'config': c.model_dump(mode='json'), 'engine_sha256': row['engine_sha256']})
        registry.note(db, key, 'holdout_consumed', f'Attempt {wid} permanently consumed before evaluation. An interruption counts as failure.')
        return row, c


def evaluate(wid, path=None, cancel=lambda: False):
    if cancel(): raise ValueError('Holdout cancelled before an attempt was consumed.')
    row, c = claim(wid, path)
    passed = False
    try:
        from .engine import run_backtest
        result = run_backtest(c, cancel=cancel)  # No progress, saved run, or public exception may reveal performance.
        m = result['metrics']
        passed = (all(isinstance(m[k], (int, float)) and isfinite(m[k]) for k in ('trade_count', 'net_pnl', 'expectancy_r', 'liquidations'))
                  and m['trade_count'] >= registry.HELD_OUT_TRADES and m['net_pnl'] > 0 and m['expectancy_r'] > 0 and m['liquidations'] == 0
                  and not cancel() and registry.engine_fingerprint() == row['engine_sha256'])
    except Exception:
        # Even an error message can contain a metric; public output is deliberately only the decision.
        passed = False
    with registry.session(path) as db:
        db.execute('UPDATE holdout_ledger SET passed=?,status=? WHERE window_id=? AND status=?', (int(passed), 'passed' if passed else 'failed', wid, 'running'))
        registry.note(db, row['strategy'], 'holdout_result', 'Passed.' if passed else 'Failed. Attempt remains consumed.')
    return {'status': 'passed' if passed else 'failed'}
