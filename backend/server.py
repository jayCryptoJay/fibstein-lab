"""Local-only API, cancellable jobs, persistent results and presets."""
import csv,gzip,io,json,sqlite3,threading,uuid,hashlib,ipaddress
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor
from fastapi import FastAPI,HTTPException,Request
from fastapi.responses import FileResponse,Response,JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel,Field
from .config import Config,PAIRS,STRATEGIES
from .data import ROOT,inventory,download_archive,download_ccxt,import_csv,utc
from .engine import run_backtest
from .experiments import experiment

STATE=ROOT/'workspace'; STATE.mkdir(exist_ok=True)
RESULTS=STATE/'runs'; RESULTS.mkdir(exist_ok=True)
DB=STATE/'lab.sqlite3'
with sqlite3.connect(DB) as db:
    db.execute('CREATE TABLE IF NOT EXISTS presets (name TEXT PRIMARY KEY, config TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, created TEXT NOT NULL, kind TEXT NOT NULL, config TEXT NOT NULL, metrics TEXT NOT NULL)')
    for preset_path in sorted((ROOT/'presets').glob('*.json')):
        preset_config=Config.model_validate_json(preset_path.read_text()).model_dump(mode='json')
        db.execute('INSERT OR IGNORE INTO presets VALUES (?,?)',(preset_path.stem.replace('-',' ').title(),json.dumps(preset_config)))
JOBS={}; POOL=ThreadPoolExecutor(max_workers=1); LOCK=threading.Lock()
app=FastAPI(title='FibStein Lab',version='1.0.0',docs_url='/api/docs')

@app.middleware('http')
async def local_write_guard(request:Request,call_next):
    host=request.url.hostname or ''
    if host!='localhost':
        try: local=ipaddress.ip_address(host).is_private or ipaddress.ip_address(host).is_loopback
        except ValueError: local=False
        if not local: return JSONResponse({'detail':'This service accepts local IP hosts only.'},status_code=400)
    if request.method in ('POST','PUT','PATCH','DELETE'):
        origin=request.headers.get('origin'); host=request.url.hostname
        if origin and urlparse(origin).hostname!=host: return JSONResponse({'detail':'Cross-origin writes are disabled.'},status_code=403)
        if int(request.headers.get('content-length','0'))>30_000_000: return JSONResponse({'detail':'Upload exceeds 30 MB.'},status_code=413)
        if not request.headers.get('content-type','').startswith('application/json'): return JSONResponse({'detail':'JSON request required.'},status_code=415)
    return await call_next(request)

def persist_result(result,kind):
    rid=uuid.uuid4().hex[:16]; created=datetime.now(timezone.utc).isoformat()
    result.update({'id':rid,'created':created,'kind':kind,'engine_version':'1.0.0'})
    source=b''.join((ROOT/'backend'/x).read_bytes() for x in ['engine.py','strategies.py','config.py','data.py','experiments.py'])
    if (ROOT/'custom_strategies.py').exists(): source+=(ROOT/'custom_strategies.py').read_bytes()
    digest=hashlib.sha256(source).hexdigest()
    result['engine_sha256']=digest
    with gzip.open(RESULTS/f'{rid}.json.gz','wt') as f: json.dump(result,f,allow_nan=False)
    with sqlite3.connect(DB) as db: db.execute('INSERT INTO runs VALUES (?,?,?,?,?)',(rid,created,kind,json.dumps(result['config']),json.dumps(result.get('metrics',{}))))
    return rid

def launch(fn):
    jid=uuid.uuid4().hex[:16]
    with LOCK:
        if any(j['status'] in ('queued','running') for j in JOBS.values()): raise HTTPException(409,'A job is already running. Wait or cancel it first.')
        JOBS[jid]={'id':jid,'status':'queued','message':'Queued','cancel':False}
    def work():
        job=JOBS[jid]; job['status']='running'
        try:
            result=fn(lambda m:job.update(message=m),lambda:job['cancel'])
            job.update(status='done',result=result,message='Complete')
        except Exception as e: job.update(status='cancelled' if job['cancel'] else 'error',message=str(e))
    POOL.submit(work); return {'job_id':jid}

@app.get('/api/meta')
def meta():
    return {'pairs':PAIRS,'strategies':STRATEGIES,'defaults':Config().model_dump(mode='json'),
            'sample':Config(start='2025-01-15',end='2025-02-01').model_dump(mode='json'),'version':'1.0.0'}

@app.get('/api/data')
def datasets(): return inventory()

class RunRequest(BaseModel):
    config:Config
    mode:str='backtest'
    grid:dict|None=None
    folds:int=Field(3,ge=1,le=5)
    min_trades:int=Field(20,ge=1,le=10000)

@app.post('/api/run')
def run(body:RunRequest):
    if body.mode not in ('backtest','compare','stress','grid','walkforward'): raise HTTPException(400,'Unknown experiment mode.')
    def execute(progress,cancel):
        result=run_backtest(body.config,progress,cancel) if body.mode=='backtest' else experiment(body.config,body.mode,body.grid,body.folds,body.min_trades,progress,cancel)
        return {'run_id':persist_result(result,body.mode)}
    return launch(execute)

@app.post('/api/download')
def download(config:Config):
    if config.source=='csv': raise HTTPException(400,'Use CSV import for this source.')
    def execute(progress,cancel):
        import pandas as pd
        warmup=max(config.htf_ema*4*1.5/24,config.ema_slow*config.timeframe*2/1440,3)
        begin=(utc(config.start)-pd.Timedelta(days=warmup)).date()
        for pair in config.pairs:
            if config.source=='binance_archive': download_archive(pair,begin,config.end,progress,cancel)
            else: download_ccxt(pair,config.source,begin,config.end,progress,cancel)
        return {'datasets':inventory()}
    return launch(execute)

@app.get('/api/jobs/{jid}')
def job(jid:str):
    if jid not in JOBS: raise HTTPException(404,'Job not found; the server may have restarted.')
    return {k:v for k,v in JOBS[jid].items() if k!='cancel'}

@app.post('/api/jobs/{jid}/cancel')
def cancel(jid:str):
    if jid not in JOBS: raise HTTPException(404,'Job not found.')
    JOBS[jid]['cancel']=True
    return {'message':'Cancellation requested; a current data request may take up to 25 seconds to finish.'}

@app.get('/api/runs')
def runs():
    with sqlite3.connect(DB) as db: rows=db.execute('SELECT * FROM runs ORDER BY created DESC LIMIT 100').fetchall()
    return [{'id':r[0],'created':r[1],'kind':r[2],'config':json.loads(r[3]),'metrics':json.loads(r[4])} for r in rows]

def read_run(rid):
    if len(rid)!=16 or any(x not in '0123456789abcdef' for x in rid): raise HTTPException(400,'Invalid run ID.')
    path=RESULTS/f'{rid}.json.gz'
    if not path.exists(): raise HTTPException(404,'Run not found.')
    with gzip.open(path,'rt') as f: return json.load(f)

@app.get('/api/runs/{rid}')
def result(rid:str):
    r=read_run(rid)
    # Display-only sampling; full-resolution calculations/exports are preserved.
    for key in ['equity']:
        series=r.get(key,[])
        if len(series)>3000:
            stride=max(1,len(series)//3000); r[key]=series[::stride]
            if r[key][-1]!=series[-1]: r[key].append(series[-1])
    for p,series in r.get('price',{}).items(): r['price'][p]=series[::max(1,len(series)//3000)]
    return r

@app.get('/api/runs/{rid}/export/{kind}')
def export(rid:str,kind:str):
    r=read_run(rid)
    if kind=='json': return Response(json.dumps(r,indent=2),media_type='application/json',headers={'Content-Disposition':f'attachment; filename="fibstein-{rid}.json"'})
    if kind not in ('trades','equity'): raise HTTPException(400,'Choose json, trades, or equity.')
    rows=r.get(kind,[]); output=io.StringIO()
    if rows:
        w=csv.DictWriter(output,fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    return Response(output.getvalue(),media_type='text/csv',headers={'Content-Disposition':f'attachment; filename="fibstein-{rid}-{kind}.csv"'})

@app.get('/api/presets')
def presets():
    with sqlite3.connect(DB) as db: rows=db.execute('SELECT * FROM presets ORDER BY name').fetchall()
    return [{'name':n,'config':json.loads(c)} for n,c in rows]

class Preset(BaseModel):
    name:str=Field(min_length=1,max_length=80)
    config:Config

@app.post('/api/presets')
def save_preset(preset:Preset):
    with sqlite3.connect(DB) as db: db.execute('INSERT OR REPLACE INTO presets VALUES (?,?)',(preset.name,json.dumps(preset.config.model_dump(mode='json'))))
    return {'name':preset.name}

@app.post('/api/validate')
def validate(c:Config): return c.model_dump(mode='json')

class ImportRequest(BaseModel):
    pair:str=Field(pattern=r'^[A-Z0-9]{2,20}USDT$')
    candles:str=Field(max_length=25_000_000)
    funding:str|None=Field(None,max_length=2_000_000)

@app.post('/api/import')
def import_data(body:ImportRequest):
    if any(j['status'] in ('queued','running') for j in JOBS.values()): raise HTTPException(409,'Finish the running job before changing its data.')
    try: return import_csv(body.pair,body.candles,body.funding)
    except Exception as e: raise HTTPException(400,str(e)) from e

if (ROOT/'custom_strategies.py').exists():
    import importlib.util
    spec=importlib.util.spec_from_file_location('custom_strategies',ROOT/'custom_strategies.py')
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)

if (ROOT/'static').exists(): app.mount('/',StaticFiles(directory=ROOT/'static',html=True),name='ui')
