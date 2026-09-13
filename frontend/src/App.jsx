import React,{useState,useEffect} from 'react';
import {Folder,Play,LoaderCircle,X,SlidersHorizontal} from 'lucide-react';
import {api} from './api';
import Settings from './components/Settings';
import Results from './components/Results';
import {DataView,CompareView,Methodology} from './components/Views';

export default function App(){
 const [meta,setMeta]=useState(null),[c,setC]=useState(null),[tab,setTab]=useState('Backtest'),[result,setResult]=useState(null),[experiment,setExperiment]=useState(null),[error,setError]=useState(''),[notice,setNotice]=useState(''),[job,setJob]=useState(null),[runs,setRuns]=useState([]),[datasets,setDatasets]=useState([]),[presets,setPresets]=useState([]),[settingsOpen,setSettingsOpen]=useState(false);
 const set=(key,value)=>setC(old=>({...old,[key]:value}));
 const refreshRuns=()=>api('/runs').then(setRuns);
 const refreshData=()=>api('/data').then(setDatasets);
 const refreshPresets=()=>api('/presets').then(setPresets);
 useEffect(()=>{Promise.all([api('/meta'),api('/runs'),api('/data'),api('/presets')]).then(([m,r,d,p])=>{setMeta(m);setC(m.defaults);setRuns(r);setDatasets(d);setPresets(p)}).catch(e=>setError(e.message))},[]);
 useEffect(()=>{if(!job?.id)return;let cancelled=false;const poll=async()=>{try{const j=await api('/jobs/'+job.id);if(cancelled)return;if(['error','cancelled'].includes(j.status)){setError(j.message);setJob(null)}else if(j.status==='done'){setJob(null);if(j.result?.run_id)await load(j.result.run_id);else{await refreshData();setNotice('Download complete. Cached data is ready to validate in a backtest.')}await refreshRuns()}else setJob(old=>old?{...old,...j}:old)}catch(e){if(!cancelled){setError(e.message);setJob(null)}}};const timer=setInterval(poll,900);poll();return()=>{cancelled=true;clearInterval(timer)}},[job?.id]);
 async function load(id){try{const r=await api('/runs/'+id);if(r.kind==='backtest'){setResult(r);setTab('Backtest')}else{setExperiment(r);setTab('Compare')}}catch(e){setError(e.message)}}
 async function run(mode='backtest',options={}){if(job){setError('A job is already running.');return}setError('');setNotice('');try{const j=await api('/run',{config:c,mode,...options});setJob({id:j.job_id,status:'queued',message:'Preparing data…'});setSettingsOpen(false)}catch(e){setError(e.message)}}
 async function download(){setError('');try{const j=await api('/download',c);setJob({id:j.job_id,status:'queued',message:'Preparing download…'})}catch(e){setError(e.message)}}
 function sample(){setC({...meta.sample,pairs:c?.pairs||meta.sample.pairs});setNotice('Bundled sample selected: January 15–31, 2025, with January 1–14 for warmup. Press Run backtest.');setTab('Backtest')}
 async function onConfig(config){try{setC(await api('/validate',config));setNotice('Settings loaded. Existing results still refer to their recorded configuration.')}catch(e){setError(e.message)}}
 if(!meta)return <div className="loading"><h1>FIBSTEIN / LAB</h1><p>{error||'Opening your workspace…'}</p>{error&&<p>Start the Python server, then reload this page.</p>}</div>;
 const stale=result&&JSON.stringify(result.config)!==JSON.stringify(c);
 return <><header className="app-header"><a className="brand" href="#" onClick={e=>{e.preventDefault();setTab('Backtest')}}>FIBSTEIN <span>/ LAB</span></a><nav aria-label="Main navigation">{['Backtest','Compare','Data','Methodology'].map(t=><button className={tab===t?'active':''} key={t} onClick={()=>setTab(t)}>{t}</button>)}</nav><span className="workspace-label"><Folder size={16}/>Local workspace</span><button className="mobile-settings" onClick={()=>setSettingsOpen(!settingsOpen)}><SlidersHorizontal size={18}/>Settings</button></header>
 <div className={'app-layout '+(settingsOpen?'settings-open':'')}><Settings c={c} set={set} meta={meta} onConfig={onConfig} onError={setError} presets={presets} refreshPresets={refreshPresets}/><main>
 {error&&<div role="alert" className="message error"><div>{error}</div><button aria-label="Dismiss error" onClick={()=>setError('')}><X size={16}/></button></div>}{notice&&<div role="status" className="message"><div>{notice}</div><button aria-label="Dismiss notice" onClick={()=>setNotice('')}><X size={16}/></button></div>}
 {job&&<div role="status" className="job-status"><LoaderCircle size={17} className="spin"/><span>{job.message}</span><button onClick={()=>api('/jobs/'+job.id+'/cancel',{}).then(r=>setNotice(r.message)).catch(e=>setError(e.message))}>Cancel</button></div>}
 {tab==='Backtest'&&<><div className="page-heading"><div><h1>Test the edge.</h1><p>Perpetual futures. Real costs. Clear assumptions.</p></div><button className="primary run-button" disabled={!!job} onClick={()=>run()}><Play size={17} fill="currentColor"/>{job?'Working…':'Run backtest'}</button></div>{result&&<div className="result-context"><span>{result.config.pairs.join(' · ')} · {result.config.strategy.replaceAll('_',' ')} · {result.config.start} → {result.config.end} · {result.config.source}</span>{stale&&<b>Settings changed · rerun to apply</b>}</div>}<Results result={result} onSample={sample}/></>}
 {tab==='Data'&&<DataView c={c} set={set} datasets={datasets} onDownload={download} onSample={sample} onError={setError} refresh={refreshData}/>}
 {tab==='Compare'&&<CompareView c={c} onRun={run} result={experiment} runs={runs} onLoad={load} onSample={sample} onError={setError}/>}
 {tab==='Methodology'&&<Methodology/>}
 </main></div><footer><span>Research tool · Results depend on data quality</span><span>FIBSTEIN / LAB <small>v1.0.0</small></span></footer></>
}
