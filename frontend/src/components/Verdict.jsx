import React from 'react';
import {fmt} from '../api';

// Wraps bare numbers in the prose so figures read as instrument data, not copy.
const NUM=/(-?[\d,]+\.?\d*\s?(?:%|USDT|×|trades|folds)?)/g;
function Prose({text}){
 return <>{text.split(NUM).map((part,i)=>i%2?<b key={i}>{part}</b>:part)}</>;
}

function CostRail({raw,costs,net}){
 if(!raw&&!costs) return null;
 const losing=raw<=0;
 const total=Math.abs(raw)+(losing?costs:0);
 const kept=Math.max(0,Math.abs(net));
 const costWidth=Math.min(100,costs/Math.max(total,1e-9)*100);
 return <div className="cost-rail">
  <div className="rail" role="img" aria-label={`Costs took ${fmt(costs)} of ${fmt(Math.abs(raw))}`}>
   <i className={losing?'rail-loss':'rail-kept'} style={{width:`${100-costWidth}%`}}/>
   <i className="rail-cost" style={{width:`${costWidth}%`}}/>
  </div>
  <div className="rail-legend">
   <span>Signal <b>{fmt(raw)}</b></span>
   <span>Costs <b>{fmt(-costs)}</b></span>
   <span>{losing?'Total loss':'You keep'} <b>{fmt(net)}</b></span>
  </div>
 </div>;
}

export default function Verdict({v,onExplain}){
 if(!v) return null;
 return <section className={`verdict tone-${v.tone}`}>
  <div className="verdict-tag"><span className="mark">φ</span>{v.evidence}<small>{v.trades} trades</small></div>
  <h2 className="verdict-headline"><Prose text={v.headline}/></h2>
  <p className="verdict-detail"><Prose text={v.detail}/></p>
  <CostRail raw={v.raw_pnl} costs={v.costs} net={v.net_pnl}/>
  {onExplain&&<button className="text-button" onClick={onExplain}>What these numbers mean</button>}
 </section>;
}
