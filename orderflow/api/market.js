import { requestURL } from '../market.mjs';

async function publicData(url) {
  const response = await fetch(url, {
    signal: AbortSignal.timeout(4500),
    headers: { Accept: 'application/json' }
  });
  if (!response.ok) throw Error('Exchange HTTP ' + response.status);
  const data = await response.json();
  if (data.retCode || data.code < 0) throw Error('Exchange unavailable');
  return data;
}

// Fixed public read-only routes. No private endpoints or arbitrary upstream URLs.
export default async function handler(req, res) {
  if (req.method !== 'GET') return res.status(405).end();
  let url;
  try {
    const { venue, symbol, frame, kind, end } = req.query;
    url = requestURL(venue, symbol, frame, kind, end);
  } catch {
    return res.status(400).json({ message: 'Unsupported market request' });
  }
  try {
    let data;
    try {
      data = await publicData(url);
    } catch (err) {
      if (req.query.venue !== 'bybit') throw err;
      url.hostname = 'api.bytick.com';
      data = await publicData(url);
    }
    const ttl = { book: 1, trades: 1, funding: 10, bars: 10, latest: 3, oi: 30, fundingHistory: 180 }[
      req.query.kind
    ];
    res.setHeader('Cache-Control', `public, s-maxage=${ttl}`);
    return res.status(200).json(data);
  } catch {
    return res.status(502).json({ message: 'Public exchange feed unavailable' });
  }
}
