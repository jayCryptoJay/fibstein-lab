# FibStein Order Flow

A read-only browser workspace for USDT perpetual markets, opening on Binance SUPER/USDT. Candles, trades, depth and open interest are scoped to the selected exchange. Footprints and depth history show only observations the feed supplied. This workspace uses an independent Canvas renderer.

Serve this directory with Vercel to enable the same-origin public market-data endpoint. For static hosting or local use, the browser can also request the public exchange APIs directly. Run `python -m http.server 8000 --directory orderflow` from the repository root and open http://localhost:8000.

The local backtesting server is independent of this site.
