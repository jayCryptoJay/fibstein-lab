import React,{useState,useEffect} from 'react';
import {Archive,ArrowUpRight,Plus,RotateCcw,ArrowUp} from 'lucide-react';
import {api,fmt} from '../api';

const words=s=>s.replaceAll('_',' ');
const day=t=>t.slice(0,16).replace('T',' ');

function Chip({status}){return <span className={`chip status-${status}`}>{status}</span>}

// Shared with the results page: the same fixed-rule facts an AI reads from the run card.
export function Findings({runId}){
 const [card,setCard]=useState(null);
 useEffect(()=>{let live=true;setCard(null);if(runId)api(`/runs/${runId}/card`).then(k=>live&&setCard(k)).catch(()=>{});return()=>{live=false}},[runId]);
 if(!card?.findings?.length) return null;
 const s=card.selection;
 return <section className="panel"><div className="panel-title"><h2>What the numbers say</h2>
  <div className="exports">{s&&<small>Best of {s.lineage_trials} configurations tried</small>}<a href={`/api/runs/${runId}/digest`} target="_blank" rel="noreferrer">Digest</a><a href={`/api/runs/${runId}/card`} target="_blank" rel="noreferrer">Run card</a></div></div>
  <ul className="facts">{card.findings.map(f=><li key={f.id}><span className={`fact-mark fact-${f.severity}`}>{f.severity}</span><span>{f.text}</span></li>)}</ul>
 </section>;
}

function Detail({k,reasons,onChange,onError,onLoad}){
 const [d,setD]=useState(null),[hypothesis,setHypothesis]=useState(''),[reason,setReason]=useState(''),[text,setText]=useState('');
 const show=x=>{setD(x);setHypothesis(x.hypothesis);setText('')};
 useEffect(()=>{setD(null);api('/library/'+k).then(show).catch(e=>onError(e.message))},[k]);
 if(!d) return null;
 const act=async body=>{try{show(await api('/library/'+k,body));onChange()}catch(e){onError(e.message)}};
 const g=d.gates,next=d.status==='draft'?'candidate':'promoted';
 const ready=d.status==='draft'?g.candidate_ready:g.promotion_ready;
 return <>
  <section className="panel"><div className="panel-title"><h2>{d.name} <small>· {d.key}{d.parent&&` · child of ${d.parent}`} · {d.origin}</small></h2><Chip status={d.status}/></div>
   <div className="padded idea"><label className="field"><span>Hypothesis · why this should make money, written before the test</span><textarea aria-label="Hypothesis" rows={3} value={hypothesis} onChange={e=>setHypothesis(e.target.value)} placeholder="None recorded yet."/></label>
    <button disabled={hypothesis.trim()===d.hypothesis||!hypothesis.trim()} onClick={()=>act({action:'hypothesis',text:hypothesis})}>Save hypothesis</button>
    {d.archive_reason&&<p className="hint">Archived: {words(d.archive_reason)}. {reasons[d.archive_reason]} Its trials are kept and still count.</p>}</div></section>
  <section className="panel"><div className="panel-title"><h2>Gates</h2><small>{g.evidence_run?`Evidence: latest held-out run ${g.evidence_run.slice(0,8)}`:'No held-out evidence yet'}</small></div>
   <ul className="facts">{g.gates.map(x=><li key={x.id}><span className={`fact-mark gate-${x.status}`}>{x.status}</span><span><b>{words(x.id)}</b> · needed for {x.needed_for}<br/>{x.detail}</span></li>)}</ul>
   <div className="actions">
    {d.status==='archived'?<button onClick={()=>act({action:'reopen',text})}><RotateCcw size={15}/>Reopen as draft</button>:<>
     {d.status!=='promoted'&&<button className="primary" disabled={!ready} onClick={()=>act({action:'promote'})}><ArrowUp size={15}/>Move to {next}</button>}
     <label className="field"><span>Archive reason</span><select aria-label="Archive reason" value={reason} onChange={e=>setReason(e.target.value)}><option value="">Choose a reason</option>{Object.keys(reasons).map(r=><option key={r} value={r}>{words(r)}</option>)}</select></label>
     <button disabled={!reason} onClick={()=>act({action:'archive',reason,text})}><Archive size={15}/>Archive</button></>}
    <label className="field grow"><span>Note · kept in the journal</span><input aria-label="Note" value={text} onChange={e=>setText(e.target.value)}/></label>
    <button disabled={!text.trim()} onClick={()=>act({action:'note',text})}>Add note</button>
   </div>
   {!ready&&d.status!=='archived'&&d.status!=='promoted'&&<p className="hint padded-small">A gate cannot be overridden. {d.status==='candidate'?'Promotion waits for the overfitting checks and the locked final holdout.':'Run a walk-forward on this strategy to produce held-out evidence.'}</p>}
  </section>
  <section className="panel"><div className="panel-title"><h2>Runs</h2>{d.studies.length>0&&<small>{d.studies.length} most recent</small>}</div><div className="table-scroll"><table><thead><tr><th>Run</th><th>Kind</th><th>Trades</th><th>Net return</th><th>Read</th><th>Open</th></tr></thead>
   <tbody>{d.studies.map(s=><tr key={s.id}><td>{s.id.slice(0,8)}<small>{day(s.created)}</small></td><td>{s.kind}<small>{s.held_out?'held out':'in-sample'}</small></td><td>{s.metrics.trade_count??'—'}</td><td>{s.metrics.net_return_pct===undefined?'—':fmt(s.metrics.net_return_pct)+'%'}</td><td><a href={`/api/runs/${s.id}/digest`} target="_blank" rel="noreferrer">Digest</a></td><td><button aria-label={'Open run '+s.id} onClick={()=>onLoad(s.id)}><ArrowUpRight size={16}/></button></td></tr>)}</tbody></table>
   {!d.studies.length&&<p className="empty-table">No runs yet. Select this strategy in Settings and run it.</p>}</div></section>
  <section className="panel"><div className="panel-title"><h2>Journal</h2></div><ul className="facts">{d.journal.map((e,i)=><li key={i}><span className="fact-mark">{e.created.slice(0,10)}</span><span><b>{e.kind}</b> · {e.detail}</span></li>)}</ul></section>
 </>;
}

export default function LibraryView({onLoad,onError}){
 const [lib,setLib]=useState(null),[chosen,setChosen]=useState(null),[filter,setFilter]=useState('all'),[idea,setIdea]=useState({key:'',name:'',hypothesis:'',parent:'',origin:'python'});
 const refresh=()=>api('/library').then(setLib).catch(e=>onError(e.message));
 useEffect(()=>{refresh()},[]);
 if(!lib) return null;
 const rows=lib.strategies.filter(s=>filter==='all'||s.status===filter);
 const count=s=>lib.strategies.filter(x=>x.status===s).length;
 async function add(){try{await api('/library',{...idea,name:idea.name||null,parent:idea.parent||null});setChosen(idea.key);setIdea({key:'',name:'',hypothesis:'',parent:'',origin:'python'});refresh()}catch(e){onError(e.message)}}
 return <><div className="page-heading"><div><h1>Keep what holds.</h1><p>Every idea, every trial, and the reason each one was shelved.</p></div></div>
  <div className="metric-strip status-strip">{lib.statuses.map(s=><div key={s}><span>{s[0].toUpperCase()+s.slice(1)}</span><strong>{count(s)}</strong></div>)}</div>
  <section className="panel"><div className="panel-title"><h2>Strategies</h2><select aria-label="Filter by status" value={filter} onChange={e=>setFilter(e.target.value)}><option value="all">All statuses</option>{lib.statuses.map(s=><option key={s}>{s}</option>)}</select></div>
   <div className="table-scroll"><table className="library-table"><thead><tr><th>Strategy</th><th>Status</th><th>Tried</th><th>Latest held-out</th><th>Hypothesis</th></tr></thead>
   <tbody>{rows.map(s=><tr key={s.key} className={'selectable '+(chosen===s.key?'selected':'')} onClick={()=>setChosen(s.key)}>
    <td><button className="text-button" onClick={()=>setChosen(s.key)}>{s.name}</button><small>{s.key} · {s.origin}{s.parent&&` · child of ${s.parent}`}</small></td>
    <td><Chip status={s.status}/>{s.archive_reason&&<small>{words(s.archive_reason)}</small>}</td>
    <td>{s.trials}<small>{s.lineage_trials} in lineage</small></td>
    <td className={s.held_out?(s.held_out.net_return_pct>=0?'positive':'negative'):''}>{s.held_out?fmt(s.held_out.net_return_pct)+'%':'—'}{s.held_out&&<small>{s.held_out.trades} trades</small>}</td>
    <td className="wrap">{s.hypothesis||<span className="muted">None recorded</span>}</td></tr>)}</tbody></table>
   {!rows.length&&<p className="empty-table">{lib.strategies.length?'Nothing with this status.':'Strategies appear here after their first run, or when you record an idea below.'}</p>}</div>
   <p className="hint padded-small">Tried counts distinct configurations, including every candidate in a search. Rerunning the same settings does not add one; archiving never removes one.</p></section>
  {chosen&&<Detail key={chosen} k={chosen} reasons={lib.reasons} onChange={refresh} onError={onError} onLoad={onLoad}/>}
  <section className="panel padded idea"><h2>Record an idea</h2><p>Write the hypothesis before the first test. A variant of an existing strategy should name its parent, so trials are counted across the whole lineage.</p>
   <div className="experiment-controls"><label className="field"><span>Key · matches the registered strategy</span><input aria-label="Strategy key" value={idea.key} placeholder="pullback_long_only" onChange={e=>setIdea({...idea,key:e.target.value.toLowerCase().replace(/[^a-z0-9_]/g,'_')})}/></label>
    <label className="field"><span>Name · optional</span><input aria-label="Strategy name" value={idea.name} onChange={e=>setIdea({...idea,name:e.target.value})}/></label>
    <label className="field"><span>Parent</span><select aria-label="Parent strategy" value={idea.parent} onChange={e=>setIdea({...idea,parent:e.target.value})}><option value="">None · a new idea</option>{lib.strategies.map(s=><option key={s.key} value={s.key}>{s.name}</option>)}</select></label>
    <label className="field"><span>Origin</span><select aria-label="Origin" value={idea.origin} onChange={e=>setIdea({...idea,origin:e.target.value})}>{lib.origins.map(o=><option key={o} value={o}>{{python:'Python',pine:'Pine Script conversion',ai:'AI proposal'}[o]||o}</option>)}</select></label></div>
   <label className="field"><span>Hypothesis</span><textarea aria-label="New hypothesis" rows={3} value={idea.hypothesis} onChange={e=>setIdea({...idea,hypothesis:e.target.value})}/></label>
   <button className="primary" disabled={!/^[a-z][a-z0-9_]{1,39}$/.test(idea.key)} onClick={add}><Plus size={15}/>Record idea</button></section></>;
}
