import test from 'node:test';
import assert from 'node:assert/strict';
import { decode, requestURL, mergeSeries } from '../market.mjs';
import { footprints, heikinAshi, studies, signals } from '../studies.mjs';
import { validate } from '../drawings.mjs';
import handler from '../api/market.js';

test('SUPER perpetual request parameters are specific to the selected futures venue', () => {
  const b = requestURL('binance', 'SUPERUSDT', '4h', 'bars'),
    y = requestURL('bybit', 'SUPERUSDT', '4h', 'bars');
  assert.equal(b.pathname, '/fapi/v1/klines');
  assert.equal(b.searchParams.get('interval'), '4h');
  assert.equal(y.searchParams.get('category'), 'linear');
  assert.equal(y.searchParams.get('interval'), '240');
  assert.equal(requestURL('binance', 'SUPERUSDT', '1m', 'book').searchParams.get('symbol'), 'SUPERUSDT');
});
test('untrusted route, symbol, venue and pagination parameters are rejected', () => {
  for (const args of [
    ['other', 'SUPERUSDT', '1m', 'book'],
    ['binance', 'BTC', '1m', 'book'],
    ['bybit', 'SUPERUSDT', 'x', 'bars'],
    ['binance', 'SUPERUSDT', '1m', '__proto__'],
    ['bybit', 'SUPERUSDT', '1m', 'bars', 'bad']
  ])
    assert.throws(() => requestURL(...args));
});
test('REST and streaming records retain exchange side and share footprint keys', () => {
  const trades = decode('binance', 'trades', [
    { a: 4, T: 65000, p: '0.1', q: '2', m: true },
    { a: 5, T: 65005, p: '0.1', q: '3', m: false }
  ]);
  assert.deepEqual(footprints(trades, 60000, 0.00001).get(60000).get(10000), { buy: 3, sell: 2 });
  assert.equal(mergeSeries(trades, [{ ...trades[0] }], 'id').length, 2);
  const bybit = decode('bybit', 'trades', {
    retCode: 0,
    result: { list: [{ execId: 'a', time: '65000', price: '0.1', size: '2', side: 'Sell' }] }
  });
  assert.equal(bybit[0].buy, false);
  assert.equal(bybit[0].t, 65000);
});
test('empty intervals are not backfilled, and Bybit history is chronological', () => {
  const raw = [
    [180000, '2', '4', '1', '3', '7'],
    [60000, '2', '4', '1', '3', '7']
  ];
  const bars = decode('bybit', 'bars', { retCode: 0, result: { list: raw } });
  assert.deepEqual(
    bars.map((b) => b.t),
    [60000, 180000]
  );
  assert.deepEqual(
    mergeSeries(bars, [{ ...bars[1], c: 4 }]).map((b) => b.c),
    [3, 4]
  );
});
test('OI, funding and book snapshots retain their own exchange timestamps', () => {
  assert.deepEqual(decode('binance', 'oi', [{ timestamp: 42, sumOpenInterest: '8' }]), [{ v: 8, t: 42 }]);
  assert.deepEqual(
    decode('bybit', 'fundingHistory', {
      retCode: 0,
      result: { list: [{ fundingRate: '0.0002', fundingRateTimestamp: '44' }] }
    }),
    [{ t: 44, rate: 0.0002 }]
  );
  assert.equal(decode('binance', 'book', { T: 100, E: 102, bids: [], asks: [] }).t, 100);
});
test('Wilder RSI warmup and Heikin-Ashi recursion use only available bars', () => {
  const bars = Array.from({ length: 40 }, (_, i) => ({
    t: i,
    o: 10 + i,
    h: 12 + i,
    l: 9 + i,
    c: 11 + i,
    v: 10
  }));
  const r = studies(bars);
  assert.equal(r[13].rsi, null);
  assert.equal(r[14].rsi, 100);
  assert.deepEqual(studies(bars.slice(0, 18)), r.slice(0, 18));
  assert.deepEqual(
    signals(bars.slice(0, 18), r.slice(0, 18)),
    signals(bars, r).filter((s) => s.i < 18)
  );
  const ha = heikinAshi(bars);
  assert.equal(ha[1].o, (ha[0].o + ha[0].c) / 2);
});
test('drawing imports validate finite anchors and bounded text', () => {
  assert.ok(
    validate([
      {
        kind: 'fib',
        pts: [
          [0, 1],
          [60000, 1.2]
        ]
      }
    ])
  );
  for (const drawing of [
    { kind: 'bad', pts: [[1, 1]] },
    { kind: 'fib', pts: [[1, NaN]] },
    { kind: 'note', pts: [[1, 2]], note: 'x'.repeat(101) }
  ])
    assert.equal(validate([drawing]), false);
});
test('HTTP handler rejects unsupported requests before an upstream call', async () => {
  const res = {
    status(s) {
      this.code = s;
      return this;
    },
    json() {},
    end() {}
  };
  await handler({ method: 'POST', query: {} }, res);
  assert.equal(res.code, 405);
  await handler({ method: 'GET', query: { venue: 'x', kind: 'book' } }, res);
  assert.equal(res.code, 400);
});
test('modeled leverage bands discard levels already crossed', async () => {
  const { leverageBands } = await import('../studies.mjs');
  const bar = { h: 100, l: 100, c: 100, v: 60 };
  const bands = leverageBands([bar], 1);
  assert.equal(bands.length, 6);
  const cleared = leverageBands([bar, { h: 120, l: 80, c: 100, v: 0 }], 1);
  assert.equal(
    cleared.reduce((sum, b) => sum + b.volume, 0),
    0
  );
});
