"""Command line for research memory and run cards: python -m backend.lab --help

Reads the same workspace the app writes. Nothing here changes how a backtest is
computed; `run` calls the exact engine and saves through the server's own path.
"""
import argparse, gzip, json, sqlite3, sys
from pathlib import Path
from . import registry, runcard, holdout
from .config import Config

WORKSPACE = registry.ROOT/'workspace'


def db_path(): return WORKSPACE/'lab.sqlite3'
def runs_dir(): return WORKSPACE/'runs'


def run_ids():
    """Newest first, from the files themselves so a run is found even if its summary row is missing."""
    return [p.name[:-8] for p in sorted(runs_dir().glob('*.json.gz'), key=lambda p: p.stat().st_mtime, reverse=True)]


def resolve(rid):
    ids = run_ids()
    if not ids: raise ValueError('No saved runs yet. Run a backtest in the app, or use: python -m backend.lab run --preset sample-jto')
    if rid == 'latest': return ids[0]
    matches = [x for x in ids if x.startswith(rid)]
    if len(matches) != 1: raise ValueError(f'Run "{rid}" matches {len(matches)} saved runs; give more of the ID.')
    return matches[0]


def load(rid):
    with gzip.open(runs_dir()/f'{resolve(rid)}.json.gz', 'rt') as f: return json.load(f)


def build_card(result):
    with registry.session(db_path()) as db:
        registry.record_run(db, result)   # Runs saved before the registry existed are picked up here.
        context = registry.context(db, result)
    return runcard.card(result, context, defaults=Config().model_dump(mode='json'))


def show(value):
    print(json.dumps(value, indent=2, default=str))


def table(rows, columns):
    if not rows: print('(none)'); return
    cells = [[str(r.get(c, '') if r.get(c) is not None else '—') for c in columns] for r in rows]
    widths = [max(len(c), *(len(x[i]) for x in cells)) for i, c in enumerate(columns)]
    line = lambda xs: '  '.join(x.ljust(w) for x, w in zip(xs, widths)).rstrip()
    print(line(columns)); print(line(['-' * w for w in widths]))
    for x in cells: print(line(x))


def cmd_runs(a):
    rows = []
    for rid in run_ids()[:a.limit]:
        r = load(rid); m = r.get('metrics') or {}
        rows.append({'id': rid, 'created': r['created'][:16].replace('T', ' '), 'kind': r['kind'], 'strategy': r['config']['strategy'],
                     'pairs': ','.join(r['config']['pairs']), 'trades': m.get('trade_count'),
                     'net_pct': None if m.get('net_return_pct') is None else f'{m["net_return_pct"]:.2f}'})
    show(rows) if a.json else table(rows, ['id', 'created', 'kind', 'strategy', 'pairs', 'trades', 'net_pct'])


def cmd_card(a): show(build_card(load(a.run)))
def cmd_digest(a): print(runcard.digest(build_card(load(a.run))), end='')


def cmd_run(a):
    from . import server   # Loads custom strategies and owns persistence; imported only when a run is requested.
    from .engine import run_backtest
    from .experiments import experiment
    if bool(a.preset) == bool(a.config): raise ValueError('Give exactly one of --preset NAME or --config FILE.')
    path = Path(a.config) if a.config else registry.ROOT/'presets'/f'{a.preset}.json'
    if not path.exists(): raise ValueError(f'{path} not found. Presets: ' + ', '.join(p.stem for p in sorted((registry.ROOT/"presets").glob("*.json"))))
    settings = json.loads(path.read_text())
    for item in a.set or []:
        key, _, value = item.partition('=')
        try: settings[key] = json.loads(value)
        except ValueError: settings[key] = value
    c = Config.model_validate(settings); progress = lambda message: print(message, file=sys.stderr)
    # Inline JSON, or a file holding it: shells disagree about quoting braces and quotes.
    grid = None if not a.grid else json.loads(a.grid) if a.grid.lstrip().startswith('{') else json.loads(Path(a.grid).read_text())
    holdout.research_access(c, db_path())
    result = run_backtest(c, progress) if a.mode == 'backtest' else experiment(c, a.mode, grid, a.folds, a.min_trades, progress)
    server.persist_result(result, a.mode)
    k = build_card(result)
    show(k) if a.json else print(runcard.digest(k), end='')


def cmd_library(a):
    with registry.session(db_path()) as db: rows = registry.library(db, a.status)
    if a.json: return show(rows)
    for r in rows:
        h = r['held_out']
        r['held-out'] = f'{h["net_return_pct"]:+.2f}% / {h["trades"]} trades' if h else None
        r['why'] = r['archive_reason']
    table(rows, ['key', 'status', 'origin', 'parent', 'trials', 'lineage_trials', 'held-out', 'why'])


def print_gates(g):
    print(f'{g["strategy"]}: evidence run {g["evidence_run"] or "none"}')
    for x in g['gates']: print(f'  [{x["status"]:7}] {x["id"]} (for {x["needed_for"]}): {x["detail"]}')
    print(f'  candidate ready: {"yes" if g["candidate_ready"] else "no"} · promotion ready: {"yes" if g["promotion_ready"] else "no"}')


def cmd_gates(a):
    with registry.session(db_path()) as db: g = registry.gates(db, a.key, a.run and resolve(a.run))
    show(g) if a.json else print_gates(g)


def cmd_strategy(a):
    with registry.session(db_path()) as db:
        if a.action == 'add': row = registry.add_strategy(db, a.key, a.name, a.hypothesis or '', a.parent, a.origin)
        elif a.action == 'hypothesis':
            if not a.hypothesis: raise ValueError('Give the hypothesis text with --hypothesis.')
            row = registry.set_hypothesis(db, a.key, a.hypothesis)
        else: row = registry.require(db, a.key)
        detail = {**row, 'gates': registry.gates(db, a.key), 'versions': registry.versions(db, a.key),
                  'holdout': registry.holdout_status(db, a.key), 'studies': registry.studies(db, a.key, 10), 'journal': registry.journal(db, a.key, 20)}
    if a.json: return show(detail)
    print(f'{row["key"]} · {row["name"]} · {row["status"]}' + (f' ({row["archive_reason"]})' if row['archive_reason'] else '') + f' · {row["origin"]}' + (f' · child of {row["parent"]}' if row['parent'] else ''))
    print(f'Hypothesis: {row["hypothesis"] or "none recorded"}'); print_gates(detail['gates'])
    print('Recent runs:'); table([{'id': s['id'], 'kind': s['kind'], 'created': s['created'][:16], 'trades': s['metrics'].get('trade_count'), 'net_pct': s['metrics'].get('net_return_pct')} for s in detail['studies']], ['id', 'kind', 'created', 'trades', 'net_pct'])


def cmd_promote(a):
    with registry.session(db_path()) as db: row = registry.promote(db, a.key, a.run and resolve(a.run))
    print(f'{a.key} is now {row["status"]}.')


def cmd_archive(a):
    with registry.session(db_path()) as db: registry.archive(db, a.key, a.reason, a.note or '')
    print(f'{a.key} archived ({a.reason}). Its trials are kept and still count.')


def cmd_reopen(a):
    with registry.session(db_path()) as db: registry.reopen(db, a.key, a.note or '')
    print(f'{a.key} is a draft again.')


def cmd_note(a):
    with registry.session(db_path()) as db: registry.require(db, a.key); registry.note(db, a.key, 'note', a.text)
    print('Noted.')


def cmd_journal(a):
    with registry.session(db_path()) as db:
        entries = registry.journal(db, a.key); rows = registry.library(db)
    if a.json: return show({'strategies': rows, 'entries': entries})
    print('# Research journal\n\nRead this before proposing anything: archived ideas are not to be retried without a new hypothesis.\n')
    for r in rows:
        if a.key and r['key'] != a.key: continue
        why = f' ({r["archive_reason"]}: {registry.ARCHIVE_REASONS[r["archive_reason"]]})' if r['archive_reason'] else ''
        h = r['held_out']
        print(f'## {r["key"]} · {r["status"]}{why}')
        print(f'- Hypothesis: {r["hypothesis"] or "none recorded"}')
        print(f'- Origin {r["origin"]}' + (f', child of {r["parent"]}' if r['parent'] else '') + f'; {r["trials"]} configurations tried, {r["lineage_trials"]} across the lineage')
        print('- Latest held-out result: ' + (f'{h["net_return_pct"]:+.2f}% over {h["trades"]} trades, {h["max_drawdown_pct"]:.2f}% drawdown (run {h["run"]})' if h else 'none'))
        for e in reversed([e for e in entries if e['strategy'] == r['key']]): print(f'- {e["created"][:10]} {e["kind"]}: {e["detail"]}')
        print()


def cmd_sync(a):
    with registry.session(db_path()) as db: out = registry.sync(db, runs_dir())
    print(f'{out["added"]} runs added to the registry, {out["unreadable"]} unreadable.')


def cmd_check(a):
    from . import server   # Loads custom_strategies.py exactly as the app does.
    from .causality import prefix_invariant
    out = prefix_invariant(a.key)
    if a.json: show(out)
    elif not out['conclusive']: print(f'{a.key}: inconclusive. The strategy produced no signals on the synthetic data, so nothing was compared.')
    elif out['ok']: print(f'{a.key}: no look-ahead found across {out["signals_checked"]} signal comparisons.')
    else: print(f'{a.key}: LOOK-AHEAD. {len(out["mismatches"])} signals changed once later bars existed. First: {out["mismatches"][0]}')
    if not (out['ok'] and out['conclusive']): raise SystemExit(1)


def cmd_holdout(a):
    if a.action == 'lock':
        if not a.start or not a.end: raise ValueError('Lock requires --start and --end dates.')
        show(holdout.lock(a.key, a.start, a.end, db_path(), runs_dir()))
    elif a.action == 'evaluate':
        from . import server   # Register custom strategies before checking the frozen version.
        show(holdout.evaluate(a.key, db_path()))
    else:
        with registry.session(db_path()) as db:
            registry.require(db, a.key); show(registry.holdout_status(db, a.key))


def parser():
    p = argparse.ArgumentParser(prog='python -m backend.lab', description='FibStein Lab research memory and run cards.')
    s = p.add_subparsers(dest='command', required=True)
    def add(name, fn, text):
        x = s.add_parser(name, help=text, description=text); x.set_defaults(fn=fn); return x
    x = add('runs', cmd_runs, 'List saved runs, newest first.'); x.add_argument('--limit', type=int, default=20); x.add_argument('--json', action='store_true')
    x = add('card', cmd_card, 'Print the JSON run card for a run.'); x.add_argument('run', help='Run ID, a unique prefix of one, or "latest".')
    x = add('digest', cmd_digest, 'Print the short Markdown digest for a run.'); x.add_argument('run', help='Run ID, a unique prefix of one, or "latest".')
    x = add('run', cmd_run, 'Run a backtest or experiment with the exact engine, save it, and print its digest.')
    x.add_argument('--preset'); x.add_argument('--config'); x.add_argument('--mode', default='backtest', choices=['backtest', 'compare', 'stress', 'grid', 'walkforward'])
    x.add_argument('--grid', help='JSON such as {"stop_atr":[1,1.5,2]}, or the path of a file containing it.'); x.add_argument('--folds', type=int, default=3); x.add_argument('--min-trades', type=int, default=20)
    x.add_argument('--set', action='append', metavar='KEY=VALUE', help='Override one setting, for example stop_atr=2 or start=2024-10-01; repeatable.'); x.add_argument('--json', action='store_true')
    x = add('library', cmd_library, 'List every registered strategy with trial counts and latest held-out result.')
    x.add_argument('--status', choices=registry.STATUSES); x.add_argument('--json', action='store_true')
    x = add('strategy', cmd_strategy, 'Register a strategy, set its hypothesis, or show it.')
    x.add_argument('action', choices=['add', 'show', 'hypothesis']); x.add_argument('key'); x.add_argument('--name'); x.add_argument('--hypothesis'); x.add_argument('--parent')
    x.add_argument('--origin', default='python', choices=registry.ORIGINS); x.add_argument('--json', action='store_true')
    x = add('gates', cmd_gates, 'Show the objective checks between a strategy and its next status.'); x.add_argument('key'); x.add_argument('--run'); x.add_argument('--json', action='store_true')
    x = add('promote', cmd_promote, 'Move a strategy one status up if its gates pass.'); x.add_argument('key'); x.add_argument('--run')
    x = add('archive', cmd_archive, 'Archive a strategy with a reason. Its trials are kept.'); x.add_argument('key'); x.add_argument('--reason', required=True, choices=list(registry.ARCHIVE_REASONS)); x.add_argument('--note')
    x = add('reopen', cmd_reopen, 'Return an archived strategy to draft.'); x.add_argument('key'); x.add_argument('--note')
    x = add('note', cmd_note, 'Add a journal note to a strategy.'); x.add_argument('key'); x.add_argument('text')
    x = add('journal', cmd_journal, 'Print the research journal: hypotheses, outcomes and status history.'); x.add_argument('key', nargs='?'); x.add_argument('--json', action='store_true')
    add('sync', cmd_sync, 'Record any saved runs the registry has not seen.')
    x = add('check', cmd_check, 'Test a registered strategy for look-ahead.'); x.add_argument('key'); x.add_argument('--json', action='store_true')
    x = add('holdout', cmd_holdout, 'Lock fresh final dates, evaluate once, or show pass/fail and lineage attempts.')
    x.add_argument('action', choices=['lock', 'evaluate', 'show']); x.add_argument('key', help='Strategy key for lock/show; locked window ID for evaluate.')
    x.add_argument('--start'); x.add_argument('--end')
    return p


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'): stream.reconfigure(encoding='utf-8', errors='replace')   # Windows consoles default to cp1252.
    a = parser().parse_args(argv)
    try: a.fn(a)
    except (ValueError, sqlite3.Error, OSError) as e: raise SystemExit(f'Error: {e}')


if __name__ == '__main__': main()
