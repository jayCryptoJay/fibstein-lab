export function studies(bars) {
  let up = 0,
    down = 0,
    rsAvg,
    prevAlpha,
    positive = 0,
    negative = 0,
    trTotal = 0,
    flowQueue = [],
    trQueue = [];
  let ema20, ema50, previous, prevTypical;
  return bars.map((b, i) => {
    const typical = (b.h + b.l + b.c) / 3,
      change = b.c - (previous?.c ?? b.c);
    const tr = Math.max(
      b.h - b.l,
      Math.abs(b.h - (previous?.c ?? b.c)),
      Math.abs(b.l - (previous?.c ?? b.c))
    );
    trTotal += tr;
    trQueue.push(tr);
    if (trQueue.length > 14) trTotal -= trQueue.shift();
    const pos = typical > prevTypical ? typical * b.v : 0,
      neg = typical < prevTypical ? typical * b.v : 0;
    positive += pos;
    negative += neg;
    flowQueue.push([pos, neg]);
    if (flowQueue.length > 14) {
      const [p, n] = flowQueue.shift();
      positive -= p;
      negative -= n;
    }
    if (i <= 14) {
      up += Math.max(change, 0) / 14;
      down += Math.max(-change, 0) / 14;
    } else {
      up = (up * 13 + Math.max(change, 0)) / 14;
      down = (down * 13 + Math.max(-change, 0)) / 14;
    }
    const rsi = i < 14 ? null : !down ? (up ? 100 : 50) : 100 - 100 / (1 + up / down);
    if (rsi !== null) rsAvg = rsAvg == null ? rsi : rsAvg + ((rsi - rsAvg) * 2) / 10;
    ema20 = ema20 == null ? b.c : ema20 + ((b.c - ema20) * 2) / 21;
    ema50 = ema50 == null ? b.c : ema50 + ((b.c - ema50) * 2) / 51;
    if (i >= 14) {
      const atr = trTotal / 14;
      prevAlpha =
        positive >= negative
          ? Math.max(prevAlpha ?? b.l - atr, b.l - atr)
          : Math.min(prevAlpha ?? b.h + atr, b.h + atr);
    }
    previous = b;
    prevTypical = typical;
    return { rsi, rsAvg, ema20, ema50, alpha: prevAlpha };
  });
}
export function heikinAshi(bars) {
  let prev;
  return bars.map((b) => {
    const c = (b.o + b.c + b.h + b.l) / 4,
      o = prev ? (prev.o + prev.c) / 2 : (b.o + b.c) / 2;
    return (prev = { ...b, o, c, h: Math.max(o, c, b.h), l: Math.min(o, c, b.l) });
  });
}
export function footprints(trades, ms, tick) {
  const buckets = new Map();
  for (const x of trades) {
    const t = Math.floor(x.t / ms) * ms,
      p = Math.round(x.p / tick);
    if (!buckets.has(t)) buckets.set(t, new Map());
    const levels = buckets.get(t);
    const row = levels.get(p) ?? { buy: 0, sell: 0 };
    row[x.buy ? 'buy' : 'sell'] += x.q;
    levels.set(p, row);
  }
  return buckets;
}
export function priceStep(raw) {
  if (!(raw > 0)) return 0.00001;
  const mag = 10 ** Math.floor(Math.log10(raw)),
    frac = raw / mag;
  return (frac <= 1 ? 1 : frac <= 2 ? 2 : frac <= 5 ? 5 : 10) * mag;
}
export function price(p) {
  return Number.isFinite(p)
    ? p.toLocaleString('en-US', {
        minimumFractionDigits: p < 1 ? 5 : p < 10 ? 4 : 2,
        maximumFractionDigits: p < 1 ? 6 : p < 10 ? 4 : 2
      })
    : '—';
}
export function compact(n) {
  return Number.isFinite(n)
    ? Intl.NumberFormat('en', { notation: 'compact', maximumFractionDigits: 1 }).format(n)
    : '—';
}
export function percentile(values, q) {
  if (!values.length) return 0;
  const a = [...values].sort((x, y) => x - y);
  return a[Math.floor((a.length - 1) * q)];
}
export function signals(bars, values) {
  const out = [];
  let high, low;
  for (let i = 4; i < bars.length; i++) {
    const j = i - 2,
      r = values[j].rsi;
    if (r == null) continue;
    const window = bars.slice(i - 4, i + 1),
      b = bars[j];
    if (window.every((x) => b.h >= x.h)) {
      if (high && b.h > high.p && r < high.r) out.push({ i, side: -1, tag: 'Div' });
      high = { p: b.h, r };
    }
    if (window.every((x) => b.l <= x.l)) {
      if (low && b.l < low.p && r > low.r) out.push({ i, side: 1, tag: 'Div' });
      low = { p: b.l, r };
    }
    if (i > 15) {
      const r0 = values[i - 2].rsi,
        r1 = values[i - 1].rsi,
        r2 = values[i].rsi;
      if (
        values[i].ema20 > values[i].ema50 &&
        r0 > r1 &&
        r1 >= 40 &&
        r1 <= 50 &&
        r2 > r1 &&
        bars[i].c > bars[i - 1].h
      )
        out.push({ i, side: 1, tag: 'PB' });
      if (
        values[i].ema20 < values[i].ema50 &&
        r0 < r1 &&
        r1 >= 50 &&
        r1 <= 60 &&
        r2 < r1 &&
        bars[i].c < bars[i - 1].l
      )
        out.push({ i, side: -1, tag: 'PB' });
      const prior = bars.slice(i - 5, i);
      if (bars[i].h > Math.max(...prior.map((x) => x.h)) && bars[i].c < bars[i - 1].h && r2 < r1 && r1 >= 65)
        out.push({ i, side: -1, tag: 'Trap' });
      if (bars[i].l < Math.min(...prior.map((x) => x.l)) && bars[i].c > bars[i - 1].l && r2 > r1 && r1 <= 35)
        out.push({ i, side: 1, tag: 'Trap' });
    }
    if (values[i - 1].rsi != null && values[i - 1].rsi <= 30 && values[i].rsi > 30)
      out.push({ i, side: 1, tag: '30↑' });
    if (values[i - 1].rsi >= 70 && values[i].rsi < 70) out.push({ i, side: -1, tag: '70↓' });
  }
  return out;
}

// A transparent scenario, not exchange position data. Add equal volume to
// hypothetical long/short entries at three leverages and retire touched levels.
export function leverageBands(bars, step) {
  const longs = new Map(),
    shorts = new Map();
  const add = (map, p, v) => {
    const k = Math.round(p / step);
    map.set(k, (map.get(k) || 0) + v);
  };
  for (const b of bars) {
    for (const k of longs.keys()) if (b.l <= k * step) longs.delete(k);
    for (const k of shorts.keys()) if (b.h >= k * step) shorts.delete(k);
    const basis = (b.h + b.l + b.c) / 3;
    for (const leverage of [10, 20, 50]) {
      add(longs, basis * (1 - 1 / leverage), b.v / 6);
      add(shorts, basis * (1 + 1 / leverage), b.v / 6);
    }
  }
  return [...longs, ...shorts].map(([k, volume]) => ({ p: k * step, volume }));
}
