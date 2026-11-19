import { price } from './studies.mjs';
export const tools = {
  trend: 'Trendline',
  horizontal: 'Horizontal line',
  arrow: 'Arrow',
  fib: 'Fibonacci',
  measure: 'Price range',
  circle: 'Ellipse',
  channel: 'Parallel channel',
  pitch: 'Pitchfork',
  long: 'Long position',
  short: 'Short position',
  vwap: 'Anchored VWAP',
  brush: 'Freehand',
  path: 'Polyline',
  elliott: 'Elliott impulse',
  rectangle: 'Rectangle',
  note: 'Text note'
};
export const pointsNeeded = {
  horizontal: 1,
  vwap: 1,
  note: 1,
  channel: 3,
  pitch: 3,
  long: 3,
  short: 3,
  elliott: 6,
  path: Infinity,
  brush: Infinity
};
export function validate(rows) {
  return (
    Array.isArray(rows) &&
    rows.length <= 300 &&
    rows.every(
      (d) =>
        d &&
        Object.hasOwn(tools, d.kind) &&
        Array.isArray(d.pts) &&
        d.pts.length > 0 &&
        d.pts.length <= 5000 &&
        d.pts.every((p) => Array.isArray(p) && p.length === 2 && p.every(Number.isFinite)) &&
        (!d.note || (typeof d.note === 'string' && d.note.length <= 100))
    )
  );
}
export function paintDrawing(g, d, P, bars, color) {
  const pts = d.pts.map(([t, p]) => [P.xt(t), P.y(p)]);
  const [a, b = a, c = b] = pts;
  const path = (points) => {
    g.beginPath();
    points.forEach(([x, y], i) => (i ? g.lineTo(x, y) : g.moveTo(x, y)));
    g.stroke();
  };
  const label = (s, x, y) => g.fillText(s, x + 3, y - 3);
  const rect = (x, y, w, h, ink) => {
    g.fillStyle = ink;
    g.fillRect(x, y, w, h);
    g.fillStyle = color;
  };
  g.save();
  g.strokeStyle = color;
  g.fillStyle = color;
  g.lineWidth = 1.35;
  g.font = '11px monospace';
  switch (d.kind) {
    case 'horizontal':
      path([
        [0, a[1]],
        [P.w, a[1]]
      ]);
      label(price(d.pts[0][1]), 5, a[1]);
      break;
    case 'vwap': {
      let sum = 0,
        qty = 0;
      const line = [];
      for (const bar of bars) {
        if (bar.t < d.pts[0][0]) continue;
        sum += ((bar.h + bar.l + bar.c) / 3) * bar.v;
        qty += bar.v;
        if (qty) line.push([P.xt(bar.t), P.y(sum / qty)]);
      }
      path(line);
      if (line.length) label('aVWAP', ...line[0]);
      break;
    }
    case 'fib':
      for (const f of [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1, 1.618]) {
        const y = a[1] + (b[1] - a[1]) * f;
        path([
          [Math.min(a[0], b[0]), y],
          [Math.max(a[0], b[0]) + 36, y]
        ]);
        label(f.toString(), Math.max(a[0], b[0]), y);
      }
      break;
    case 'circle':
      g.beginPath();
      g.ellipse(
        (a[0] + b[0]) / 2,
        (a[1] + b[1]) / 2,
        Math.abs(b[0] - a[0]) / 2,
        Math.abs(b[1] - a[1]) / 2,
        0,
        0,
        2 * Math.PI
      );
      g.stroke();
      break;
    case 'rectangle':
    case 'measure':
      g.strokeRect(a[0], a[1], b[0] - a[0], b[1] - a[1]);
      if (d.kind === 'measure' && d.pts[1])
        label(((d.pts[1][1] / d.pts[0][1] - 1) * 100).toFixed(2) + '%', ...b);
      break;
    case 'channel':
      path([a, b]);
      path([c, [c[0] + b[0] - a[0], c[1] + b[1] - a[1]]]);
      break;
    case 'pitch': {
      const m = [(b[0] + c[0]) / 2, (b[1] + c[1]) / 2];
      for (const p of [a, b, c]) path([p, [p[0] + (m[0] - a[0]) * 3, p[1] + (m[1] - a[1]) * 3]]);
      path([b, c]);
      break;
    }
    case 'short':
    case 'long': {
      const w = Math.max(35, Math.abs(b[0] - a[0]), Math.abs(c[0] - a[0]));
      const sign = d.kind === 'long' ? -1 : 1;
      const reward = Math.abs(b[1] - a[1]),
        risk = Math.abs(c[1] - a[1]);
      rect(a[0], a[1], w, sign * reward, '#27c99c44');
      rect(a[0], a[1], w, -sign * risk, '#ff577744');
      path([a, [a[0] + w, a[1]]]);
      if (risk) label((reward / risk).toFixed(2) + 'R', ...a);
      break;
    }
    case 'arrow': {
      path([a, b]);
      const t = Math.atan2(b[1] - a[1], b[0] - a[0]);
      path([
        [b[0] - 9 * Math.cos(t - 0.4), b[1] - 9 * Math.sin(t - 0.4)],
        b,
        [b[0] - 9 * Math.cos(t + 0.4), b[1] - 9 * Math.sin(t + 0.4)]
      ]);
      break;
    }
    case 'note':
      label(d.note || 'Note', ...a);
      break;
    default:
      path(pts);
      if (d.kind === 'elliott') pts.forEach((p, i) => label('(' + i + ')', ...p));
  }
  for (const p of pts.length < 10 ? pts : []) {
    g.beginPath();
    g.arc(...p, 2, 0, Math.PI * 2);
    g.fill();
  }
  g.restore();
}
