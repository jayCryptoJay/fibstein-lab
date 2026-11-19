import { markets, venues, intervals, fetchMarket, mergeSeries, sockets } from './market.mjs';
import { price, compact, priceStep } from './studies.mjs';
import { readCapture, writeCapture } from './capture.mjs';
import { render } from './plot.mjs';
import { tools, pointsNeeded, validate } from './drawings.mjs';
const $ = (id) => document.getElementById(id);
const canvas = $('chart'),
  dialog = $('dialog');
const query = new URLSearchParams(location.search);
const state = {
  symbol: markets.includes(query.get('symbol')) ? query.get('symbol') : 'SUPERUSDT',
  venue: Object.hasOwn(venues, query.get('venue')) ? query.get('venue') : 'binance',
  frame: Object.hasOwn(intervals, query.get('frame')) ? query.get('frame') : '1m',
  mode: 'candles',
  tab: 'book',
  count: 70,
  offset: 0,
  priceZoom: 1,
  heatFloor: 0.12,
  heatGain: 0.8,
  plugins: [],
  layers: {
    heat: true,
    depth: true,
    vol: true,
    rsi: false,
    signals: true,
    oi: false,
    cvd: false,
    funding: false,
    alpha: false,
    ema: false,
    profile: false,
    tpo: false,
    bubbles: false,
    liq: false,
    clusters: false
  },
  hover: null,
  tool: null,
  pending: null,
  selected: null,
  replay: null,
  bars: [],
  drawings: []
};
const layerNames = {
  heat: 'Depth heatmap',
  depth: 'Current depth profile',
  vol: 'Volume',
  rsi: 'RSI 14 + EMA',
  signals: 'RSI signals',
  oi: 'Open interest',
  cvd: 'Cumulative delta',
  funding: 'Funding rate',
  alpha: 'AlphaTrend',
  ema: 'EMA 20 / 50',
  profile: 'Traded volume profile',
  tpo: 'Time at price (TPO)',
  bubbles: 'Large trades',
  liq: 'Liquidation prints',
  clusters: 'Est. liquidation clusters'
};
let controller,
  dirty = true,
  P,
  tradeIDs = new Set(),
  fail = new Set(),
  liveMsg = {},
  sideDirty = true,
  lastSide = 0,
  lastPaint = 0,
  playing,
  bootstrap = false;
const safeLoad = (key, fallback) => {
  try {
    return JSON.parse(localStorage.getItem(key)) ?? fallback;
  } catch {
    return fallback;
  }
};
function save(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* Private browsing can disable storage. */
  }
}
const savedPrefs = safeLoad('fs-orderflow-prefs', {});
for (const key in state.layers)
  if (typeof savedPrefs.layers?.[key] === 'boolean') state.layers[key] = savedPrefs.layers[key];
if (['light', 'dark'].includes(savedPrefs.theme)) document.documentElement.dataset.theme = savedPrefs.theme;
function prefs() {
  save('fs-orderflow-prefs', {
    layers: state.layers,
    theme: document.documentElement.dataset.theme || 'dark'
  });
}
function changed() {
  dirty = true;
  sideDirty = true;
}
function drawKey() {
  return 'fs-drawings-v1-' + state.venue + '-' + state.symbol;
}
function persistDrawings() {
  save(drawKey(), state.drawings);
  changed();
}
function control(name, action, pressed) {
  const b = document.createElement('button');
  b.textContent = name;
  if (pressed != null) b.setAttribute('aria-pressed', pressed);
  b.onclick = () => action(b);
  return b;
}
for (const s of markets) $('symbol').add(new Option(s.replace('USDT', ' / USDT'), s));
for (const [key, name] of Object.entries(venues)) $('venue').add(new Option(name, key));
$('symbol').value = state.symbol;
$('venue').value = state.venue;
$('symbol').onchange = () => {
  state.symbol = $('symbol').value;
  connect();
};
$('venue').onchange = () => {
  state.venue = $('venue').value;
  connect();
};
for (const key of Object.keys(intervals))
  $('frames').append(
    control(
      key,
      () => {
        if (state.frame !== key) {
          state.frame = key;
          connect();
        }
      },
      state.frame === key
    )
  );
function setBook(data) {
  if (!Number.isFinite(data.t) || data.t <= 0 || data.t < (state.book?.t || 0)) return;
  const numeric = (rows) => rows.map(([p, q]) => [+p, +q]).filter(([p, q]) => p > 0 && q > 0);
  const bids = numeric(data.bids),
    asks = numeric(data.asks);
  if (!bids.length || !asks.length || bids[0][0] >= asks[0][0]) return;
  state.book = { t: data.t, bids, asks };
  const last = state.heat.at(-1),
    bucket = Math.floor(data.t / 2000);
  if (last && Math.floor(last.t / 2000) === bucket) return;
  const mid = (bids[0][0] + asks[0][0]) / 2,
    tick = priceStep(mid * 0.0001);
  const levels = new Map();
  for (const [rows, side] of [
    [bids, 1],
    [asks, 0]
  ])
    for (const [p, q] of rows) {
      const key = Math.round(p / tick) + ':' + side;
      const cell = levels.get(key) || [Math.round(p / tick) * tick, 0, side];
      cell[1] += q;
      levels.set(key, cell);
    }
  state.heat.push({ t: data.t, rows: [...levels.values()], tick });
  if (state.heat.length > 1800) state.heat.shift();
}
function ingest(kind, data, stamp) {
  if (stamp) liveMsg[kind] = Date.now();
  if (kind === 'bars' || kind === 'latest') state.bars = mergeSeries(state.bars, data);
  if (kind === 'oi') {
    // Keep one live observation per minute alongside the independently backfilled 5m series.
    if (
      stamp &&
      state.oi.length &&
      Math.floor(state.oi.at(-1).t / 60000) === Math.floor(data.at(-1).t / 60000)
    )
      state.oi.pop();
    state.oi = mergeSeries(state.oi, data, 't', 1800);
  }
  if (kind === 'fundingHistory') state.fundingHistory = mergeSeries(state.fundingHistory, data);
  if (kind === 'funding' && (!state.funding || state.funding.t <= data.t)) {
    state.funding = data;
    const last = state.fundingHistory.at(-1);
    if (!last || Date.now() - last.t > 60000 || last.rate !== data.rate)
      state.fundingHistory = mergeSeries(state.fundingHistory, [data]);
  }
  if (kind === 'trades') {
    const fresh = data.filter((t) => {
      if (tradeIDs.has(t.id) || !(t.p > 0 && t.q > 0)) return false;
      tradeIDs.add(t.id);
      return true;
    });
    if (fresh.length) {
      state.trades.push(...fresh);
      if (
        state.trades.length > fresh.length &&
        fresh[0].t < state.trades[state.trades.length - fresh.length - 1].t
      )
        state.trades.sort((a, b) => a.t - b.t);
      if (state.trades.length > 32000) {
        state.trades.splice(0, state.trades.length - 30000);
        tradeIDs = new Set(state.trades.map((t) => t.id));
      }
    }
  }
  if (kind === 'liqs') state.liqs = [...state.liqs, ...data].slice(-3000);
  if (kind === 'book') setBook(data);
  fail.delete(kind);
  changed();
}
let activeMarket,
  captureReady = false;
const memory = new Map();
function checkpoint() {
  if (!activeMarket || !captureReady || !state.heat.length) return;
  const snap = { heat: state.heat.slice(), trades: state.trades.slice(), liqs: state.liqs.slice() };
  memory.set(activeMarket, snap);
  writeCapture(activeMarket, snap);
}
function restoreCapture(snap) {
  if (!snap) return;
  const since = Date.now() - 3600000;
  state.heat = mergeSeries(
    (snap.heat || []).filter((x) => x.t > since),
    state.heat,
    't',
    1800
  );
  state.liqs = mergeSeries(
    (snap.liqs || []).filter((x) => x.t > since),
    state.liqs,
    't',
    3000
  );
  ingest(
    'trades',
    (snap.trades || []).filter((x) => x.t > since)
  );
}
addEventListener('pagehide', checkpoint);
document.addEventListener('visibilitychange', () => {
  if (document.hidden) checkpoint();
});
setInterval(checkpoint, 45000);
function connect() {
  checkpoint();
  captureReady = false;
  controller?.abort();
  clearInterval(playing);
  playing = null;
  controller = new AbortController();
  const { signal } = controller;
  Object.assign(state, {
    bars: [],
    trades: [],
    book: null,
    oi: [],
    funding: null,
    fundingHistory: [],
    heat: [],
    liqs: [],
    offset: 0,
    hover: null,
    pending: null,
    selected: null,
    tool: null,
    replay: null,
    ms: intervals[state.frame] * 60000
  });
  tradeIDs = new Set();
  fail = new Set();
  liveMsg = {};
  bootstrap = true;
  P = null;
  const saved = safeLoad(drawKey(), []);
  state.drawings = validate(saved) ? saved : [];
  const { venue, symbol, frame } = state;
  activeMarket = venue + ':' + symbol;
  if (memory.has(activeMarket)) {
    restoreCapture(memory.get(activeMarket));
    captureReady = true;
  } else
    readCapture(activeMarket).then((snap) => {
      if (!signal.aborted) {
        restoreCapture(snap);
        captureReady = true;
      }
    });
  history.replaceState(null, '', '?' + new URLSearchParams({ symbol, venue, frame }));
  for (const b of $('frames').children) b.setAttribute('aria-pressed', b.textContent === frame);
  $('replay-tools').hidden = true;
  $('replay').setAttribute('aria-pressed', 'false');
  $('play').textContent = 'Play';
  $('foot-venue').textContent = venues[venue] + ' · USDT perpetual';
  changed();
  async function pull(kind, end) {
    try {
      const result = await fetchMarket(venue, symbol, frame, kind, signal, end);
      if (signal.aborted) return;
      // Prefer newer streamed values when a slow history request overlaps.
      if (kind === 'bars') {
        const existing = state.bars;
        ingest(kind, result);
        if (existing.length) ingest(kind, existing);
      } else if (kind === 'latest' && Date.now() - (liveMsg.bars || 0) < 5000)
        ingest('bars', result.slice(0, -1));
      else ingest(kind, result);
      return result;
    } catch {
      if (!signal.aborted) {
        fail.add(kind);
        changed();
      }
    }
  }
  function poll(kind, period, stream = kind) {
    let timer;
    async function tick() {
      if (signal.aborted) return;
      if (!document.hidden && (!liveMsg[stream] || Date.now() - liveMsg[stream] > period * 1.5))
        await pull(kind);
      if (!signal.aborted) timer = setTimeout(tick, period);
    }
    timer = setTimeout(tick, period);
    signal.addEventListener('abort', () => clearTimeout(timer), { once: true });
  }
  async function initial() {
    const rows = await pull('bars');
    if (signal.aborted) return;
    if (venue === 'bybit' && rows?.length === 1000) await pull('bars', rows[0].t - 1);
    if (signal.aborted) return;
    bootstrap = false;
    changed();
  }
  initial();
  for (const kind of ['trades', 'book', 'oi', 'funding', 'fundingHistory']) pull(kind);
  sockets(venue, symbol, frame, ingest, signal);
  poll('book', 3500);
  poll('trades', 5500);
  poll('latest', 12000, 'bars');
  poll('oi', 45000);
  poll('funding', 30000);
  // Retry a failed bootstrap when a connection comes back.
  let recovery = setInterval(() => {
    if (!document.hidden && !bootstrap && state.bars.length < 20) {
      bootstrap = true;
      initial();
    }
  }, 25000);
  signal.addEventListener('abort', () => clearInterval(recovery), { once: true });
}
function mode(next) {
  state.mode = next;
  state.count =
    next === 'footprint'
      ? Math.max(5, Math.floor(canvas.clientWidth / 68))
      : Math.max(18, Math.floor(canvas.clientWidth / 10));
  state.offset = 0;
  state.priceZoom = 1;
  for (const b of document.querySelectorAll('[data-mode]'))
    b.setAttribute(
      'aria-pressed',
      b.dataset.mode === next || (b.dataset.mode === 'candles' && next !== 'footprint')
    );
  $('style').value = next === 'footprint' ? 'candles' : next;
  changed();
}
document.querySelectorAll('[data-mode]').forEach((b) => (b.onclick = () => mode(b.dataset.mode)));
$('style').onchange = () => mode($('style').value);
document.querySelectorAll('[data-tab]').forEach(
  (b) =>
    (b.onclick = () => {
      state.tab = b.dataset.tab;
      for (const x of b.parentNode.children) x.setAttribute('aria-pressed', x === b);
      changed();
    })
);
function zoom(f) {
  state.count = Math.max(3, Math.min(500, Math.round(state.count * f)));
  changed();
}
$('minus').onclick = () => zoom(1.32);
$('plus').onclick = () => zoom(0.75);
$('fit').onclick = () => {
  mode(state.mode);
  state.hover = null;
  changed();
};
function show(title) {
  $('dialog-title').textContent = title;
  $('dialog-content').replaceChildren();
  dialog.showModal();
  return $('dialog-content');
}
$('close').onclick = () => dialog.close();
dialog.onclick = (e) => {
  if (e.target === dialog) dialog.close();
};
function help(parent, s) {
  const p = document.createElement('p');
  p.className = 'help';
  p.textContent = s;
  parent.append(p);
}
function field(parent, name, input) {
  const label = document.createElement('label');
  label.className = 'field';
  label.append(name, input);
  parent.append(label);
}
$('layers').onclick = () => {
  const panel = show('Chart layers'),
    grid = document.createElement('div');
  grid.className = 'choices';
  panel.append(grid);
  for (const [key, name] of Object.entries(layerNames))
    grid.append(
      control(
        name,
        (b) => {
          state.layers[key] = !state.layers[key];
          b.setAttribute('aria-pressed', state.layers[key]);
          prefs();
          changed();
        },
        state.layers[key]
      )
    );
  for (const [key, label, min, max] of [
    ['heatFloor', 'Minimum depth', 0, 0.95],
    ['heatGain', 'Heat intensity', 0.1, 1.6]
  ]) {
    const input = document.createElement('input');
    input.type = 'range';
    input.min = min;
    input.max = max;
    input.step = '.05';
    input.value = state[key];
    input.oninput = () => {
      state[key] = +input.value;
      changed();
    };
    field(panel, label, input);
  }
  help(
    panel,
    'Depth accumulates from observed book snapshots. This browser retains up to one hour of captured depth. Footprints show aggressive sells × buys at each price. The outline marks the highest-volume row. Side markers flag a 3:1 diagonal imbalance.'
  );
  help(
    panel,
    'Estimated clusters split each of the last 200 bars’ volume across hypothetical 10×, 20× and 50× longs and shorts. Touched levels expire. Brighter bands carry more modeled volume. The model uses public candles only.'
  );
};
function chooseTool(kind) {
  state.tool = kind;
  state.selected = null;
  state.pending = null;
  dialog.close();
  changed();
}
function finish() {
  if (state.pending?.pts.length) {
    state.drawings.push(state.pending);
    state.selected = state.pending;
    persistDrawings();
  }
  state.tool = null;
  state.pending = null;
  changed();
}
function del() {
  if (state.selected) {
    state.drawings = state.drawings.filter((d) => d !== state.selected);
    state.selected = null;
    persistDrawings();
  }
}
$('draw').onclick = () => {
  const panel = show('Drawing tools'),
    grid = document.createElement('div');
  grid.className = 'choices';
  panel.append(grid);
  for (const [key, name] of Object.entries(tools))
    grid.append(control(name, () => chooseTool(key), key === state.tool));
  panel.append(
    control('Select drawing', () => chooseTool('select')),
    control('Delete selected', () => {
      del();
      dialog.close();
    }),
    control('Clear market drawings', () => {
      if (confirm('Clear drawings for this market?')) {
        state.drawings = [];
        state.selected = null;
        persistDrawings();
      }
      dialog.close();
    })
  );
  if (state.pending?.kind === 'path')
    panel.append(
      control('Finish polyline', () => {
        finish();
        dialog.close();
      })
    );
};
$('settings').onclick = () => {
  const panel = show('Chart settings'),
    theme = document.createElement('select');
  for (const n of ['dark', 'light']) theme.add(new Option(n === 'dark' ? 'Dark' : 'Light', n));
  theme.value = document.documentElement.dataset.theme || 'dark';
  theme.onchange = () => {
    document.documentElement.dataset.theme = theme.value;
    prefs();
    changed();
  };
  field(panel, 'Theme', theme);
  const yRange = document.createElement('input');
  yRange.type = 'range';
  yRange.min = '.3';
  yRange.max = '8';
  yRange.step = '.1';
  yRange.value = state.priceZoom;
  yRange.oninput = () => {
    state.priceZoom = +yRange.value;
    changed();
  };
  field(panel, 'Price range scale', yRange);
  const picker = document.createElement('input');
  picker.type = 'color';
  picker.value = getComputedStyle(canvas).getPropertyValue('--accent').trim() || '#ba9aff';
  picker.oninput = () => {
    document.documentElement.style.setProperty('--accent', picker.value);
    save('fs-orderflow-accent', picker.value);
    changed();
  };
  field(panel, 'Accent color', picker);
  panel.append(
    control('Export drawings', () => {
      const a = document.createElement('a');
      a.href = URL.createObjectURL(
        new Blob([JSON.stringify({ venue: state.venue, symbol: state.symbol, drawings: state.drawings })], {
          type: 'application/json'
        })
      );
      a.download = state.symbol + '-drawings.json';
      a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 1000);
    })
  );
  const file = document.createElement('input');
  file.type = 'file';
  file.accept = '.json';
  file.hidden = true;
  panel.append(
    file,
    control('Import market drawings', () => file.click())
  );
  file.onchange = async () => {
    try {
      if (file.files[0].size > 1500000) throw Error();
      const d = JSON.parse(await file.files[0].text());
      if (d.venue !== state.venue || d.symbol !== state.symbol || !validate(d.drawings)) throw Error();
      state.drawings = d.drawings;
      persistDrawings();
      dialog.close();
    } catch {
      help(panel, 'Choose a valid drawing export for this exchange and symbol.');
    }
  };
  help(
    panel,
    'Drag to pan. Pinch or use the wheel to zoom. In footprint mode, zoom in for bid × ask values. Times are UTC.'
  );
};
const accent = safeLoad('fs-orderflow-accent', '');
if (/^#[0-9a-f]{6}$/i.test(accent)) document.documentElement.style.setProperty('--accent', accent);
function replaySet(i) {
  if (!state.replay) return;
  state.replay.index = Math.max(0, Math.min(i, state.replay.bars.length - 1));
  $('replay-range').value = state.replay.index;
  state.offset = 0;
  changed();
}
function exitReplay() {
  clearInterval(playing);
  playing = null;
  $('play').textContent = 'Play';
  state.replay = null;
  $('replay-tools').hidden = true;
  $('replay').setAttribute('aria-pressed', 'false');
  state.offset = 0;
  changed();
}
$('replay').onclick = () => {
  if (state.replay) {
    exitReplay();
    return;
  }
  if (!state.bars.length) return;
  state.replay = { bars: state.bars.slice(), index: 0 };
  $('replay-range').min = 0;
  $('replay-range').max = state.bars.length - 1;
  $('replay-tools').hidden = false;
  $('replay').setAttribute('aria-pressed', 'true');
  replaySet(Math.max(0, state.bars.length - 60));
};
$('replay-range').oninput = (e) => replaySet(+e.target.value);
$('step').onclick = () => replaySet(state.replay.index + 1);
$('exit-replay').onclick = exitReplay;
$('play').onclick = () => {
  if (playing) {
    clearInterval(playing);
    playing = null;
    $('play').textContent = 'Play';
    return;
  }
  $('play').textContent = 'Pause';
  playing = setInterval(() => {
    if (state.replay.index >= state.replay.bars.length - 1) {
      $('play').click();
      return;
    }
    replaySet(state.replay.index + 1);
  }, 480);
};
const pointers = new Map();
let drag, dist;
function position(e) {
  const r = canvas.getBoundingClientRect();
  return [e.clientX - r.left, e.clientY - r.top];
}
const length = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1]);
canvas.onpointerdown = (e) => {
  const p = position(e);
  pointers.set(e.pointerId, p);
  canvas.setPointerCapture(e.pointerId);
  if (pointers.size === 2) {
    dist = length(...pointers.values());
    drag = null;
    return;
  }
  if (!P) return;
  drag = { p, offset: state.offset, moved: false };
  if (state.tool === 'brush') state.pending = { kind: 'brush', pts: [P.point(...p)] };
};
canvas.onpointermove = (e) => {
  const p = position(e);
  if (pointers.has(e.pointerId)) pointers.set(e.pointerId, p);
  if (pointers.size === 2) {
    const d = length(...pointers.values());
    if (dist && d > 0 && Math.abs(d / dist - 1) > 0.06) {
      zoom(dist / d);
      dist = d;
    }
    return;
  }
  if (!P) return;
  state.hover = p;
  if (drag) {
    if (length(drag.p, p) > 4) drag.moved = true;
    if (state.tool === 'brush' && state.pending?.pts.length < 5000) state.pending.pts.push(P.point(...p));
    else if (!state.tool) {
      state.offset = Math.max(
        0,
        Math.min(
          (state.replay ? state.replay.index + 1 : state.bars.length) - 2,
          Math.round(drag.offset + (p[0] - drag.p[0]) / P.dx)
        )
      );
    }
  }
  dirty = true;
};
canvas.onpointerup = (e) => {
  const p = position(e);
  pointers.delete(e.pointerId);
  if (state.tool === 'brush' && state.pending) {
    finish();
  } else if (drag && !drag.moved && state.tool && P && p[1] <= P.h) {
    const a = P.point(...p);
    if (state.tool === 'select') {
      const hits = state.drawings
        .map((d) => ({ d, gap: Math.min(...d.pts.map((q) => length(p, [P.xt(q[0]), P.y(q[1])]))) }))
        .sort((a, b) => a.gap - b.gap);
      state.selected = hits[0]?.gap < 30 ? hits[0].d : null;
      state.tool = null;
    } else {
      state.pending ??= { kind: state.tool, pts: [] };
      state.pending.pts.push(a);
      if (state.tool === 'note') {
        const note = prompt('Chart note');
        if (!note) {
          chooseTool(null);
          return;
        }
        state.pending.note = note.slice(0, 100);
      }
      if (state.pending.pts.length >= (pointsNeeded[state.tool] ?? 2)) finish();
    }
  }
  drag = null;
  if (e.pointerType === 'touch') state.hover = null;
  changed();
};
canvas.onpointercancel = (e) => {
  pointers.delete(e.pointerId);
  drag = null;
  state.hover = null;
  changed();
};
canvas.onpointerleave = () => {
  if (!pointers.size) {
    state.hover = null;
    dirty = true;
  }
};
canvas.ondblclick = () => {
  if (state.tool === 'path') finish();
  else if (!state.tool) $('fit').click();
};
canvas.onwheel = (e) => {
  e.preventDefault();
  const [px] = position(e);
  if (P && px > P.w) {
    state.priceZoom = Math.max(0.3, Math.min(12, state.priceZoom * (e.deltaY > 0 ? 1.2 : 1 / 1.2)));
    changed();
  } else zoom(e.deltaY > 0 ? 1.15 : 0.87);
};
canvas.addEventListener('wheel', (e) => e.preventDefault(), { passive: false });
addEventListener('keydown', (e) => {
  if (dialog.open || ['INPUT', 'SELECT'].includes(e.target.tagName)) return;
  if (e.key === 'Escape') {
    state.tool = null;
    state.pending = null;
    changed();
  }
  if (e.key === 'Delete' || e.key === 'Backspace') del();
  if (e.key === 'Enter' && state.pending?.kind === 'path') finish();
});
function side() {
  const root = $('side-content'),
    coin = state.symbol.slice(0, -4);
  if (state.replay) {
    root.innerHTML = '<div class="side-note">Live market panels are paused during bar replay.</div>';
    return;
  }
  const row = (cl, cells, css = '') =>
    '<div class="' +
    cl +
    '" style="' +
    css +
    '">' +
    cells.map((c) => '<span>' + c + '</span>').join('') +
    '</div>';
  const fresh = (t) => Math.max(0, Math.floor((Date.now() - t) / 1000));
  if (state.tab === 'book' && state.book) {
    const n = Math.max(3, Math.min(22, Math.floor((root.clientHeight - 72) / 38))),
      { bids, asks, t } = state.book;
    const sideRows = (levels, sell) => {
      let total = 0;
      const top = levels.slice(0, n),
        max = top.reduce((a, r) => a + r[1], 0);
      const rows = top.map(([p, q]) => {
        total += q;
        return row(
          'book-row',
          ['<b class="' + (sell ? 'sell' : 'buy') + '">' + price(p) + '</b>', compact(q), compact(total)],
          '--bar:var(--' + (sell ? 'askbg' : 'bidbg') + ');--pct:' + Math.round((100 * total) / max) + '%'
        );
      });
      return (sell ? rows.reverse() : rows).join('');
    };
    root.innerHTML =
      row('columns', ['Price (USDT)', coin, 'Total']) +
      sideRows(asks, true) +
      row('spread', ['Spread ' + price(asks[0][0] - bids[0][0]), fresh(t) + 's ago']) +
      sideRows(bids, false);
    return;
  }
  if (state.tab === 'tape' && state.trades.length) {
    root.innerHTML =
      row('columns', ['Time (UTC)', 'Price (USDT)', coin]) +
      state.trades
        .slice(-65)
        .reverse()
        .map((t) =>
          row('trade-row', [
            new Date(t.t).toISOString().slice(11, 19),
            '<b class="' + (t.buy ? 'buy' : 'sell') + '">' + price(t.p) + '</b>',
            compact(t.q)
          ])
        )
        .join('');
    return;
  }
  if (state.tab === 'flow') {
    const recent = state.trades.filter((t) => t.t >= Date.now() - 60000);
    let buy = 0,
      sell = 0;
    for (const t of recent) {
      if (t.buy) buy += t.q;
      else sell += t.q;
    }
    const oi = state.oi.at(-1),
      old = oi && state.oi.findLast((x) => x.t <= oi.t - 300000);
    const values = [
      ['Aggressive buys · 1m', recent.length ? compact(buy) : '—'],
      ['Aggressive sells · 1m', recent.length ? compact(sell) : '—'],
      ['Observed delta · 1m', recent.length ? compact(buy - sell) : '—'],
      ['OI change · 5m', old ? compact(oi.v - old.v) : '—'],
      ['Observed liquidations', String(state.liqs.length)],
      ['Captured depth columns', String(state.heat.length)]
    ];
    root.innerHTML = values
      .map(([k, v]) => '<div class="flow"><span>' + k + '</span><b>' + v + '</b></div>')
      .join('');
    return;
  }
  root.innerHTML =
    '<div class="side-note">' +
    (fail.has(state.tab === 'book' ? 'book' : 'trades')
      ? 'Feed unavailable. Retrying…'
      : 'Waiting for exchange data…') +
    '</div>';
}
function display() {
  const bars = state.replay ? state.replay.bars.slice(0, state.replay.index + 1) : state.bars,
    bar = bars.at(-1),
    book = state.replay ? null : state.book,
    lastTrade = state.replay ? null : state.trades.at(-1);
  const mark = state.replay ? null : state.funding;
  const quote = lastTrade && (!bar || lastTrade.t >= bar.t) ? lastTrade.p : (bar?.c ?? mark?.mark);
  $('price').textContent = price(quote);
  $('bid').textContent = price(book?.bids[0]?.[0]);
  $('ask').textContent = price(book?.asks[0]?.[0]);
  const oi = state.oi.at(-1);
  $('open-interest').textContent = state.replay ? '—' : compact(oi?.v);
  $('funding').textContent = mark ? (100 * mark.rate).toFixed(4) + '%' : '—';
  const age = book ? Date.now() - book.t : Infinity,
    stream = Date.now() - (liveMsg.book || 0) < 10000;
  const label = state.replay
    ? 'Bar replay'
    : age < 15000
      ? stream
        ? 'Streaming'
        : 'Snapshots'
      : book
        ? 'Depth stale'
        : bar
          ? 'Depth offline'
          : fail.size
            ? 'Reconnecting'
            : 'Connecting';
  $('status').textContent = label;
  $('status').className = 'status' + (age < 15000 && !state.replay ? ' live' : '');
  $('empty').hidden = !!bar;
  $('empty').textContent = fail.has('bars')
    ? 'Candle feed unavailable. Retrying…'
    : 'Connecting to the market…';
  $('context').textContent = state.replay
    ? 'Replay · ' + new Date(bar.t).toISOString().slice(0, 16).replace('T', ' ') + ' UTC'
    : state.bars.length.toLocaleString() +
      ' bars · ' +
      state.trades.length.toLocaleString() +
      ' observed trades';
  $('tool-state').textContent = state.tool
    ? (tools[state.tool] || 'Select a drawing') +
      (state.tool === 'path'
        ? ' · Enter to finish'
        : state.tool === 'brush'
          ? ' · drag'
          : state.tool === 'select'
            ? ' · tap anchor'
            : ' · tap ' + ((state.pending?.pts.length || 0) + 1))
    : '';
  $('legend').textContent =
    state.mode === 'footprint'
      ? 'Footprint · sells × buys · POC outlined'
      : (state.mode === 'heikin' ? 'Heikin-Ashi' : state.symbol) +
        ' · ' +
        state.frame +
        (state.layers.heat ? '  /  Depth' : '');
  $('clock').textContent = new Date().toISOString().slice(11, 19) + ' UTC';
  P = render(canvas, state);
  const tip = $('tooltip');
  tip.hidden = !P?.hover;
  if (P?.hover) {
    const { bar: b, indicator, level, oi, cvd } = P.hover;
    tip.textContent = [
      new Date(b.t).toISOString().slice(5, 16).replace('T', ' ') + ' UTC',
      'O ' + price(b.o) + '   H ' + price(b.h),
      'L ' + price(b.l) + '   C ' + price(b.c),
      'Volume ' + compact(b.v),
      state.layers.rsi && indicator.rsi != null ? 'RSI ' + indicator.rsi.toFixed(1) : null,
      state.layers.oi ? 'OI ' + compact(oi) : null,
      state.layers.cvd ? 'CVD ' + compact(cvd) : null,
      level ? 'Sell ' + compact(level.sell) + ' × Buy ' + compact(level.buy) : null
    ]
      .filter(Boolean)
      .join('\n');
    tip.style.left = Math.max(0, Math.min(canvas.clientWidth - tip.offsetWidth, state.hover[0] + 10)) + 'px';
    tip.style.top = Math.max(0, Math.min(canvas.clientHeight - tip.offsetHeight, state.hover[1] + 10)) + 'px';
  }
}
function tick(now) {
  if (!document.hidden && dirty && now - lastPaint >= 110) {
    dirty = false;
    lastPaint = now;
    display();
  }
  if (!document.hidden && sideDirty && now - lastSide >= 350) {
    sideDirty = false;
    lastSide = now;
    side();
  }
  requestAnimationFrame(tick);
}
window.FibStein = Object.freeze({
  registerIndicator(plugin) {
    if (!plugin || typeof plugin.compute !== 'function')
      throw Error('Expected compute(bars) returning price values');
    state.plugins.push(plugin);
    changed();
    return () => {
      state.plugins = state.plugins.filter((x) => x !== plugin);
      changed();
    };
  }
});
new ResizeObserver(changed).observe(canvas);
setInterval(changed, 1000);
requestAnimationFrame(tick);
connect();
