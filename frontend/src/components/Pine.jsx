import React,{useState,useEffect} from 'react';
import {ArrowUpRight,FileUp,Pencil,Plus,ScanSearch} from 'lucide-react';
import {api} from '../api';

const EXAMPLE=`//@version=6
strategy("EMA pullback with ATR stop", overlay = true)

fastLen = input.int(20, "Fast EMA")
slowLen = input.int(50, "Slow EMA")
atrMult = input.float(2.0, "Stop, in ATR")
reward  = input.float(2.0, "Target, in R")

fast = ta.ema(close, fastLen)
slow = ta.ema(close, slowLen)
atr  = ta.atr(14)

longSetup  = fast > slow and low <= fast and close > fast and close > open
shortSetup = fast < slow and high >= fast and close < fast and close < open

if longSetup and strategy.position_size == 0
    stop = close - atrMult * atr
    strategy.entry("Long", strategy.long)
    strategy.exit("Long exit", "Long", stop = stop, limit = close + reward * (close - stop))

if shortSetup and strategy.position_size == 0
    stop = close + atrMult * atr
    strategy.entry("Short", strategy.short)
    strategy.exit("Short exit", "Short", stop = stop, limit = close - reward * (stop - close))
`;

const where=x=>{const at=x.line?[x.line]:x.lines||[];return at.length?`Line${at.length>1?'s':''} ${at.slice(0,6).join(', ')}${at.length>6?' and more':''}`:''};
const count=(n,one,many)=>`${n.toLocaleString()} ${n===1?one:many}`;

function Fact({mark,tone,at,children}){return <li><span className={`fact-mark ${tone||''}`}>{mark}</span><span>{at&&<b>{at} · </b>}{children}</span></li>}

function Report({r}){
 const d=r.dry_run,la=r.look_ahead;
 return <ul className="facts">
  {r.refused.map((x,i)=><Fact key={'r'+i} mark="refused" tone="fact-problem" at={where(x)}>{x.text}</Fact>)}
  {r.approximated.map((x,i)=><Fact key={'a'+i} mark="differs" tone="fact-caution" at={where(x)}>{x.text}</Fact>)}
  {r.ignored.map((x,i)=><Fact key={'i'+i} mark="skipped" tone="fact-info" at={where(x)}>{x.text}</Fact>)}
  {r.entries.length>0&&<Fact mark="orders"><b>Entries</b> {r.entries.map(e=>`${e.id} (${e.direction})`).join(', ')}.{r.exits.length>0?<> <b>Exits</b> {r.exits.join('; ')}.</>:' No exit of its own: a position ends at its stop or when the script reverses.'}</Fact>}
  {r.inputs.length>0&&<Fact mark="inputs">{r.inputs.length===1?'One input, used at its default':`${r.inputs.length} inputs, used at their defaults`}: {r.inputs.map(x=>`${x.title||x.kind} = ${x.default}`).join(', ')}. To test another value, change it in the script and add the script again: it is saved as a variant, and its trials are counted with the original.</Fact>}
  {r.indicators.length>0&&<Fact mark="uses">{r.indicators.join(', ')}</Fact>}
  {d&&<Fact mark="trial run">On {d.bars.toLocaleString()} synthetic candles the script placed {count(d.long_entries,'long entry','long entries')} and {count(d.short_entries,'short entry','short entries')}, {count(d.signal_exits,'signal exit','signal exits')}, and moved a stop or target {count(d.moved_levels,'time','times')}. This only shows that it runs; it says nothing about performance.</Fact>}
  {la&&<Fact mark="causality" tone={la.ok?'gate-pass':'fact-problem'}>{la.ok?`No look-ahead: the orders stayed the same when later candles were added (${la.signals_checked.toLocaleString()} comparisons).`:'Signals changed when later candles were added.'}</Fact>}
 </ul>;
}

export default function PineView({onUse,onError,refreshMeta}){
 const [source,setSource]=useState(''),[report,setReport]=useState(null),[busy,setBusy]=useState(false),[name,setName]=useState(''),[hypothesis,setHypothesis]=useState(''),[parent,setParent]=useState(null),[saved,setSaved]=useState([]),[added,setAdded]=useState(null);
 const refresh=()=>api('/pine').then(setSaved).catch(e=>onError(e.message));
 useEffect(()=>{refresh()},[]);
 const edit=text=>{setSource(text);setReport(null);setAdded(null)};
 async function check(){setBusy(true);setAdded(null);try{const r=await api('/pine/check',{source});setReport(r);if(r.title&&!name)setName(r.title)}catch(e){onError(e.message)}finally{setBusy(false)}}
 async function add(){setBusy(true);try{const s=await api('/pine',{source,name:name.trim()||null,hypothesis,parent});setAdded(s);setParent(s.key);await Promise.all([refresh(),refreshMeta()])}catch(e){onError(e.message)}finally{setBusy(false)}}
 async function open(key){try{const s=await api('/pine/'+key);edit(s.source);setName(s.name);setParent(s.key);document.querySelector('main')?.scrollTo?.({top:0})}catch(e){onError(e.message)}}
 async function file(e){const f=e.target.files[0];if(f){edit(await f.text());setParent(null);setName('')}e.target.value=''}
 const known=saved.find(s=>s.key===parent);
 return <><div className="page-heading"><div><h1>Bring your own script.</h1><p>Paste a TradingView strategy. Same engine, same costs, no future candles.</p></div></div>
  <section className="panel padded idea"><h2>Pine Script · v5 or v6</h2><p>Paste a <b>strategy()</b> script from the Pine Editor. It is run one completed candle at a time, so it cannot read ahead, and its orders go through the same fills, fees, funding and liquidation as every other strategy here. Nothing leaves this computer.</p>
   <label className="field"><span>Script</span><textarea className="code" aria-label="Pine script" rows={18} spellCheck={false} value={source} onChange={e=>edit(e.target.value)} placeholder={'//@version=6\nstrategy("My strategy")\n…'}/></label>
   <div className="pine-actions"><button className="primary" disabled={busy||!source.trim()} onClick={check}><ScanSearch size={15}/>{busy?'Checking…':'Check script'}</button><label className="button"><FileUp size={15}/>Open a file<input type="file" accept=".pine,.txt,text/plain" hidden onChange={file}/></label>{!source.trim()&&<button className="text-button" onClick={()=>{edit(EXAMPLE);setParent(null);setName('')}}>Load an example</button>}</div>
   {known&&<p className="hint">Opened from <b>{known.name}</b>. If you change its logic, adding it saves a variant of that script: both stay in the Library and their trials are counted together.</p>}
  </section>
  {report&&<section className="panel"><div className="panel-title"><h2>{report.ok?'Ready to test':'Cannot be tested yet'}</h2><small>{report.version?`Pine v${report.version}`:'No version line'}{report.title?` · ${report.title}`:''}</small></div>
   <Report r={report}/>
   {report.ok&&!added&&<div className="actions"><label className="field"><span>Name</span><input aria-label="Strategy name" maxLength={80} value={name} onChange={e=>setName(e.target.value)}/></label>
    <label className="field grow"><span>Hypothesis · why this should make money, written before the test</span><input aria-label="Hypothesis" maxLength={2000} value={hypothesis} onChange={e=>setHypothesis(e.target.value)}/></label>
    <button className="primary" disabled={busy} onClick={add}><Plus size={15}/>Add to strategies</button></div>}
   {added&&<div className="actions"><p className="pine-added">{added.existing?'Already saved':'Saved'} as <b>{added.name}</b> <small>{added.key}</small></p><button className="primary" onClick={()=>onUse(added.key,added.name)}><ArrowUpRight size={15}/>Use in a backtest</button></div>}
   {!report.ok&&<p className="hint padded-small">Nothing is approximated silently: a script is tested as written or not at all. Change the lines above in TradingView, check that the script still does what you intend there, and paste it again.</p>}
  </section>}
  <section className="panel"><div className="panel-title"><h2>Saved scripts</h2>{saved.length>0&&<small>{saved.length} saved</small>}</div><div className="table-scroll"><table><thead><tr><th>Script</th><th>Pine</th><th>Added</th><th>Variant of</th><th>Edit</th><th>Use</th></tr></thead>
   <tbody>{saved.map(s=><tr key={s.key}><td>{s.name}<small>{s.key}</small></td><td>v{s.version}</td><td>{s.created.slice(0,16).replace('T',' ')}</td><td>{s.parent||'—'}</td>
    <td><button aria-label={'Edit '+s.name} onClick={()=>open(s.key)}><Pencil size={15}/></button></td><td><button aria-label={'Use '+s.name} onClick={()=>onUse(s.key,s.name)}><ArrowUpRight size={16}/></button></td></tr>)}</tbody></table>
   {!saved.length&&<p className="empty-table">Scripts you add appear here and in the Strategy list in Settings.</p>}</div>
   <p className="hint padded-small">A backtest of a script is still in-sample. For held-out evidence, select the script, open Compare and run an expanding walk-forward with a single candidate, for example {'{"stop_atr":[1.5]}'}: each training period then only decides whether the script trades the next one. A saved script is never deleted; archive it in the Library when it is done.</p></section></>;
}
