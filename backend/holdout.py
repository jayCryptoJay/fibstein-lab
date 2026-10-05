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
from .data import load_pair

# Dates that can never be a final holdout again: a live lock, or any attempt ever recorded. A released lock is neither.
RESERVED = ('SELECT window_start,window_end FROM holdout_windows WHERE released IS NULL '
            'UNION ALL SELECT window_start,window_end FROM holdout_ledger')
# Dates ordinary research may not read: locked and not yet evaluated. Once an attempt is consumed its
# verdict is fixed, so the period becomes development data like any other; it only stops being fresh.
LIVE = ('SELECT window_start,window_end FROM holdout_windows w WHERE released IS NULL '
        'AND NOT EXISTS (SELECT 1 FROM holdout_ledger l WHERE l.window_id=w.id)')


def warmup_days(c):
    # The same history load_pair reads before the first trading day.
    return max(c.htf_ema*4*1.5/24, c.ema_slow*c.timeframe*2/1440, 3)


def bounds(c):
    # Include exactly the warmup load_pair can read, not just the trading dates.
    start = datetime.combine(c.start, datetime.min.time(), timezone.utc) - timedelta(days=warmup_days(c))
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
        for row in db.execute(LIVE):
            if overlaps(start, end, row['window_start'], row['window_end']):
                raise ValueError('Research range (including warmup) overlaps a locked final holdout.')
        db.execute('INSERT OR IGNORE INTO research_access (window_start,window_end) VALUES (?,?)', (start, end))


def import_guard(candles, funding=None, path=None):
    """Imported candles may not change dates that are already locked or evaluated.

    Importing is not research access: nothing is shown and nothing is run, and a CSV user must be
    able to load a final period before locking it. Fetching data is how a holdout becomes possible.
    """
    import io
    import pandas as pd
    from .data import normalize
    f = normalize(pd.read_csv(io.StringIO(candles)))
    ranges = [(f.index.min().isoformat(), (f.index.max() + pd.Timedelta(minutes=1)).isoformat())]
    if funding:
        timestamps = pd.to_datetime(pd.read_csv(io.StringIO(funding))['timestamp'], utc=True, format='mixed')
        if not timestamps.empty: ranges.append((timestamps.min().isoformat(), (timestamps.max() + pd.Timedelta(microseconds=1)).isoformat()))
    with registry.session(path) as db:
        for row in db.execute(LIVE):
            if any(overlaps(a, b, row['window_start'], row['window_end']) for a, b in ranges):
                raise ValueError('These dates belong to a locked final holdout; its data cannot be replaced.')


def lock(key, start, end, path=None, results_dir=None):
    """Freeze the latest fold's training winner, never a winner chosen from test results."""
    start = date.fromisoformat(start).isoformat(); end = date.fromisoformat(end).isoformat()
    if end <= start: raise ValueError('Holdout end must follow start (exclusive).')
    engine = registry.engine_fingerprint(); version = registry.strategy_version(key, engine)
    results = Path(results_dir or registry.paths.WORKSPACE/'runs')
    # Reading every saved run can be slow; do it before taking the write lock, not while holding it.
    with registry.session(path) as db: unreadable = registry.sync(db, results)['unreadable']
    if unreadable: raise ValueError('Unreadable saved runs prevent proving that this period is fresh.')
    with registry.session(path) as db:
        db.execute('BEGIN IMMEDIATE')
        row = registry.require(db, key)
        if row['status'] == 'archived': raise ValueError('Reopen the strategy before locking a holdout.')
        e = registry.evidence(db, key)
        if not e or e['engine_sha256'] != engine: raise ValueError('Current-engine walk-forward evidence is required.')
        if start < e['config']['end']: raise ValueError('Final holdout must follow the entire development period.')
        if db.execute('SELECT 1 FROM holdout_windows WHERE strategy_version=? AND released IS NULL', (version,)).fetchone():
            raise ValueError('This strategy version already has a locked holdout; it cannot be replaced. Release it first if it was never evaluated.')
        if db.execute('SELECT 1 FROM holdout_ledger WHERE strategy_version=?', (version,)).fetchone():
            raise ValueError('This strategy version has already used its final holdout.')
        # Records predating access logging must also disqualify already-inspected dates.
        ranges = list(db.execute('SELECT window_start,window_end FROM research_access')) + list(db.execute(RESERVED))
        for study in db.execute('SELECT config FROM studies'):
            a, b = bounds(Config.model_validate_json(study['config'])); ranges.append({'window_start': a, 'window_end': b})
        if any(overlaps(start, end, r['window_start'], r['window_end']) for r in ranges):
            raise ValueError('This period has already been accessed or reserved. Choose fresh dates.')
        if not re.fullmatch(r'[a-zA-Z0-9_-]+', e['id']): raise ValueError('Invalid evidence run identifier.')
        with gzip.open(results/f'{e["id"]}.json.gz', 'rt') as f: result = json.load(f)
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
        db.execute('INSERT INTO holdout_windows (id,strategy,strategy_version,engine_sha256,evidence_run,window_start,window_end,config,created) VALUES (?,?,?,?,?,?,?,?,?)',
                   (wid, key, version, engine, e['id'], start, end, json.dumps(frozen), registry.now()))
        registry.note(db, key, 'holdout_locked', f'Locked {wid}, {start} to {end}, version {version}, evidence {e["id"]}.')
        return {'id': wid, 'strategy': key, 'strategy_version': version, 'start': start, 'end': end, 'status': 'locked'}


def release(wid, path=None):
    """Give back dates nobody has read. Only a lock that was never evaluated can be released.

    Research was refused on these dates for as long as the lock was live, so releasing teaches
    nothing. Without this, any code change after locking would strand a fresh period for good.
    """
    with registry.session(path) as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT * FROM holdout_windows WHERE id=?', (wid,)).fetchone()
        if not row: raise ValueError('Unknown locked holdout.')
        if row['released']: raise ValueError('This lock was already released.')
        if db.execute('SELECT 1 FROM holdout_ledger WHERE window_id=?', (wid,)).fetchone():
            raise ValueError('This holdout was evaluated. A consumed attempt cannot be released.')
        db.execute('UPDATE holdout_windows SET released=? WHERE id=?', (registry.now(), wid))
        registry.note(db, row['strategy'], 'holdout_released', f'Released {wid}, {row["window_start"]} to {row["window_end"]}, never evaluated. The dates are fresh again.')
        return {'id': wid, 'strategy': row['strategy'], 'status': 'released'}


def ready(db, wid):
    """Every reason an evaluation may not start. Raises without consuming anything."""
    found = db.execute('SELECT * FROM holdout_windows WHERE id=?', (wid,)).fetchone()
    if not found: raise ValueError('Unknown locked holdout.')
    row = dict(found); key = row['strategy']
    if row['released']: raise ValueError('This lock was released. Lock fresh dates to evaluate.')
    if registry.require(db, key)['status'] == 'archived': raise ValueError('An archived strategy cannot consume a holdout.')
    if db.execute('SELECT 1 FROM holdout_ledger WHERE window_id=? OR strategy_version=?', (wid, row['strategy_version'])).fetchone():
        raise ValueError('Holdout attempt already consumed, including failed or interrupted attempts.')
    if row['engine_sha256'] != registry.engine_fingerprint():
        raise ValueError('Code changed after locking; this version cannot be evaluated. Release this lock, rerun the walk-forward, and lock again.')
    g = registry.gates(db, key)
    if g['evidence_run'] != row['evidence_run']: raise ValueError('Evidence changed after locking; this holdout cannot be evaluated. Release this lock and lock again.')
    blocking = [x['id'] for x in g['gates'] if x['id'] != 'final_holdout' and x['status'] != 'pass']
    if blocking: raise ValueError('Holdout remains locked. Required gates: ' + ', '.join(blocking))
    return row, Config.model_validate_json(row['config'])


def preflight(wid, path=None):
    """Refuse, without consuming the attempt, while the window's data is not fully cached.

    A one-shot test must not be spent on a missing file. Loading validates coverage only; it shows
    no price or result, and the message is load_pair's own description of what is missing.
    """
    with registry.session(path) as db: row, c = ready(db, wid)
    try:
        for pair in c.pairs: load_pair(pair, c)
    except ValueError as e:
        raise ValueError(f'Holdout data is not ready. {e} Nothing was consumed. Fetch the window (holdout fetch) or import it, then evaluate again.') from None
    return row, c


def fetch(wid, path=None, progress=lambda x: None, cancel=lambda: False):
    """Download the locked window's candles and funding, warmup included. Fetching is not research access."""
    from .data import download_archive, download_ccxt
    with registry.session(path) as db:
        row = db.execute('SELECT * FROM holdout_windows WHERE id=?', (wid,)).fetchone()
        if not row or row['released']: raise ValueError('Unknown or released holdout lock.')
    c = Config.model_validate_json(row['config'])
    if c.source == 'csv': raise ValueError('This strategy uses imported CSV data. Import the window, with its warmup, before evaluating.')
    begin = (datetime.combine(c.start, datetime.min.time()) - timedelta(days=warmup_days(c))).date()
    for pair in c.pairs:
        if c.source == 'binance_archive': download_archive(pair, begin, c.end, progress, cancel)
        else: download_ccxt(pair, c.source, begin, c.end, progress, cancel)
    return {'id': wid, 'status': 'fetched', 'pairs': c.pairs, 'start': str(begin), 'end': str(c.end)}


def claim(wid, path=None):
    """The transaction commits before data access, so crashes and concurrent callers cannot retry."""
    with registry.session(path) as db:
        db.execute('BEGIN IMMEDIATE')
        row, c = ready(db, wid); key = row['strategy']
        rid = uuid.uuid4().hex; created = registry.now()
        db.execute('INSERT INTO holdout_ledger (strategy,engine_sha256,window_start,window_end,run_id,passed,created,strategy_version,window_id,status,evidence_run) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                   (key, row['engine_sha256'], row['window_start'], row['window_end'], rid, 0, created, row['strategy_version'], wid, 'running', row['evidence_run']))
        registry.record_run(db, {'id': rid, 'kind': 'holdout', 'created': created, 'config': c.model_dump(mode='json'), 'engine_sha256': row['engine_sha256']})
        registry.note(db, key, 'holdout_consumed', f'Attempt {wid} permanently consumed before evaluation. An interruption counts as failure.')
        return row, c


def evaluate(wid, path=None, cancel=lambda: False):
    if cancel(): raise ValueError('Holdout cancelled before an attempt was consumed.')
    preflight(wid, path)
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
