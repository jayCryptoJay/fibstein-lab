import {
  studies,
  heikinAshi,
  footprints,
  priceStep,
  price,
  compact,
  percentile,
  signals,
  leverageBands
} from './studies.mjs';
import { paintDrawing } from './drawings.mjs';
const time = (n, day = false) => new Date(n).toISOString().slice(day ? 5 : 11, day ? 10 : 16);
export function render(canvas, state) {
  const W = canvas.clientWidth,
    H = canvas.clientHeight;
  if (W < 30 || H < 35) return;
  const dpr = Math.min(2, devicePixelRatio || 1);
  if (canvas.width !== Math.round(W * dpr) || canvas.height !== Math.round(H * dpr)) {
    canvas.width = Math.round(W * dpr);
    canvas.height = Math.round(H * dpr);
  }
  const g = canvas.getContext('2d');
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, W, H);
  const style = getComputedStyle(canvas),
    col = (k) => style.getPropertyValue('--' + k).trim();
  const text = col('text'),
    muted = col('muted'),
    grid = col('grid'),
    buy = col('buy'),
    sell = col('sell'),
    accent = col('accent');
  const { layers: L, ms } = state;
  const source = state.replay?.bars || state.bars;
  const stop = state.replay ? state.replay.index + 1 : source.length;
  const end = Math.max(1, stop - state.offset),
    start = Math.max(0, end - state.count);
  const all = source.slice(0, stop),
    rows = all.slice(start, end);
  if (!rows.length) return;
  const w = W - (W < 450 ? 63 : 76),
    dx = w / (state.count + 2),
    x = (i) => (i - start + 0.5) * dx;
  const xt = (t) => {
    // Interpolate on the bar index so missing candles do not shift drawing anchors.
    let lo = 0,
      hi = all.length;
    while (lo < hi) {
      const m = (lo + hi) >> 1;
      if (all[m].t < t) lo = m + 1;
      else hi = m;
    }
    if (lo === all.length) return x(lo - 1) + ((t - all.at(-1).t) / ms) * dx;
    if (lo === 0 || all[lo].t === t) return x(lo) + ((t - all[lo].t) / ms) * dx;
    return x(lo - 1) + ((t - all[lo - 1].t) / (all[lo].t - all[lo - 1].t)) * dx;
  };
  const panels = ['vol', 'rsi', 'oi', 'cvd', 'funding'].filter((k) => L[k]);
  const bottom = H - 21,
    ph = Math.min(H * 0.15, 62, (H * 0.52) / Math.max(1, panels.length)),
    h = bottom - ph * panels.length;
  const indicators = studies(all),
    series = state.mode === 'heikin' ? heikinAshi(all) : all;
  let low = Infinity,
    high = -Infinity;
  for (let i = start; i < end; i++) {
    const b = series[i];
    low = Math.min(low, b.l);
    high = Math.max(high, b.h);
    if (L.alpha && indicators[i].alpha != null) {
      low = Math.min(low, indicators[i].alpha);
      high = Math.max(high, indicators[i].alpha);
    }
  }
  const span = Math.max(high - low, Math.abs(high) * 0.00015, 1e-9);
  const center = (high + low) / 2;
  high = center + span * 0.62 * state.priceZoom;
  low = center - span * 0.59 * state.priceZoom;
  const y = (p) => 12 + ((high - p) / (high - low)) * Math.max(10, h - 22),
    py = (v) => high - ((v - 12) / Math.max(10, h - 22)) * (high - low);
  const P = {
    w,
    h,
    xt,
    y,
    start,
    end,
    dx,
    x,
    point: (px, sy) => {
      const i = Math.max(0, Math.min(all.length - 1, start + Math.floor(px / dx)));
      return [all[i].t, py(sy)];
    }
  };
  const line = (points, color, width = 1, dash = []) => {
    g.strokeStyle = color;
    g.lineWidth = width;
    g.setLineDash(dash);
    g.beginPath();
    let open = false;
    for (const p of points) {
      if (!p || !Number.isFinite(p[1])) {
        open = false;
        continue;
      }
      if (open) g.lineTo(...p);
      else g.moveTo(...p);
      open = true;
    }
    g.stroke();
    g.setLineDash([]);
  };
  const label = (s, px, yy, c = muted) => {
    g.fillStyle = c;
    g.fillText(String(s), px, yy);
  };
  g.font = '10px ui-monospace, monospace';
  const step = priceStep((high - low) / Math.max(3, h / 44));
  for (let p = Math.ceil(low / step) * step; p <= high; p += step) {
    const yy = y(p);
    line(
      [
        [0, yy],
        [w, yy]
      ],
      grid
    );
    label(price(p), w + 5, yy + 3);
  }
  const jump = Math.max(1, Math.ceil(70 / dx));
  for (let i = start; i < end; i++)
    if (i % jump === 0) {
      line(
        [
          [x(i), 0],
          [x(i), bottom]
        ],
        grid
      );
      label(time(all[i].t, ms >= 3600000), Math.max(1, x(i) - 13), H - 5);
    }
  const cutoff = state.replay ? all.at(-1).t + ms - 1 : Infinity;
  const trades = state.trades.filter((t) => t.t <= cutoff);
  const first = rows[0].t,
    last = rows.at(-1).t + ms,
    visible = trades.filter((t) => t.t >= first && t.t < last);
  const rowsize = priceStep((high - low) / Math.max(5, h / (state.mode === 'footprint' ? 14 : 3)));
  const columns = footprints(visible, ms, rowsize);
  g.save();
  g.beginPath();
  g.rect(0, 0, w, h);
  g.clip();
  if (L.heat) {
    const history = state.heat.filter((c) => c.t >= first - ms && c.t < last && c.t <= cutoff);
    const volumes = history.flatMap((c) => c.rows.map((r) => r[1]));
    const max = percentile(volumes, 0.96) || 1;
    for (let i = 0; i < history.length; i++) {
      const c = history[i],
        left = xt(c.t) - dx / 2;
      const width = Math.max(
        1,
        Math.min(xt(Math.min(history[i + 1]?.t ?? c.t + 2000, c.t + 12000, cutoff)) - dx / 2 - left, w - left)
      );
      for (const [p, q, side] of c.rows) {
        const power = Math.min(1, q / max);
        if (power < state.heatFloor) continue;
        g.globalAlpha = 0.08 + Math.min(0.85, power * state.heatGain);
        g.fillStyle = power > 0.88 ? '#f4b95e' : side ? '#36a8a9' : '#a071cb';
        g.fillRect(
          Math.floor(left),
          y(p) - 1,
          Math.ceil(width),
          Math.max(2, Math.min(7, Math.abs(y(p) - y(p + c.tick))))
        );
      }
    }
    g.globalAlpha = 1;
  }
  if (L.depth && !state.offset && !state.replay && state.book && Date.now() - state.book.t < 15000) {
    const { bids, asks } = state.book,
      largest = Math.max(...bids.map((r) => r[1]), ...asks.map((r) => r[1]));
    for (const [levels, color] of [
      [bids, buy],
      [asks, sell]
    ]) {
      g.fillStyle = color;
      g.globalAlpha = 0.35;
      for (const [p, q] of levels) {
        const width = Math.max(1, w * 0.12 * Math.sqrt(q / largest));
        g.fillRect(w - width, y(p) - 1, width, 2);
      }
    }
    g.globalAlpha = 1;
  }
  if (L.tpo || L.profile) {
    const slot = priceStep((high - low) / 44),
      bins = new Map();
    const add = (key, qty) => bins.set(key, (bins.get(key) || 0) + qty);
    if (L.tpo)
      for (const b of rows) for (let k = Math.floor(b.l / slot); k <= Math.floor(b.h / slot); k++) add(k, 1);
    const drawBins = (c) => {
      const max = Math.max(1, ...bins.values());
      for (const [k, v] of bins) {
        const a = (w * 0.14 * v) / max;
        g.fillStyle = c;
        g.fillRect(w - a, y((k + 1) * slot), a, Math.max(2, y(k * slot) - y((k + 1) * slot) - 1));
      }
    };
    if (L.tpo) {
      drawBins('#8f78d344');
      bins.clear();
    }
    if (L.profile) {
      for (const t of visible) add(Math.floor(t.p / slot), t.q);
      drawBins('#4594c46b');
    }
  }
  if (L.clusters) {
    const slot = priceStep((high - low) / 100);
    const completed = all.slice(Math.max(0, end - 201), end - (end === all.length && !state.replay ? 1 : 0));
    const bands = leverageBands(completed, slot).filter((b) => b.p >= low && b.p <= high);
    const max =
      percentile(
        bands.map((b) => b.volume),
        0.95
      ) || 1;
    for (const band of bands) {
      const power = Math.min(1, band.volume / max);
      g.fillStyle = '#e3a44b';
      g.globalAlpha = 0.08 + 0.3 * power;
      g.fillRect(0, y(band.p + slot / 2), w, Math.max(1, y(band.p - slot / 2) - y(band.p + slot / 2)));
    }
    g.globalAlpha = 1;
  }
  if (['line', 'area'].includes(state.mode)) {
    const points = rows.map((b, i) => [x(start + i), y(b.c)]);
    if (state.mode === 'area') {
      g.beginPath();
      g.moveTo(points[0][0], h);
      for (const p of points) g.lineTo(...p);
      g.lineTo(points.at(-1)[0], h);
      g.closePath();
      g.fillStyle = '#9770e32b';
      g.fill();
    }
    line(points, accent, 1.8);
  } else
    for (let i = start; i < end; i++) {
      const b = series[i],
        xx = x(i),
        ink = b.c >= b.o ? buy : sell;
      g.fillStyle = ink;
      const cell = columns.get(b.t);
      if (state.mode === 'footprint' && cell) {
        const max = Math.max(...[...cell.values()].map((v) => v.buy + v.sell));
        for (const [id, v] of cell) {
          const yy = y(id * rowsize),
            height = Math.max(3, Math.min(23, Math.abs(y(0) - y(rowsize))));
          const share = v.buy / (v.buy + v.sell);
          g.globalAlpha = 0.22 + (0.6 * (v.buy + v.sell)) / max;
          g.fillStyle = share >= 0.5 ? buy : sell;
          g.fillRect(xx - dx * 0.46, yy - height / 2, dx * 0.93, height - 1);
          g.globalAlpha = 1;
          if (v.buy + v.sell === max) {
            g.strokeStyle = accent;
            g.strokeRect(xx - dx * 0.46, yy - height / 2, dx * 0.93, height - 1);
          }
          const lower = cell.get(id - 1)?.sell,
            upper = cell.get(id + 1)?.buy;
          if (lower > 0 && v.buy >= 3 * lower) {
            g.fillStyle = buy;
            g.fillRect(xx + dx * 0.4, yy - height / 2, 2, height - 1);
          }
          if (upper > 0 && v.sell >= 3 * upper) {
            g.fillStyle = sell;
            g.fillRect(xx - dx * 0.46, yy - height / 2, 2, height - 1);
          }
          if (dx >= 46) {
            g.font = (dx < 56 ? '8' : '10') + 'px monospace';
            const s = compact(v.sell) + '×' + compact(v.buy);
            label(s, xx - g.measureText(s).width / 2, yy + 3, text);
          }
        }
      } else {
        g.globalAlpha = state.mode === 'footprint' ? 0.32 : 1;
        line(
          [
            [xx, y(b.h)],
            [xx, y(b.l)]
          ],
          ink
        );
        g.fillRect(
          xx - Math.max(1, dx * 0.32),
          Math.min(y(b.o), y(b.c)),
          Math.max(1, dx * 0.64),
          Math.max(1.3, Math.abs(y(b.o) - y(b.c)))
        );
        g.globalAlpha = 1;
      }
    }
  g.font = '10px monospace';
  if (state.mode === 'footprint' && dx >= 36) {
    for (let i = start; i < end; i++) {
      const cell = columns.get(all[i].t);
      if (!cell) continue;
      let delta = 0;
      for (const row of cell.values()) delta += row.buy - row.sell;
      const txt = 'Δ' + compact(delta);
      label(txt, x(i) - g.measureText(txt).width / 2, h - 4, delta >= 0 ? buy : sell);
    }
  }
  if (L.ema || L.alpha)
    for (const [key, c] of [
      ['ema20', '#78ace9'],
      ['ema50', '#d7ae6d'],
      ['alpha', '#bd8df1']
    ]) {
      if (key === 'alpha' ? !L.alpha : !L.ema) continue;
      line(
        rows.map((_, i) =>
          indicators[i + start][key] == null ? null : [x(i + start), y(indicators[i + start][key])]
        ),
        c,
        1.4
      );
    }
  if (L.bubbles && visible.length) {
    const q = percentile(
      visible.map((t) => t.q),
      0.98
    );
    for (const t of visible) {
      if (t.q < q) continue;
      g.beginPath();
      g.arc(xt(t.t) - dx / 2, y(t.p), Math.min(11, 3 + Math.sqrt(t.q / q)), 0, 2 * Math.PI);
      g.strokeStyle = t.buy ? buy : sell;
      g.stroke();
    }
  }
  if (L.liq)
    for (const e of state.liqs) {
      if (e.t < first || e.t >= last || e.t > cutoff) continue;
      label('×', xt(e.t) - dx / 2, y(e.p), '#ffb96d');
    }
  for (const d of state.drawings) paintDrawing(g, d, P, all, d === state.selected ? '#e7bd65' : accent);
  if (state.pending?.pts.length) paintDrawing(g, state.pending, P, all, '#f7be6d');
  for (const plugin of state.plugins) {
    try {
      const vals = plugin.compute(all);
      line(
        rows.map((_, i) => (Number.isFinite(vals[start + i]) ? [x(start + i), y(vals[start + i])] : null)),
        plugin.color || accent
      );
    } catch {
      /* Isolate third-party indicator errors. */
    }
  }
  g.restore();
  const lastBar = rows.at(-1),
    yy = y(lastBar.c);
  line(
    [
      [0, yy],
      [w, yy]
    ],
    lastBar.c >= lastBar.o ? buy : sell,
    1,
    [2, 4]
  );
  g.fillStyle = col('pane');
  g.fillRect(w, yy - 8, W - w, 16);
  label(price(lastBar.c), w + 4, yy + 3, lastBar.c >= lastBar.o ? buy : sell);
  const delta = new Map();
  let cumulative = 0;
  for (const t of trades) {
    const bucket = Math.floor(t.t / ms) * ms;
    delta.set(bucket, (delta.get(bucket) || 0) + (t.buy ? t.q : -t.q));
  }
  const cvd = all.map((b) => {
    if (!delta.has(b.t) && (!trades.length || b.t + ms <= trades[0].t)) return null;
    cumulative += delta.get(b.t) || 0;
    return cumulative;
  });
  function align(series, field) {
    let idx = 0,
      v = null;
    return all.map((b) => {
      while (idx < series.length && series[idx].t < Math.min(cutoff + 1, b.t + ms)) {
        v = series[idx][field];
        idx++;
      }
      return v;
    });
  }
  const panelInfo = {};
  for (const [j, k] of panels.entries()) {
    const top = h + j * ph;
    const full =
      k === 'rsi'
        ? indicators.map((a) => a.rsi)
        : k === 'vol'
          ? all.map((a) => a.v)
          : k === 'cvd'
            ? cvd
            : k === 'funding'
              ? align(state.fundingHistory, 'rate')
              : align(state.oi, 'v');
    panelInfo[k] = full;
    const vis = full.slice(start, end),
      valid = vis.filter(Number.isFinite);
    const lo = k === 'vol' || k === 'rsi' ? 0 : Math.min(...valid),
      hi = k === 'rsi' ? 100 : Math.max(...valid);
    const sy = (v) => top + ph - 3 - (hi > lo ? (v - lo) / (hi - lo) : 0.5) * (ph - 8);
    line(
      [
        [0, top],
        [W, top]
      ],
      grid
    );
    const ink = { vol: buy, rsi: '#ba92ef', oi: '#6eb3e1', cvd: '#54c5a7', funding: '#e3a667' }[k];
    if (k === 'vol')
      for (let i = start; i < end; i++) {
        g.fillStyle = all[i].c >= all[i].o ? '#329c81' : '#aa536a';
        g.fillRect(x(i) - dx * 0.3, sy(full[i]), Math.max(1, dx * 0.6), top + ph - sy(full[i]));
        if (i >= 20 && full[i] > (2 * full.slice(i - 20, i).reduce((sum, v) => sum + v, 0)) / 20) {
          g.fillStyle = '#e9b45e';
          g.fillRect(x(i) - dx * 0.3, sy(full[i]), Math.max(1, dx * 0.6), 2);
        }
      }
    else if (valid.length) {
      line(
        vis.map((v, i) => (Number.isFinite(v) ? [x(start + i), sy(v)] : null)),
        ink,
        1.4
      );
      if (k === 'rsi') {
        for (const r of [30, 70])
          line(
            [
              [0, sy(r)],
              [w, sy(r)]
            ],
            grid,
            1,
            [3, 3]
          );
        line(
          indicators
            .slice(start, end)
            .map((a, i) => (Number.isFinite(a.rsAvg) ? [x(start + i), sy(a.rsAvg)] : null)),
          '#ae9463'
        );
        if (L.signals)
          for (const s of signals(all, indicators)) {
            if (s.i >= start && s.i < end) label(s.tag, x(s.i), sy(full[s.i]) - 2, s.side === 1 ? buy : sell);
          }
      }
    }
    label(
      { vol: 'Volume', rsi: 'RSI 14', oi: 'Open interest', cvd: 'CVD · observed', funding: 'Funding' }[k],
      6,
      top + 12,
      ink
    );
    if (valid.length) {
      const v = valid.at(-1);
      label(
        k === 'rsi' ? v.toFixed(1) : k === 'funding' ? (100 * v).toFixed(4) + '%' : compact(v),
        w + 4,
        top + ph / 2
      );
    }
  }
  const hover = state.hover;
  if (hover) {
    const i = Math.min(end - 1, Math.max(start, start + Math.floor(hover[0] / dx)));
    line(
      [
        [x(i), 0],
        [x(i), bottom]
      ],
      muted,
      1,
      [3, 3]
    );
    line(
      [
        [0, hover[1]],
        [w, hover[1]]
      ],
      muted,
      1,
      [3, 3]
    );
    P.hover = {
      bar: all[i],
      indicator: indicators[i],
      oi: panelInfo.oi?.[i],
      cvd: cvd[i],
      level: columns.get(all[i].t)?.get(Math.round(py(hover[1]) / rowsize))
    };
  }
  return P;
}
