import gzip,json
import numpy as np
import pandas as pd
import pytest
from backend import lab,registry,strategies
from backend.causality import prefix_invariant
from backend.config import Config
from backend.engine import metrics

def saved(workspace,rid,kind='backtest',**kw):
    m=metrics([],[{'timestamp':0,'equity':1000.},{'timestamp':1,'equity':1050.}],1000,0)
    r={'id':rid,'kind':kind,'created':'2025-03-01T00:00:00+00:00','engine_sha256':'e'*64,'warnings':['Modelled assumption.'],
       'config':Config(start='2025-01-01',end='2025-02-01',**kw).model_dump(mode='json'),'metrics':{**m,'trade_count':0},'trades':[]}
    (workspace/'runs').mkdir(parents=True,exist_ok=True)
    with gzip.open(workspace/'runs'/f'{rid}.json.gz','wt') as f: json.dump(r,f)
    return r

@pytest.fixture
def workspace(tmp_path,monkeypatch):
    monkeypatch.setattr(lab,'WORKSPACE',tmp_path); return tmp_path

def test_card_and_digest_for_a_saved_run(workspace,capsys):
    saved(workspace,'ab'+'0'*14)
    lab.main(['card','ab']); k=json.loads(capsys.readouterr().out)
    assert k['schema']=='fibstein.runcard/1' and k['run']['id']=='ab'+'0'*14 and k['warnings']==['Modelled assumption.']
    assert k['strategy']['status']=='draft' and k['selection']['label']=='best of 1'   # Recorded on first read.
    lab.main(['digest','latest']); assert capsys.readouterr().out.startswith('# Run ab')

def test_ambiguous_or_missing_run_is_an_error(workspace):
    with pytest.raises(SystemExit,match='No saved runs'): lab.main(['card','latest'])
    saved(workspace,'ab'+'0'*14); saved(workspace,'ab'+'1'*14)
    with pytest.raises(SystemExit,match='matches 2 saved runs'): lab.main(['card','ab'])

def test_library_archive_and_journal_roundtrip(workspace,capsys):
    saved(workspace,'ab'+'0'*14); lab.main(['sync'])
    lab.main(['strategy','add','pullback_long','--parent','trend_pullback','--origin','pine','--hypothesis','Longs only in uptrends.'])
    lab.main(['archive','pullback_long','--reason','superseded','--note','Replaced.'])
    with pytest.raises(SystemExit,match='held_out_evidence'): lab.main(['promote','trend_pullback'])
    capsys.readouterr(); lab.main(['library','--json']); rows={r['key']:r for r in json.loads(capsys.readouterr().out)}
    assert rows['pullback_long']['status']=='archived' and rows['pullback_long']['archive_reason']=='superseded'
    assert rows['trend_pullback']['trials']==1 and rows['pullback_long']['lineage_trials']==1
    lab.main(['journal']); text=capsys.readouterr().out
    assert 'Longs only in uptrends.' in text and 'archived (superseded' in text

@pytest.mark.parametrize('strategy',['trend_pullback','breakout_retest','vwap_reversion'])
def test_built_in_strategies_pass_the_look_ahead_check(strategy):
    out=prefix_invariant(strategy); assert out['ok'] and out['conclusive'] and out['signals_checked']>0

def test_look_ahead_is_caught(monkeypatch):
    # Buys when the NEXT bar closes higher: profitable, and impossible.
    peek=lambda f,c: pd.Series(np.where(f.close.shift(-1)>f.close,1,0),index=f.index)
    monkeypatch.setitem(strategies.REGISTRY,'peek',peek)
    out=prefix_invariant('peek'); assert not out['ok'] and out['mismatches']

def test_silent_strategy_is_inconclusive(monkeypatch):
    monkeypatch.setitem(strategies.REGISTRY,'silent',lambda f,c: pd.Series(0,index=f.index))
    out=prefix_invariant('silent'); assert out['ok'] and not out['conclusive']
