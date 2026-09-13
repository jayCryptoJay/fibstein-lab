"""Plain-language reading of a run. One sentence a beginner understands, no jargon."""

PRELIMINARY = 30   # Below this, sampling noise dominates any ranking.
STABLE = 100       # Matches the engine's existing evidence warning.
THIN = 1.5         # Cost headroom below this is not a durable edge.

COSTS = ['fees', 'slippage', 'spread', 'funding', 'liquidation_fee']


def money(x, unit='USDT'):
    return f'{x:,.2f} {unit}'


def read(metrics, out_of_sample=False, folds=None):
    """Return {tone, headline, detail, evidence, cost_share, breakeven}.

    tone drives colour only. headline is the whole story in one sentence;
    detail is the single most useful follow-up, never a list of caveats.
    """
    m = metrics
    n = m['trade_count']
    raw = m['raw_pnl']                       # Before any execution cost.
    net = m['net_pnl']
    costs = sum(m[k] for k in COSTS)         # net == raw - costs, exactly.
    ret = m['net_return_pct']
    dd = m['max_drawdown_pct']

    # Cost headroom: how many times current costs the signal can absorb.
    breakeven = raw / costs if raw > 0 and costs > 0 else None
    cost_share = costs / raw if raw > 0 else None

    evidence = ('Held out' if out_of_sample else 'In-sample')
    if out_of_sample and folds:
        evidence = f'Held out across {folds} folds'

    def result(tone, headline, detail):
        return {'tone': tone, 'headline': headline, 'detail': detail, 'evidence': evidence,
                'trades': n, 'raw_pnl': raw, 'costs': costs, 'net_pnl': net,
                'cost_share': cost_share, 'breakeven': breakeven,
                'out_of_sample': out_of_sample}

    if n == 0:
        return result('empty', 'No trades were taken.',
                      'The rules never triggered in this range. Widen the dates, loosen the '
                      'higher-timeframe filter, or check the signal diagnostics below.')

    if m['liquidations']:
        word = 'position was' if m['liquidations'] == 1 else 'positions were'
        return result('blown', f'{m["liquidations"]} {word} liquidated.',
                      'Liquidation means the account could not hold the position, so the stop '
                      'never mattered. Lower leverage or reduce risk per trade before reading '
                      'anything else here.')

    if raw > 0 and net <= 0:
        return result('blown', 'The signal was profitable. Execution costs took all of it.',
                      f'Entries and exits earned {money(raw)} before costs, then fees, spread, '
                      f'slippage and funding removed {money(costs)}. The idea may be sound; '
                      'it cannot pay for itself at this trade frequency.')

    if net <= 0:
        of_which = f' {money(costs)} of that was execution cost.' if costs > 0 else ''
        return result('loss', f'Lost {abs(ret):.1f}% over {n} trades.',
                      f'The strategy was losing before costs, so this is the idea, not the '
                      f'fills.{of_which}')

    if n < PRELIMINARY:
        return result('unproven', f'Made {ret:.1f}%, but only across {n} trades.',
                      f'Under {PRELIMINARY} trades, a result this size is normal luck. Treat the '
                      'number as an anecdote and run a longer range before drawing a conclusion.')

    if breakeven is not None and breakeven < THIN:
        return result('thin', f'Made {ret:.1f}%, but the edge barely clears its costs.',
                      f'Costs would only need to rise {breakeven:.1f}× — a worse fee tier, a '
                      'thinner book, one bad funding week — to erase the whole result.')

    headroom = (f' It survives costs up to {breakeven:.1f}× today\u2019s.' if breakeven else '')
    if not out_of_sample:
        return result('unproven', f'Made {ret:.1f}% over {n} trades, with a {dd:.1f}% worst drawdown.',
                      'These are the same dates the settings were chosen on, so the result is not '
                      'yet evidence.' + headroom + ' Run a walk-forward to see if it holds on dates '
                      'it has never seen.')

    if n < STABLE:
        return result('holds', f'Held up on unseen dates: {ret:.1f}% over {n} trades.',
                      f'Encouraging, but under {STABLE} held-out trades the estimate still moves '
                      'a lot.' + headroom)

    return result('holds', f'Held up on unseen dates: {ret:.1f}% over {n} trades, '
                           f'{dd:.1f}% worst drawdown.',
                  'Chosen on training dates and tested on dates it had never seen.' + headroom +
                  ' Reserve a fresh period before risking money.')
