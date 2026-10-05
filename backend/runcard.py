"""Run card: a compact, machine-readable reading of one saved run, for an AI or a person in a hurry.

Pure, like verdict.py: takes a result dict (plus optional registry context), returns
dicts and text. It never recomputes performance. Every number is copied from the
exact engine's output and rounded for reading; exact values stay in the full export.
"""
from collections import Counter
from .verdict import COSTS, PRELIMINARY, STABLE, THIN, money, read as read_verdict

SCHEMA = 'fibstein.runcard/1'

MIN_GROUP = 10        # Smallest bucket worth a sentence; below this a split is anecdote.
CONCENTRATED = .6     # Share of all losses in one bucket that counts as concentration.
OVERWEIGHT = .15      # ...and it must exceed that bucket's share of trades by this much.
REJECT_HEAVY = .3     # Share of signals refused by risk, margin or order rules.
IGNORED_HEAVY = .5    # Share of signals that arrived while already positioned or pending.
TIME_EXIT_HEAVY = .4
GAVE_BACK = .25       # Share of losers that were at least +1R in open profit first.

SETUP = ['pairs', 'start', 'end', 'source', 'strategy', 'timeframe', 'execution_minutes', 'direction',
         'leverage', 'margin_mode', 'entry_order', 'target_order', 'funding_mode', 'stress_multiplier']
SEVERITY = ['problem', 'caution', 'info']
COST_NAMES = {'fees': 'Fees are', 'slippage': 'Slippage is', 'spread': 'Spread is', 'funding': 'Funding is', 'liquidation_fee': 'Liquidation fees are'}
DASH = '\u2014'


def r(x, d=2):
    return None if x is None else round(float(x), d) + 0.0   # + 0.0 turns the engine's -0.0 into 0.0.


def cash_folds(result):
    """Folds that traded nothing because no training candidate qualified."""
    return sum(f.get('status') == 'no_trade' for f in result.get('folds') or [])


def key_numbers(m):
    """The handful of metrics worth reading first. Missing keys stay None rather than guessed."""
    g = m.get
    return {'trades': g('trade_count'), 'net_pnl': r(g('net_pnl')), 'net_return_pct': r(g('net_return_pct')),
            'max_drawdown_pct': r(g('max_drawdown_pct')), 'win_rate_pct': r(g('win_rate_pct')),
            'profit_factor': r(g('profit_factor'), 3), 'expectancy': r(g('expectancy')),
            'expectancy_r': r(g('expectancy_r'), 3), 'average_hold_hours': r(g('average_hold_hours')),
            'exposure_pct': r(g('exposure_pct')), 'liquidations': g('liquidations')}


def group(trades, key):
    """Same shape as the engine's `groups`, for results (walk-forward) that only carry trades."""
    out = []
    for label in sorted(set(t[key] for t in trades)):
        pnls = [t['net_pnl'] for t in trades if t[key] == label]
        out.append({'label': label, 'trades': len(pnls), 'net_pnl': sum(pnls),
                    'win_rate_pct': 100 * sum(x > 0 for x in pnls) / len(pnls), 'expectancy': sum(pnls) / len(pnls)})
    return out


def breakdown(result):
    trades = result.get('trades') or []
    groups = result.get('groups') or {k: group(trades, k) for k in ['pair', 'side', 'regime', 'reason']}
    return {k: [{'label': x['label'], 'trades': x['trades'], 'net_pnl': r(x['net_pnl']),
                 'win_rate_pct': r(x['win_rate_pct'], 1), 'expectancy': r(x['expectancy'])}
                for x in sorted(v, key=lambda x: x['net_pnl'])] for k, v in groups.items()}


def costs(m):
    if not m or 'raw_pnl' not in m: return None
    parts = {k: m[k] for k in COSTS}; total = sum(parts.values()); raw = m['raw_pnl']
    largest = max(parts, key=lambda k: parts[k]) if total > 0 else None
    return {'raw_pnl': r(raw), **{k: r(v) for k, v in parts.items()}, 'total': r(total), 'net_pnl': r(m['net_pnl']),
            # Headroom: how many times today's costs the pre-cost result could absorb. Linear estimate.
            'breakeven_multiple': r(raw / total, 2) if raw > 0 and total > 0 else None,
            'cost_share_of_raw': r(total / raw, 3) if raw > 0 else None,
            'largest': largest, 'largest_share_of_costs': r(parts[largest] / total, 3) if largest else None}


def signals(result):
    d = result.get('diagnostics')
    if not d: return None
    entered = len(result.get('trades') or []); rejected = dict(d.get('rejected') or {}); refused = sum(rejected.values())
    total = d.get('signals', 0)
    return {'total': total, 'entered': entered, 'rejected': rejected, 'rejected_total': refused,
            # A signal that arrives while positioned, or behind a resting order, is not acted on.
            'ignored_or_pending': max(0, total - entered - refused), 'peak_open_positions': d.get('peak_open_positions')}


def splits(result):
    """Each stretch of dates with its own numbers, marked held out or not."""
    c = result['config']; out = []
    if result.get('folds'):
        for f in result['folds']:
            ranking = f['training_ranking']; eligible = [x for x in ranking if x['eligible']]
            winner = next((x for x in eligible if x['parameters'] == f['selected']), None)
            out.append({'name': f'fold {f["fold"]} training', 'held_out': False, 'start': f['train_start'], 'end': f['train_end'],
                        'selected': f['selected'], 'status': f.get('status', 'selected'), 'no_trade_reason': f.get('no_trade_reason'), 'candidates': len(ranking), 'eligible': len(eligible),
                        # A cash fold still shows how its best-ranked candidate did in training.
                        'best_rejected': not winner and bool(ranking),
                        'metrics': key_numbers((winner or ranking[0])['metrics']) if ranking else None})
            out.append({'name': f'fold {f["fold"]} test', 'held_out': True, 'start': f['test_start'], 'end': f['test_end'],
                        'selected': f['selected'], 'status': f.get('status', 'selected'), 'no_trade_reason': f.get('no_trade_reason'), 'metrics': key_numbers(f['test_metrics'])})
        out.append({'name': 'held-out total', 'held_out': True, 'start': result['folds'][0]['test_start'],
                    'end': result['folds'][-1]['test_end'], 'metrics': key_numbers(result['metrics'])})
    elif result.get('metrics'):
        out.append({'name': 'full range', 'held_out': False, 'start': c['start'], 'end': c['end'],
                    'metrics': key_numbers(result['metrics'])})
    return out


def finding(fid, severity, text, **data):
    return {'id': fid, 'severity': severity, 'text': text, 'data': data}


def pct(x): return f'{x * 100:.0f}%'


def findings(result):
    """Deterministic facts about a run, ordered problem, caution, info. No judgement beyond fixed thresholds."""
    out = []; kind = result.get('kind', 'backtest'); m = result.get('metrics') or {}
    trades = result.get('trades') or []
    held = bool(result.get('folds')); where = 'held-out trades' if held else 'trades'
    n = m.get('trade_count')

    if result.get('rows'):
        rows = result['rows']
        if kind == 'stress':
            ladder = [(x['settings']['stress_multiplier'], x['metrics']['net_pnl']) for x in rows]
            broke = next((mult for mult, net in ladder if net <= 0), None)
            text = (f'Already losing before any stress: {money(ladder[0][1])} at {ladder[0][0]}×.' if broke == ladder[0][0] else
                    f'Net result turns negative at {broke}× spread and slippage.' if broke else
                    f'Still profitable at {ladder[-1][0]}× spread and slippage ({money(ladder[-1][1])}).')
            out.append(finding('cost_stress', 'problem' if broke and broke <= 2 else 'caution' if broke else 'info', text,
                               net_by_multiplier={str(k): r(v) for k, v in ladder}))
            out.append(finding('stress_scope', 'info', 'Stress multiplies spread and slippage only; fees and funding are unchanged, '
                               'and sizing and fills are recomputed, so results need not fall evenly.'))
        else:
            ranked = sorted(rows, key=lambda x: x['metrics']['net_return_pct'], reverse=True)
            label = lambda x: ', '.join(f'{k}={v}' for k, v in x['settings'].items())
            out.append(finding('same_period_ranking', 'caution',
                               f'Best of {len(rows)} on the same dates: {label(ranked[0])} at {ranked[0]["metrics"]["net_return_pct"]:.2f}%; '
                               f'worst {label(ranked[-1])} at {ranked[-1]["metrics"]["net_return_pct"]:.2f}%. In-sample, so the ranking is not evidence.',
                               ranking=[{'settings': x['settings'], **key_numbers(x['metrics'])} for x in ranked]))
        return out

    skipped = [f for f in result.get('folds', []) if f.get('selected') is None]
    if skipped:
        # Profitable in training yet ineligible means the only thing missing was the trade minimum.
        thin = [f['fold'] for f in skipped if any((x['metrics'].get('net_pnl') or 0) > 0 and (x['metrics'].get('expectancy_r') or 0) > 0 for x in f['training_ranking'])]
        text = (f'{len(skipped)} held-out fold{"s" if len(skipped) != 1 else ""} stayed in cash because no training candidate qualified.'
                + (f' In {len(thin)} of them a candidate was profitable in training but had too few trades.' if thin else '')
                + ' All candidates remain counted.')
        out.append(finding('no_trade_folds', 'info', text, folds=[f['fold'] for f in skipped], too_few_trades=thin))
    if n is None: return out
    if n == 0:
        return [finding('no_trades', 'problem', 'No trades were taken, so there is nothing to measure.'), *out]

    if m.get('liquidations'):
        out.append(finding('liquidations', 'problem', f'{m["liquidations"]} of {n} {where} ended in liquidation.', count=m['liquidations']))
    if n < PRELIMINARY:
        out.append(finding('sample_size', 'problem', f'Only {n} {where}: below {PRELIMINARY}, a result this size is ordinary luck.', trades=n))
    elif n < STABLE:
        out.append(finding('sample_size', 'caution', f'{n} {where}: below {STABLE}, estimates still move a lot.', trades=n))

    c = costs(m)
    if c:
        if c['raw_pnl'] <= 0:
            out.append(finding('losing_before_costs', 'problem',
                               f'Lost {money(-c["raw_pnl"])} before any cost; execution costs added {money(c["total"])}. The signal is the problem, not the fills.',
                               raw_pnl=c['raw_pnl'], costs=c['total']))
        else:
            b = c['breakeven_multiple']; share = c['cost_share_of_raw']
            if b is not None:
                severity = 'problem' if c['net_pnl'] <= 0 else 'caution' if b < THIN else 'info'
                out.append(finding('cost_headroom', severity,
                                   f'Costs took {pct(share)} of the pre-cost result ({money(c["total"])} of {money(c["raw_pnl"])}); '
                                   f'the edge survives costs up to {b:.1f}× today’s.', breakeven_multiple=b, cost_share_of_raw=share))
        if c['largest'] and c['largest_share_of_costs'] >= .5:
            out.append(finding('largest_cost', 'info', f'{COST_NAMES[c["largest"]]} {pct(c["largest_share_of_costs"])} of all execution costs.',
                               component=c['largest'], share=c['largest_share_of_costs']))

    groups = result.get('groups') or {k: group(trades, k) for k in ['pair', 'side', 'regime', 'reason']}
    total_loss = -sum(t['net_pnl'] for t in trades if t['net_pnl'] < 0)
    for dimension in ['regime', 'pair']:
        rows = groups.get(dimension) or []
        if len(rows) < 2: continue
        if total_loss > 0:
            for x in rows:
                lost = -sum(t['net_pnl'] for t in trades if t[dimension] == x['label'] and t['net_pnl'] < 0)
                loss_share = lost / total_loss; trade_share = x['trades'] / len(trades)
                if x['trades'] >= MIN_GROUP and x['net_pnl'] < 0 and loss_share >= CONCENTRATED and loss_share >= trade_share + OVERWEIGHT:
                    out.append(finding(f'loss_concentration_{dimension}', 'caution',
                                       f'{pct(loss_share)} of losses came in {dimension} {x["label"]}, which holds {pct(trade_share)} of trades '
                                       f'({x["trades"]} trades, net {money(x["net_pnl"])}).',
                                       label=x['label'], loss_share=r(loss_share, 3), trade_share=r(trade_share, 3), trades=x['trades'], net_pnl=r(x['net_pnl'])))
        sized = [x for x in rows if x['trades'] >= MIN_GROUP]
        if m.get('net_pnl', 0) > 0 and len(sized) >= 2:
            top = max(sized, key=lambda x: x['net_pnl']); rest = sum(x['net_pnl'] for x in rows) - top['net_pnl']
            if rest <= 0:
                out.append(finding(f'single_source_{dimension}', 'caution',
                                   f'All of the net profit came from {dimension} {top["label"]} ({money(top["net_pnl"])}); every other {dimension} combined made {money(rest)}.',
                                   label=top['label'], net_pnl=r(top['net_pnl']), rest=r(rest)))

    sides = {x['label']: x for x in groups.get('side') or []}
    if len(sides) == 2 and all(x['trades'] >= MIN_GROUP for x in sides.values()):
        lo, hi = sorted(sides.values(), key=lambda x: x['net_pnl'])
        if lo['net_pnl'] < 0 < hi['net_pnl']:
            out.append(finding('side_asymmetry', 'caution',
                               f'{hi["label"].capitalize()}s made {money(hi["net_pnl"])} over {hi["trades"]} trades; '
                               f'{lo["label"]}s lost {money(-lo["net_pnl"])} over {lo["trades"]}.',
                               winning=hi['label'], losing=lo['label'], winning_net=r(hi['net_pnl']), losing_net=r(lo['net_pnl'])))

    s = signals(result)
    if s and s['total']:
        if s['rejected_total'] / s['total'] >= REJECT_HEAVY:
            reason, count = Counter(s['rejected']).most_common(1)[0]
            out.append(finding('rejected_signals', 'caution',
                               f'{pct(s["rejected_total"] / s["total"])} of {s["total"]} signals were refused; most often {reason.replace("_", " ")} ({count}).',
                               rejected=s['rejected'], total=s['total']))
        if s['ignored_or_pending'] / s['total'] >= IGNORED_HEAVY:
            out.append(finding('ignored_signals', 'info',
                               f'{pct(s["ignored_or_pending"] / s["total"])} of {s["total"]} signals arrived while a position or order was already open and were not acted on.',
                               ignored_or_pending=s['ignored_or_pending'], total=s['total']))

    timed = [t for t in trades if t['reason'] == 'time_exit']
    if len(timed) >= MIN_GROUP and len(timed) / len(trades) >= TIME_EXIT_HEAVY:
        out.append(finding('time_exits', 'info', f'{pct(len(timed) / len(trades))} of trades ended on the time limit, net {money(sum(t["net_pnl"] for t in timed))}.',
                           trades=len(timed), net_pnl=r(sum(t['net_pnl'] for t in timed))))

    losers = [t for t in trades if t['net_pnl'] < 0 and t.get('initial_risk')]
    gave = [t for t in losers if t.get('mfe_usdt', 0) >= t['initial_risk']]
    if len(losers) >= MIN_GROUP and len(gave) / len(losers) >= GAVE_BACK:
        cfg = result['config']
        guard = 'breakeven and trailing stops were off' if not cfg.get('breakeven_r') and not cfg.get('trailing_atr') else 'even with the configured breakeven or trailing stop'
        out.append(finding('gave_back_1r', 'info', f'{len(gave)} of {len(losers)} losing trades were at least 1R in open profit before reversing; {guard}.',
                           losers=len(losers), reached_1r=len(gave)))

    folds = result.get('folds') or []
    if folds:
        wins = sum(f['test_metrics']['net_pnl'] > 0 for f in folds)
        out.append(finding('fold_consistency', 'info' if wins * 2 > len(folds) else 'caution',
                           f'{wins} of {len(folds)} held-out folds were profitable.', profitable=wins, folds=len(folds),
                           net_return_pct=[r(f['test_metrics']['net_return_pct']) for f in folds]))
        pairs = []; least_bad = []
        for f in folds:
            winner = next((x for x in f['training_ranking'] if x['eligible'] and x['parameters'] == f['selected']), None)
            if not winner: continue
            pairs.append((winner['metrics'], f['test_metrics']))
            if winner['metrics']['net_pnl'] <= 0: least_bad.append(f['fold'])
        if least_bad:
            which = f'Fold {least_bad[0]}' if len(least_bad) == 1 else 'Folds ' + ', '.join(map(str, least_bad[:-1])) + f' and {least_bad[-1]}'
            out.append(finding('least_bad_pick', 'problem',
                               f'{which} traded a candidate that lost money in training: no eligible candidate was profitable, and the least-bad one was still deployed.',
                               folds=least_bad))
        if pairs:
            train = sum(a['expectancy_r'] for a, _ in pairs) / len(pairs); test = sum(b['expectancy_r'] for _, b in pairs) / len(pairs)
            severity = 'problem' if test <= 0 < train else 'caution' if train > 0 and test < train / 2 else 'info'
            out.append(finding('train_test_decay', severity,
                               f'Selected candidates averaged {train:+.2f}R per trade in training and {test:+.2f}R held out.',
                               training_expectancy_r=r(train, 3), held_out_expectancy_r=r(test, 3)))
        chosen = [f['selected'] for f in folds if f.get('selected') is not None]
        if len(chosen) > 1:
            same = all(x == chosen[0] for x in chosen)
            out.append(finding('selection_stability', 'info' if same else 'caution',
                               'The same parameters won every active fold.' if same else 'The selected parameters changed between folds, so the optimum is not stable.',
                               selected=chosen))

    return sorted(out, key=lambda f: SEVERITY.index(f['severity']))


def card(result, context=None, defaults=None):
    """Versioned JSON view of one run. `context` comes from the registry; without it those fields are None."""
    if result.get('kind') == 'holdout': raise ValueError('Final holdout performance cannot be exported as a run card.')
    context = context or {}; c = result['config']; m = result.get('metrics') or None
    verdict = result.get('verdict') or (read_verdict(m, out_of_sample=bool(result.get('folds')), folds=len(result.get('folds', [])) or None, cash_folds=cash_folds(result)) if m else None)
    notes = findings(result)
    if context.get('engine_current') is False:
        notes.append(finding('engine_changed', 'caution', 'The engine code has changed since this run; rerun it before comparing with newer results.'))
        notes.sort(key=lambda f: SEVERITY.index(f['severity']))
    changed = {k: v for k, v in c.items() if k not in SETUP and defaults is not None and defaults.get(k) != v}
    out = {
        'schema': SCHEMA,
        'precision': 'Rounded for reading. Exact values are in the full run export.',
        'run': {'id': result.get('id'), 'created': result.get('created'), 'kind': result.get('kind', 'backtest'),
                'engine_sha256': result.get('engine_sha256'), 'engine_current': context.get('engine_current'),
                'strategy_version': context.get('strategy_version') or result.get('strategy_version')},
        'strategy': context.get('strategy') or {'key': c['strategy']},
        'setup': {k: c.get(k) for k in SETUP},
        'changed_from_default': changed if defaults is not None else None,
        'verdict': {k: verdict[k] for k in ['tone', 'headline', 'detail', 'evidence']} if verdict else None,
        'splits': splits(result),
        'rows': [{'settings': x['settings'], 'metrics': key_numbers(x['metrics'])} for x in result['rows']] if result.get('rows') else None,
        'costs': costs(m),
        'breakdown': breakdown(result) if m else None,
        'signals': signals(result),
        'selection': context.get('selection'),
        'parent': context.get('parent'),
        # Final-holdout results are exposed as pass or fail only, never as numbers.
        'holdout': {k: v for k, v in (context.get('holdout') or {'status': 'not_configured'}).items() if k in ('status', 'uses')},
        'findings': notes,
        'warnings': list(result.get('warnings') or []),
    }
    return out


def first_sentence(text, limit=150):
    cut = text.split('. ')[0].rstrip('.') + '.'
    return cut if len(cut) <= limit else cut[:limit - 1].rstrip() + '…'


def digest(k):
    """Short Markdown reading of a card. A summary, not a substitute: the card keeps every warning in full."""
    run, s, strategy = k['run'], k['setup'], k['strategy']
    num = lambda x, d=2: '—' if x is None else f'{x:,.{d}f}'
    lines = [f'# Run {run["id"] or "(unsaved)"} · {run["kind"]} · {s["strategy"]}',
             f'{", ".join(s["pairs"])} · {s["timeframe"]}m signals, {s["execution_minutes"]}m execution · {s["start"]} → {s["end"]} (end exclusive) · {s["source"]}']
    status = [f'Strategy status: {strategy["status"]}' if strategy.get('status') else 'Strategy not in the registry']
    if strategy.get('parent'): status.append(f'parent {strategy["parent"]}')
    sel = k.get('selection')
    if sel: status.append(f'{sel["label"]} ({sel["study_trials"]} in this run, {sel["lineage_trials"]} across the lineage)')
    if run.get('engine_current') is False: status.append('engine changed since this run')
    lines += [' · '.join(status), '']
    if k['verdict']:
        v = k['verdict']; lines += [f'**Verdict ({v["evidence"]}):** {v["headline"]} {v["detail"]}', '']
    if k['splits']:
        lines += ['## Key numbers', '| split | dates | held out | trades | net % | max DD % | exp R | PF |', '|---|---|---|---|---|---|---|---|']
        for x in k['splits']:
            q = x['metrics'] or {}
            lines.append(f'| {x["name"]}{" (best rejected)" if x.get("best_rejected") else ""} | {x["start"]} → {x["end"]} | {"yes" if x["held_out"] else "no"} | {q.get("trades", "—")} | '
                         f'{num(q.get("net_return_pct"))} | {num(q.get("max_drawdown_pct"))} | {num(q.get("expectancy_r"), 3)} | {num(q.get("profit_factor"), 2)} |')
        lines.append('')
    if k['rows']:
        lines += ['## Rows (same dates, in-sample)', '| settings | trades | net % | max DD % | exp R |', '|---|---|---|---|---|']
        for x in k['rows']:
            q = x['metrics']; lines.append(f'| {", ".join(f"{a}={b}" for a, b in x["settings"].items())} | {q["trades"]} | {num(q["net_return_pct"])} | {num(q["max_drawdown_pct"])} | {num(q["expectancy_r"], 3)} |')
        lines.append('')
    c = k['costs']
    if c:
        headroom = f'; headroom {c["breakeven_multiple"]:.1f}×' if c['breakeven_multiple'] else ''
        lines += ['## Costs (USDT)', f'raw {num(c["raw_pnl"])} − fees {num(c["fees"])} − slippage {num(c["slippage"])} − spread {num(c["spread"])} '
                  f'− funding {num(c["funding"])} − liquidation {num(c["liquidation_fee"])} = net {num(c["net_pnl"])}{headroom}', '']
    if k['findings']:
        lines += ['## Findings'] + [f'- [{f["severity"]}] {f["text"]}' for f in k['findings']] + ['']
    if k['breakdown']:
        lines.append('## Breakdown (net USDT, trades; worst first)')
        for dim, rows in k['breakdown'].items():
            if rows: lines.append(f'- {dim}: ' + ' · '.join(f'{x["label"]} {num(x["net_pnl"])} ({x["trades"]})' for x in rows[:8]))
        lines.append('')
    g = k['signals']
    if g:
        why = ', '.join(f'{a.replace("_", " ")} {b}' for a, b in sorted(g['rejected'].items(), key=lambda x: -x[1]))
        lines += ['## Signals', f'{g["total"]} signals: {g["entered"]} entered, {g["rejected_total"]} refused{" (" + why + ")" if why else ""}, '
                  f'{g["ignored_or_pending"]} not acted on (already positioned or pending).', '']
    if k['changed_from_default']:
        lines += ['## Settings that differ from defaults', ', '.join(f'{a}={b}' for a, b in k['changed_from_default'].items()), '']
    p = k.get('parent')
    if p and p.get('delta'):
        note = '' if p['comparable'] else ' (different dates, pairs or timeframe: not like for like)'
        lines += [f'## Change versus parent {p["key"]} (run {p["run"]}){note}', ', '.join(f'{a} {b:+.2f}' for a, b in p['delta'].items() if b is not None), '']
    h = k['holdout']
    lines += [f'Final holdout: {h["status"].replace("_", " ")}.', '']
    if k['warnings']:
        lines += [f'## Assumptions ({len(k["warnings"])}; first sentence of each, full text in the card)'] + [f'- {first_sentence(w)}' for w in k['warnings']]
    return '\n'.join(lines).rstrip() + '\n'
