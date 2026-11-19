export const markets = ['SUPERUSDT', 'BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'XRPUSDT'];
export const intervals = { '1m': 1, '5m': 5, '15m': 15, '1h': 60, '4h': 240 };
export const venues = { binance: 'Binance USDⓈ-M', bybit: 'Bybit Linear' };
const paths = {
  binance: {
    bars: '/fapi/v1/klines',
    latest: '/fapi/v1/klines',
    fundingHistory: '/fapi/v1/fundingRate',
    book: '/fapi/v1/depth',
    trades: '/fapi/v1/aggTrades',
    oi: '/futures/data/openInterestHist',
    funding: '/fapi/v1/premiumIndex'
  },
  bybit: {
    bars: '/v5/market/kline',
    latest: '/v5/market/kline',
    fundingHistory: '/v5/market/funding/history',
    book: '/v5/market/orderbook',
    trades: '/v5/market/recent-trade',
    oi: '/v5/market/open-interest',
    funding: '/v5/market/tickers'
  }
};
export function requestURL(venue, symbol, frame, kind, end) {
  if (
    !Object.hasOwn(venues, venue) ||
    !markets.includes(symbol) ||
    !Object.hasOwn(intervals, frame) ||
    !Object.hasOwn(paths[venue], kind)
  )
    throw Error('Unsupported market request');
  const b = venue === 'binance';
  const url = new URL(paths[venue][kind], b ? 'https://www.binance.com' : 'https://api.bybit.com');
  url.searchParams.set('symbol', symbol);
  if (!b) url.searchParams.set('category', 'linear');
  if (kind === 'bars' || kind === 'latest') {
    url.searchParams.set('interval', b ? frame : intervals[frame]);
    url.searchParams.set('limit', kind === 'latest' ? '10' : b ? '1500' : '1000');
    if (end) {
      if (!Number.isSafeInteger(+end) || +end <= 0) throw Error('Invalid end time');
      url.searchParams.set(b ? 'endTime' : 'end', end);
    }
  }
  if (kind === 'book') url.searchParams.set('limit', b ? '100' : '50');
  if (kind === 'trades') url.searchParams.set('limit', '1000');
  if (kind === 'oi') {
    url.searchParams.set(b ? 'period' : 'intervalTime', b ? '5m' : '5min');
    url.searchParams.set('limit', b ? '500' : '200');
  }
  return url;
}
export function decode(venue, kind, json) {
  const b = venue === 'binance';
  if ((!b && json.retCode !== 0) || (b && json.code < 0))
    throw Error(json.retMsg || json.msg || 'Exchange unavailable');
  const data = b ? json : json.result;
  if (kind === 'fundingHistory')
    return (b ? data : data.list)
      .map((x) => ({ t: +(x.fundingTime || x.fundingRateTimestamp), rate: +x.fundingRate }))
      .sort((a, z) => a.t - z.t);
  if (kind === 'bars' || kind === 'latest')
    return (b ? data : data.list)
      .map((v) => ({ t: +v[0], o: +v[1], h: +v[2], l: +v[3], c: +v[4], v: +v[5] }))
      .sort((a, z) => a.t - z.t);
  if (kind === 'trades')
    return (b ? data : data.list)
      .map((x) =>
        b
          ? { id: String(x.a), t: x.T, p: +x.p, q: +x.q, buy: !x.m }
          : { id: x.execId, t: +x.time, p: +x.price, q: +x.size, buy: x.side === 'Buy' }
      )
      .sort((a, z) => a.t - z.t);
  if (kind === 'book')
    return {
      t: +(b ? data.T || data.E : data.cts || data.ts),
      bids: b ? data.bids : data.b,
      asks: b ? data.asks : data.a
    };
  if (kind === 'oi')
    return (b ? data : data.list)
      .map((x) => ({ t: +x.timestamp, v: +(b ? x.sumOpenInterest : x.openInterest) }))
      .sort((a, z) => a.t - z.t);
  const f = b ? data : data.list[0];
  return { t: b ? +f.time : +json.time, rate: +(b ? f.lastFundingRate : f.fundingRate), mark: +f.markPrice };
}
export async function fetchMarket(venue, symbol, frame, kind, signal, end) {
  const query = new URLSearchParams({ venue, symbol, frame, kind });
  if (end) query.set('end', end);
  let error;
  for (const url of ['/api/market?' + query, requestURL(venue, symbol, frame, kind, end)]) {
    try {
      const r = await fetch(url, { signal: AbortSignal.any([signal, AbortSignal.timeout(8500)]) });
      if (!r.ok) throw Error('HTTP ' + r.status);
      return decode(venue, kind, await r.json());
    } catch (e) {
      if (signal.aborted) throw e;
      error = e;
    }
  }
  throw error;
}
export function mergeSeries(old, add, key = 't', limit = 2500) {
  const rows = new Map(old.map((x) => [x[key], x]));
  for (const x of add) rows.set(x[key], x);
  return [...rows.values()].sort((a, b) => a.t - b.t).slice(-limit);
}
export function sockets(venue, symbol, frame, emit, signal) {
  const s = symbol.toLowerCase();
  function start(url, subscribe, parse) {
    let retry = 1000,
      timer,
      heartbeat,
      socket;
    function open() {
      if (signal.aborted) return;
      const ws = new WebSocket(url);
      socket = ws;
      ws.onopen = () => {
        if (subscribe) {
          ws.send(JSON.stringify({ op: 'subscribe', args: subscribe }));
          heartbeat = setInterval(() => {
            if (ws.readyState === 1) ws.send('{"op":"ping"}');
          }, 20000);
        }
      };
      ws.onmessage = (e) => {
        if (signal.aborted) return;
        try {
          parse(JSON.parse(e.data));
          retry = 1000;
        } catch {
          /* REST polling remains available. */
        }
      };
      ws.onerror = () => ws.close();
      ws.onclose = () => {
        clearInterval(heartbeat);
        if (!signal.aborted) {
          timer = setTimeout(open, retry);
          retry = Math.min(30000, retry * 2);
        }
      };
    }
    signal.addEventListener(
      'abort',
      () => {
        clearTimeout(timer);
        clearInterval(heartbeat);
        socket?.close();
      },
      { once: true }
    );
    open();
  }
  if (venue === 'binance') {
    start(
      'wss://fstream.binance.com/market/stream?streams=' +
        [s + '@aggTrade', s + '@kline_' + frame, s + '@markPrice@1s', s + '@forceOrder'].join('/'),
      null,
      (msg) => {
        const d = msg.data;
        if (d.e === 'kline') {
          const k = d.k;
          emit('bars', [{ t: k.t, o: +k.o, h: +k.h, l: +k.l, c: +k.c, v: +k.v }], d.E);
        }
        if (d.e === 'aggTrade')
          emit('trades', [{ id: String(d.a), t: d.T, p: +d.p, q: +d.q, buy: !d.m }], d.E);
        if (d.e === 'markPriceUpdate') emit('funding', { rate: +d.r, mark: +d.p, t: d.E }, d.E);
        if (d.e === 'forceOrder')
          emit('liqs', [{ t: d.o.T, p: +d.o.ap || +d.o.p, q: +d.o.z, side: d.o.S }], d.E);
      }
    );
    start('wss://fstream.binance.com/public/ws/' + s + '@depth20@100ms', null, (d) => {
      if (d.b?.length && d.a?.length) emit('book', { t: d.T || d.E, bids: d.b, asks: d.a }, d.E);
    });
  } else {
    const bids = new Map(),
      asks = new Map();
    let valid = false,
      seq = 0;
    start(
      'wss://stream.bybit.com/v5/public/linear',
      [
        'publicTrade.' + symbol,
        'orderbook.50.' + symbol,
        'kline.' + intervals[frame] + '.' + symbol,
        'tickers.' + symbol,
        'allLiquidation.' + symbol
      ],
      (m) => {
        if (!m.topic) return;
        const d = m.data;
        if (m.topic.startsWith('kline'))
          emit(
            'bars',
            d.map((k) => ({ t: +k.start, o: +k.open, h: +k.high, l: +k.low, c: +k.close, v: +k.volume })),
            m.ts
          );
        if (m.topic.startsWith('publicTrade'))
          emit(
            'trades',
            d.map((k) => ({ id: k.i, t: k.T, p: +k.p, q: +k.v, buy: k.S === 'Buy' })),
            m.ts
          );
        if (m.topic.startsWith('tickers')) {
          if (d.openInterest) emit('oi', [{ t: m.ts, v: +d.openInterest }], m.ts);
          if (d.fundingRate && d.markPrice)
            emit('funding', { t: m.ts, rate: +d.fundingRate, mark: +d.markPrice }, m.ts);
        }
        if (m.topic.startsWith('allLiquidation'))
          emit(
            'liqs',
            d.map((k) => ({ t: k.T, p: +k.p, q: +k.v, side: k.S })),
            m.ts
          );
        if (m.topic.startsWith('orderbook')) {
          if (m.type === 'snapshot' || d.u === 1) {
            bids.clear();
            asks.clear();
            valid = true;
            seq = 0;
          }
          if (!valid || d.u <= seq) return;
          seq = d.u;
          for (const [map, entries] of [
            [bids, d.b],
            [asks, d.a]
          ])
            for (const [p, q] of entries) {
              if (+q === 0) map.delete(p);
              else map.set(p, q);
            }
          emit(
            'book',
            {
              t: m.cts || m.ts,
              bids: [...bids].sort((a, b) => b[0] - a[0]),
              asks: [...asks].sort((a, b) => a[0] - b[0])
            },
            m.ts
          );
        }
      }
    );
  }
}
