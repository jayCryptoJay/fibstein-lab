import React from 'react';

// The pre-result screen. One job: get a stranger to a real result without configuring anything.
const CLAIMS=[
 ['Funding, fees, spread, slippage','Every cost a perpetual actually charges, reconciled to the cent.'],
 ['Walk-forward built in','Settings are chosen on one period and tested on dates they never saw.'],
 ['Assumptions written down','Where the model approximates the exchange, it says so instead of hiding it.'],
];

export default function FirstRun({onSample}){
 return <section className="first-run">
  <p className="first-run-mark" aria-hidden="true">φ</p>
  <h2>Most backtests lie by leaving out the costs.</h2>
  <p className="first-run-sub">
   This one charges funding, fees, spread and slippage on every fill, then tells you in one
   sentence whether what is left is an edge or a rounding error.
  </p>
  <button className="primary run-button" onClick={onSample}>Run the sample backtest</button>
  <p className="first-run-note">JTO / USDT · January 2025 · no setup, no download</p>
  <dl className="first-run-claims">
   {CLAIMS.map(([term,detail])=><div key={term}><dt>{term}</dt><dd>{detail}</dd></div>)}
  </dl>
 </section>;
}
