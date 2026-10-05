"""Research memory: every idea and every trial is recorded, including the ones that failed.

Forgetting failures is how selection bias creeps back in, so nothing here deletes.
Archived strategies keep their trials, and those trials still count toward
"best of N" for the whole lineage.
"""
import gzip, hashlib, json, re, sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from .config import Config, STRATEGIES
from .verdict import COSTS

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT/'workspace'/'lab.sqlite3'
ENGINE_FILES = ['engine.py', 'strategies.py', 'config.py', 'data.py', 'experiments.py', 'pine.py']

STATUSES = ['draft', 'candidate', 'promoted', 'archived']
ORIGINS = ['python', 'pine', 'ai']
ARCHIVE_REASONS = {
    'too_few_trades': 'Not enough held-out trades to measure anything.',
    'cost_fragile': 'The edge does not survive realistic or doubled execution costs.',
    'overfit': 'Worked in training, failed on dates it had not seen.',
    'unstable_params': 'Only a narrow parameter setting works; neighbours fail.',
    'failed_holdout': 'Failed the locked final holdout.',
    'superseded': 'Replaced by a better variant in the same lineage.',
}
HELD_OUT_KINDS = ('walkforward', 'grid')
HELD_OUT_TRADES = 100   # Matches verdict.STABLE: below this the estimate still moves a lot.
COST_MULTIPLE = 2       # Must still be profitable with execution costs doubled.

SCHEMA = '''
CREATE TABLE IF NOT EXISTS strategies (key TEXT PRIMARY KEY, name TEXT NOT NULL, parent TEXT, origin TEXT NOT NULL,
  hypothesis TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'draft', archive_reason TEXT, created TEXT NOT NULL, updated TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS studies (id TEXT PRIMARY KEY, strategy TEXT NOT NULL, kind TEXT NOT NULL, created TEXT NOT NULL,
  engine_sha256 TEXT, held_out INTEGER NOT NULL, config TEXT NOT NULL, metrics TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS trials (study TEXT NOT NULL, fingerprint TEXT NOT NULL, strategy TEXT NOT NULL, role TEXT NOT NULL,
  parameters TEXT NOT NULL, metrics TEXT NOT NULL, created TEXT NOT NULL, PRIMARY KEY (study, fingerprint));
CREATE TABLE IF NOT EXISTS holdout_ledger (id INTEGER PRIMARY KEY, strategy TEXT NOT NULL, engine_sha256 TEXT NOT NULL,
  window_start TEXT NOT NULL, window_end TEXT NOT NULL, run_id TEXT, passed INTEGER NOT NULL, created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS journal (id INTEGER PRIMARY KEY, strategy TEXT NOT NULL, created TEXT NOT NULL, kind TEXT NOT NULL, detail TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS trials_strategy ON trials (strategy);
CREATE INDEX IF NOT EXISTS studies_strategy ON studies (strategy, created);
CREATE TABLE IF NOT EXISTS strategy_versions (id TEXT PRIMARY KEY, strategy TEXT NOT NULL,
  engine_sha256 TEXT NOT NULL, created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS holdout_windows (id TEXT PRIMARY KEY, strategy TEXT NOT NULL, strategy_version TEXT NOT NULL,
  engine_sha256 TEXT NOT NULL, evidence_run TEXT NOT NULL, window_start TEXT NOT NULL, window_end TEXT NOT NULL,
  config TEXT NOT NULL, created TEXT NOT NULL, released TEXT);
CREATE TABLE IF NOT EXISTS research_access (id INTEGER PRIMARY KEY, window_start TEXT NOT NULL, window_end TEXT NOT NULL,
  UNIQUE (window_start, window_end));
'''
SCHEMA_VERSION = 2   # Bump when connect() must migrate again; an up-to-date file is opened without a write lock.


def now(): return datetime.now(timezone.utc).isoformat()


def connect(path=None):
    path = Path(path or DB); path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path); db.row_factory = sqlite3.Row; db.executescript(SCHEMA)
    if db.execute('PRAGMA user_version').fetchone()[0] >= SCHEMA_VERSION: return db
    # Old evidence keeps its original digest; migrations must not bless it as current code.
    with db:
        db.execute('BEGIN IMMEDIATE')
        for table, columns in {'studies': {'strategy_version': 'TEXT'}, 'trials': {'strategy_version': 'TEXT'}, 'holdout_windows': {'released': 'TEXT'},
                               'holdout_ledger': {'strategy_version': 'TEXT', 'window_id': 'TEXT', 'status': "TEXT NOT NULL DEFAULT 'legacy'", 'evidence_run': 'TEXT'}}.items():
            existing = {r['name'] for r in db.execute(f'PRAGMA table_info({table})')}
            for column, declaration in columns.items():
                if column not in existing: db.execute(f'ALTER TABLE {table} ADD COLUMN {column} {declaration}')
        for r in db.execute('SELECT id,strategy,engine_sha256 FROM studies WHERE strategy_version IS NULL').fetchall():
            v = ensure_version(db, r['strategy'], r['engine_sha256'])
            db.execute('UPDATE studies SET strategy_version=? WHERE id=?', (v, r['id']))
        for r in db.execute('SELECT t.rowid,t.strategy,s.engine_sha256 FROM trials t JOIN studies s ON s.id=t.study WHERE t.strategy_version IS NULL').fetchall():
            db.execute('UPDATE trials SET strategy_version=? WHERE rowid=?', (ensure_version(db, r['strategy'], r['engine_sha256']), r['rowid']))
        db.execute('CREATE UNIQUE INDEX IF NOT EXISTS holdout_once ON holdout_ledger(strategy_version) WHERE strategy_version IS NOT NULL')
        db.execute('CREATE UNIQUE INDEX IF NOT EXISTS holdout_window_once ON holdout_ledger(window_id) WHERE window_id IS NOT NULL')
        # One live lock per strategy version; a released lock no longer counts.
        db.execute('CREATE UNIQUE INDEX IF NOT EXISTS holdout_window_live ON holdout_windows(strategy_version) WHERE released IS NULL')
        db.execute(f'PRAGMA user_version={SCHEMA_VERSION}')
    return db


@contextmanager
def session(path=None):
    """Commit on success, roll back on error, and always release the file (Windows keeps open handles locked)."""
    db = connect(path)
    try:
        with db: yield db
    finally: db.close()


def engine_fingerprint(root=None):
    """Hash of the code that decides results. Identical to the digest stored on every saved run."""
    root = Path(root or ROOT)
    source = b''.join((root/'backend'/x).read_bytes() for x in ENGINE_FILES)
    if (root/'custom_strategies.py').exists(): source += (root/'custom_strategies.py').read_bytes()
    return hashlib.sha256(source).hexdigest()


def strategy_version(key, engine):
    """Conservative per-strategy identity bound to the byte-exact engine, including custom code."""
    return hashlib.sha256(json.dumps([key, engine or 'unknown'], separators=(',', ':')).encode()).hexdigest()


def ensure_version(db, key, engine):
    version = strategy_version(key, engine)
    db.execute('INSERT OR IGNORE INTO strategy_versions VALUES (?,?,?,?)', (version, key, engine or 'unknown', now()))
    return version


def fingerprint(config, parameters=None):
    """One configuration is one trial however many times it is rerun."""
    merged = {**config, **(parameters or {})}
    # Validation normalises types, so a grid's stop_atr=1 and a saved 1.0 are the same trial.
    try: merged = Config.model_validate(merged).model_dump(mode='json')
    except ValueError: pass
    return hashlib.sha256(json.dumps(merged, sort_keys=True, default=str).encode()).hexdigest()


def summary(m):
    keys = ['trade_count', 'net_pnl', 'net_return_pct', 'max_drawdown_pct', 'expectancy_r', 'profit_factor', 'liquidations']
    return {k: m.get(k) for k in keys}


def trials_of(result):
    """(strategy, parameters, role, metrics) for every configuration a run evaluated."""
    c = result['config']; kind = result['kind']
    if kind == 'holdout':
        yield c['strategy'], {}, 'holdout', {}
    elif kind in ('compare', 'stress'):
        rows = result.get('rows') or []
        if kind == 'stress':
            # Cost multipliers are assumptions about one configuration, not rival candidates.
            if rows: yield c['strategy'], rows[0]['settings'], 'stress', {'net_pnl_by_multiplier': {str(x['settings']['stress_multiplier']): x['metrics']['net_pnl'] for x in rows}, **summary(rows[0]['metrics'])}
        else:
            for x in rows: yield x['settings'].get('strategy', c['strategy']), x['settings'], 'candidate', summary(x['metrics'])
    elif result.get('folds'):
        seen = {}
        for f in result['folds']:
            for x in f['training_ranking']:
                key = json.dumps(x['parameters'], sort_keys=True)
                entry = seen.setdefault(key, {'parameters': x['parameters'], 'training': [], 'selected_in_folds': []})
                entry['training'].append({'fold': f['fold'], 'score': x['score'], 'eligible': x['eligible'], **summary(x['metrics'])})
                if f.get('selected') is not None and x['parameters'] == f['selected']: entry['selected_in_folds'].append(f['fold'])
        for entry in seen.values():
            yield {**c, **entry['parameters']}['strategy'], entry['parameters'], 'candidate', {'training': entry['training'], 'selected_in_folds': entry['selected_in_folds']}
    elif result.get('metrics'):
        yield c['strategy'], {}, 'single', summary(result['metrics'])


def get(db, key):
    row = db.execute('SELECT * FROM strategies WHERE key=?', (key,)).fetchone()
    return dict(row) if row else None


def note(db, key, kind, detail):
    db.execute('INSERT INTO journal (strategy,created,kind,detail) VALUES (?,?,?,?)', (key, now(), kind, detail))


def ensure(db, key):
    """A trial must never be dropped because nobody registered its strategy first."""
    if get(db, key): return
    t = now(); name = STRATEGIES.get(key, {}).get('name', key.replace('_', ' ').capitalize())
    db.execute('INSERT INTO strategies (key,name,origin,created,updated) VALUES (?,?,?,?,?)', (key, name, 'python', t, t))
    note(db, key, 'registered', 'Registered automatically when its first run was saved. No hypothesis recorded yet.')


def family(db, key):
    """Every strategy sharing a root with `key`: the set that one idea's trials are counted across."""
    parents = {r['key']: r['parent'] for r in db.execute('SELECT key,parent FROM strategies')}
    root = key; seen = {root}
    while parents.get(root) and parents[root] not in seen: root = parents[root]; seen.add(root)
    members = [root]
    for k in members:
        members.extend(c for c, p in parents.items() if p == k and c not in members)
    return members


def add_strategy(db, key, name=None, hypothesis='', parent=None, origin='python'):
    if not re.fullmatch(r'[a-z][a-z0-9_]{1,39}', key): raise ValueError('Strategy key must be 2-40 characters: lowercase letters, digits, underscores.')
    if origin not in ORIGINS: raise ValueError(f'Origin must be one of: {", ".join(ORIGINS)}.')
    if get(db, key): raise ValueError(f'Strategy {key} is already registered. Use set-hypothesis or note to update it.')
    if parent and not get(db, parent): raise ValueError(f'Parent {parent} is not registered.')
    t = now(); name = name or STRATEGIES.get(key, {}).get('name', key.replace('_', ' ').capitalize())
    db.execute('INSERT INTO strategies (key,name,parent,origin,hypothesis,created,updated) VALUES (?,?,?,?,?,?,?)', (key, name, parent, origin, hypothesis.strip(), t, t))
    note(db, key, 'registered', f'Registered ({origin}{", child of " + parent if parent else ""}). Hypothesis: {hypothesis.strip() or "none recorded"}')
    return get(db, key)


def set_hypothesis(db, key, hypothesis):
    require(db, key)
    db.execute('UPDATE strategies SET hypothesis=?, updated=? WHERE key=?', (hypothesis.strip(), now(), key))
    note(db, key, 'hypothesis', hypothesis.strip())
    return get(db, key)


def require(db, key):
    row = get(db, key)
    if not row: raise ValueError(f'Strategy {key} is not registered.')
    return row


def record_run(db, result):
    """Record one saved run as a study and its trials. Idempotent on run id; returns trials written."""
    rid = result['id']
    if db.execute('SELECT 1 FROM studies WHERE id=?', (rid,)).fetchone(): return 0
    c = result['config']; kind = result['kind']; created = result.get('created') or now(); written = 0
    ensure(db, c['strategy'])
    version = ensure_version(db, c['strategy'], result.get('engine_sha256'))
    db.execute('INSERT INTO studies (id,strategy,kind,created,engine_sha256,held_out,config,metrics,strategy_version) VALUES (?,?,?,?,?,?,?,?,?)',
               (rid, c['strategy'], kind, created, result.get('engine_sha256'), int(kind in HELD_OUT_KINDS), json.dumps(c),
                json.dumps({} if kind == 'holdout' else result.get('metrics') or {}), version))
    for strategy, parameters, role, metrics in trials_of(result):
        ensure(db, strategy)
        db.execute('INSERT OR IGNORE INTO trials (study,fingerprint,strategy,role,parameters,metrics,created,strategy_version) VALUES (?,?,?,?,?,?,?,?)',
                   (rid, fingerprint(c, parameters), strategy, role, json.dumps(parameters), json.dumps(metrics), created,
                    ensure_version(db, strategy, result.get('engine_sha256'))))
        written += 1
    return written


def sync(db, results_dir):
    """Backfill studies and trials from saved run files. Safe to repeat."""
    added = skipped = 0
    for path in sorted(Path(results_dir).glob('*.json.gz')):
        try:
            with gzip.open(path, 'rt') as f: result = json.load(f)
            before = db.execute('SELECT 1 FROM studies WHERE id=?', (result['id'],)).fetchone()
            record_run(db, result); added += not before
        except (OSError, ValueError, KeyError): skipped += 1
    return {'added': added, 'unreadable': skipped}


def count_trials(db, keys, study=None):
    """Distinct configurations tried: within one run if `study` is given, else across the listed strategies."""
    if study: return db.execute('SELECT COUNT(*) FROM trials WHERE study=?', (study,)).fetchone()[0]
    marks = ','.join('?' * len(keys))
    return db.execute(f"SELECT COUNT(DISTINCT COALESCE(strategy_version,'unknown') || ':' || fingerprint) FROM trials WHERE strategy IN ({marks})", keys).fetchone()[0]


def evidence(db, key):
    """The held-out study a status decision rests on: the latest one, never the best one.

    There is deliberately no way to name a run. Choosing which walk-forward counts is selection.
    """
    row = db.execute('SELECT * FROM studies WHERE strategy=? AND held_out=1 ORDER BY created DESC LIMIT 1', (key,)).fetchone()
    if not row: return None
    return {**dict(row), 'metrics': json.loads(row['metrics']), 'config': json.loads(row['config'])}


def gates(db, key, current_engine=None):
    """Objective checks for moving up a status. Each is pass, fail, or pending (cannot be evaluated yet)."""
    require(db, key); e = evidence(db, key); out = []
    def gate(gid, needed_for, status, detail, value=None, threshold=None):
        out.append({'id': gid, 'needed_for': needed_for, 'status': status, 'detail': detail, 'value': value, 'threshold': threshold})
    if not e or not e['held_out'] or not e['metrics']:
        gate('held_out_evidence', 'candidate', 'fail', 'No walk-forward or grid run is recorded for this strategy. In-sample results are not evidence.')
    else:
        m = e['metrics']; n = m['trade_count']; total = sum(m[k] for k in COSTS); stressed = m['raw_pnl'] - COST_MULTIPLE * total
        gate('held_out_trades', 'candidate', 'pass' if n >= HELD_OUT_TRADES else 'fail', f'{n} held-out trades in run {e["id"]}.', n, HELD_OUT_TRADES)
        gate('profitable_after_costs', 'candidate', 'pass' if m['net_pnl'] > 0 else 'fail', f'Net {m["net_pnl"]:,.2f} USDT after all modeled costs.', m['net_pnl'], 0)
        gate('survives_doubled_costs', 'candidate', 'pass' if stressed > 0 else 'fail',
             f'Estimated net {stressed:,.2f} USDT with every execution cost doubled and fills unchanged. A linear estimate, not a rerun.', stressed, 0)
        gate('no_liquidations', 'candidate', 'pass' if not m['liquidations'] else 'fail', f'{m["liquidations"]} liquidations.', m['liquidations'], 0)
        current = current_engine or engine_fingerprint()
        gate('engine_unchanged', 'candidate', 'pass' if e['engine_sha256'] == current else 'fail',
             'Evidence was produced by the current engine code.' if e['engine_sha256'] == current else 'The engine code changed after this evidence was produced; rerun the walk-forward.')
    gate('overfitting_checks', 'promoted', 'pending', 'Deflated Sharpe ratio and probability of backtest overfitting are not implemented yet (plan phase 2).')
    used = db.execute('SELECT passed FROM holdout_ledger WHERE strategy=? AND strategy_version=? AND evidence_run=? ORDER BY id DESC LIMIT 1',
                      (key, strategy_version(key, current_engine or engine_fingerprint()), e['id'] if e else None)).fetchone()
    if used is None: gate('final_holdout', 'promoted', 'pending', 'No final holdout has been evaluated for this strategy version and this evidence run.')
    else: gate('final_holdout', 'promoted', 'pass' if used['passed'] else 'fail', 'Locked final holdout ' + ('passed.' if used['passed'] else 'failed.'))
    return {'strategy': key, 'evidence_run': e['id'] if e else None, 'gates': out,
            'candidate_ready': all(g['status'] == 'pass' for g in out if g['needed_for'] == 'candidate'),
            'promotion_ready': all(g['status'] == 'pass' for g in out)}


def promote(db, key, current_engine=None):
    """Move one step up if the gates for that step pass. Never skips a step, never overrides a gate."""
    row = require(db, key); g = gates(db, key, current_engine)
    if row['status'] == 'archived': raise ValueError(f'{key} is archived ({row["archive_reason"]}). Reopen it first.')
    if row['status'] == 'promoted': raise ValueError(f'{key} is already promoted.')
    target = 'candidate' if row['status'] == 'draft' else 'promoted'
    blocking = [x for x in g['gates'] if x['status'] != 'pass' and (target == 'promoted' or x['needed_for'] == 'candidate')]
    if blocking: raise ValueError(f'{key} stays {row["status"]}. Blocked by: ' + '; '.join(f'{x["id"]} ({x["status"]}): {x["detail"]}' for x in blocking))
    db.execute('UPDATE strategies SET status=?, updated=? WHERE key=?', (target, now(), key))
    note(db, key, 'status', f'{row["status"]} -> {target} on run {g["evidence_run"]}. Gates passed: ' + ', '.join(x['id'] for x in g['gates'] if x['status'] == 'pass'))
    return get(db, key)


def archive(db, key, reason, detail=''):
    row = require(db, key)
    if reason not in ARCHIVE_REASONS: raise ValueError(f'Archive reason must be one of: {", ".join(ARCHIVE_REASONS)}.')
    if row['status'] == 'archived': raise ValueError(f'{key} is already archived ({row["archive_reason"]}).')
    db.execute('UPDATE strategies SET status=?, archive_reason=?, updated=? WHERE key=?', ('archived', reason, now(), key))
    note(db, key, 'status', f'{row["status"]} -> archived ({reason}). {detail.strip()}'.strip())
    return get(db, key)


def reopen(db, key, detail=''):
    row = require(db, key)
    if row['status'] != 'archived': raise ValueError(f'{key} is not archived.')
    db.execute("UPDATE strategies SET status='draft', archive_reason=NULL, updated=? WHERE key=?", (now(), key))
    note(db, key, 'status', f'archived ({row["archive_reason"]}) -> draft. {detail.strip()}'.strip())
    return get(db, key)


def library(db, status=None):
    """Every registered idea with its trial counts and latest held-out result."""
    out = []
    for row in db.execute('SELECT * FROM strategies ORDER BY created'):
        if status and row['status'] != status: continue
        key = row['key']; e = evidence(db, key)
        last = db.execute('SELECT id,kind,created FROM studies WHERE strategy=? ORDER BY created DESC LIMIT 1', (key,)).fetchone()
        m = e['metrics'] if e else None
        out.append({**dict(row), 'trials': count_trials(db, [key]), 'lineage_trials': count_trials(db, family(db, key)),
                    'studies': db.execute('SELECT COUNT(*) FROM studies WHERE strategy=?', (key,)).fetchone()[0],
                    'last_run': dict(last) if last else None,
                    'held_out': {'run': e['id'], 'created': e['created'], 'trades': m['trade_count'], 'net_return_pct': m['net_return_pct'],
                                 'max_drawdown_pct': m['max_drawdown_pct'], 'expectancy_r': m['expectancy_r']} if m else None})
    return out


def journal(db, key=None, limit=200):
    where, args = ('WHERE strategy=?', (key,)) if key else ('', ())
    return [dict(r) for r in db.execute(f'SELECT strategy,created,kind,detail FROM journal {where} ORDER BY id DESC LIMIT ?', (*args, limit))]


def studies(db, key, limit=50):
    return [{**dict(r), 'metrics': json.loads(r['metrics'])} for r in
            db.execute('SELECT id,kind,created,held_out,engine_sha256,strategy_version,metrics FROM studies WHERE strategy=? ORDER BY created DESC LIMIT ?', (key, limit))]


def versions(db, key):
    return [dict(r) for r in db.execute('SELECT * FROM strategy_versions WHERE strategy=? ORDER BY created DESC', (key,))]


def context(db, result, current_engine=None):
    """What the registry knows that a single run does not: status, lineage, trial counts, parent, holdout."""
    c = result['config']; key = c['strategy']; row = get(db, key); rid = result.get('id')
    lineage = family(db, key) if row else [key]
    study_trials = count_trials(db, [key], study=rid) if rid else 0
    lineage_trials = count_trials(db, lineage)
    out = {'strategy': {k: row[k] for k in ['key', 'name', 'status', 'origin', 'parent', 'hypothesis', 'archive_reason']} if row else None,
           'selection': {'study_trials': study_trials, 'strategy_trials': count_trials(db, [key]), 'lineage_trials': lineage_trials,
                         'lineage': lineage, 'label': f'best of {max(lineage_trials, 1)}'},
           'parent': None, 'holdout': holdout_status(db, key, current_engine),
           'strategy_version': strategy_version(key, result.get('engine_sha256')),
           'engine_current': (result['engine_sha256'] == (current_engine or engine_fingerprint())) if result.get('engine_sha256') else None}
    if row and row['parent'] and result.get('metrics'):
        p = db.execute('SELECT * FROM studies WHERE strategy=? AND kind=? ORDER BY created DESC LIMIT 1', (row['parent'], result['kind'])).fetchone()
        if p:
            pm = json.loads(p['metrics']); pc = json.loads(p['config']); m = result['metrics']
            delta = {k: (m[k] - pm[k]) if m.get(k) is not None and pm.get(k) is not None else None for k in ['net_return_pct', 'max_drawdown_pct', 'expectancy_r', 'trade_count']}
            out['parent'] = {'key': row['parent'], 'run': p['id'], 'delta': {k: round(v, 3) if v is not None else None for k, v in delta.items()},
                             'comparable': all(pc.get(k) == c.get(k) for k in ['pairs', 'start', 'end', 'timeframe'])}
    return out


def holdout_status(db, key, current_engine=None):
    keys = family(db, key); marks = ','.join('?' * len(keys))
    uses = db.execute(f'SELECT COUNT(*) FROM holdout_ledger WHERE strategy IN ({marks})', keys).fetchone()[0]
    version = strategy_version(key, current_engine or engine_fingerprint())
    row = db.execute('SELECT passed FROM holdout_ledger WHERE strategy_version=?', (version,)).fetchone()
    locked = db.execute('SELECT 1 FROM holdout_windows WHERE strategy_version=? AND released IS NULL', (version,)).fetchone()
    return {'status': ('passed' if row['passed'] else 'failed') if row else 'locked' if locked else 'not_configured', 'uses': uses}


def holdout_windows(db, key):
    """Every reservation for a strategy, newest first: dates and state only, never a result beyond pass or fail."""
    out = []
    for w in db.execute('SELECT id,strategy_version,window_start,window_end,created,released FROM holdout_windows WHERE strategy=? ORDER BY created DESC', (key,)):
        used = db.execute('SELECT status FROM holdout_ledger WHERE window_id=?', (w['id'],)).fetchone()
        out.append({'id': w['id'], 'start': w['window_start'], 'end': w['window_end'], 'created': w['created'], 'strategy_version': w['strategy_version'],
                    'state': used['status'] if used else 'released' if w['released'] else 'locked'})
    return out
