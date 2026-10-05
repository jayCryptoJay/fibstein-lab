import json,os,subprocess,sys
from pathlib import Path
from backend import paths,registry

ROOT=Path(__file__).resolve().parents[1]

def python(code,home):
    out=subprocess.run([sys.executable,'-c',code],cwd=ROOT,env={**os.environ,'FIBSTEIN_HOME':str(home)},capture_output=True,text=True)
    assert out.returncode==0,out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])

def test_a_source_checkout_keeps_research_in_the_project_and_an_installed_app_does_not(tmp_path):
    assert paths.resolve({},frozen=False)==paths.APP==ROOT
    installed=paths.resolve({},frozen=True); assert installed==paths.user_folder() and ROOT not in installed.parents and installed!=ROOT
    assert paths.resolve({'FIBSTEIN_HOME':str(tmp_path)},frozen=False)==paths.resolve({'FIBSTEIN_HOME':str(tmp_path)},frozen=True)==tmp_path.resolve()

def test_sample_candles_are_copied_once_and_never_over_existing_data(tmp_path):
    sample=tmp_path/'sample'; (sample/'binance_archive'/'JTOUSDT').mkdir(parents=True); (sample/'binance_archive'/'JTOUSDT'/'2025-01.csv.gz').write_bytes(b'x')
    assert not paths.seed(tmp_path/'none',tmp_path/'missing')
    home=tmp_path/'home'; assert paths.seed(home,sample) and (home/'data'/'binance_archive'/'JTOUSDT'/'2025-01.csv.gz').read_bytes()==b'x'
    (home/'data'/'binance_archive'/'JTOUSDT'/'2025-01.csv.gz').write_bytes(b'mine'); assert not paths.seed(home,sample)
    assert (home/'data'/'binance_archive'/'JTOUSDT'/'2025-01.csv.gz').read_bytes()==b'mine'

def test_fibstein_home_moves_the_workspace_the_cache_and_custom_strategies(tmp_path):
    (tmp_path/'custom_strategies.py').write_text("import pandas as pd\nfrom backend.strategies import register_strategy\nregister_strategy('home_made','Home made','Never trades.',lambda f,c: pd.Series(0,index=f.index))\n")
    out=python("import json; from backend import data,registry,lab,server,paths; from backend.config import STRATEGIES\n"
               "print(json.dumps({'data':str(data.DATA),'db':str(registry.DB),'lab':str(lab.WORKSPACE),'state':str(server.STATE),'pine':str(server.PINE),"
               "'app':str(paths.APP),'custom':'home_made' in STRATEGIES,'engine':registry.engine_fingerprint(),'presets':len(server.presets())}))",tmp_path)
    home=tmp_path.resolve()
    assert Path(out['data'])==home/'data' and Path(out['db'])==home/'workspace'/'lab.sqlite3' and Path(out['lab'])==Path(out['state'])==home/'workspace'
    assert Path(out['pine'])==home/'workspace'/'pine' and (home/'workspace'/'lab.sqlite3').exists() and Path(out['app'])==ROOT
    # The program's own files still come from the program: presets load, and the user's strategy file is part of the fingerprint.
    assert out['custom'] and out['presets']>=1
    if not (ROOT/'custom_strategies.py').exists(): assert out['engine']!=registry.engine_fingerprint(ROOT)

def test_the_app_reports_one_version_everywhere():
    import json,backend
    from backend import server
    assert server.meta()["version"]==backend.__version__==server.app.version
    assert json.loads((ROOT/"frontend"/"package.json").read_text())["version"]==backend.__version__
