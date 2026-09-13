import io
import numpy as np
import pandas as pd
import pytest
from backend.config import Config
from backend import data,experiments
from backend.engine import metrics

def day():
    idx=pd.date_range('2025-01-01',periods=1440,freq='1min',tz='UTC')
    return pd.DataFrame({'timestamp':idx,'open':100.,'high':101.,'low':99.,'close':100.,'volume':10000.})

def cache(tmp_path,monkeypatch,frame):
    monkeypatch.setattr(data,'DATA',tmp_path)
    folder=tmp_path/'csv'/'JTOUSDT';folder.mkdir(parents=True)
    frame.to_csv(folder/'2025-01.csv.gz',index=False,compression='gzip')
    return folder

def cfg(**kw): return Config(start='2025-01-01',end='2025-01-02',source='csv',**kw)

def test_missing_candle_blocks_run(tmp_path,monkeypatch):
    f=day().drop(index=123);cache(tmp_path,monkeypatch,f)
    with pytest.raises(ValueError,match='1 missing minutes'): data.load_pair('JTOUSDT',cfg(funding_mode='off'))

def test_missing_historical_funding_blocks_run(tmp_path,monkeypatch):
    cache(tmp_path,monkeypatch,day())
    with pytest.raises(ValueError,match='historical funding missing'): data.load_pair('JTOUSDT',cfg())

def test_estimate_is_explicit_and_scheduled(tmp_path,monkeypatch):
    cache(tmp_path,monkeypatch,day());bundle=data.load_pair('JTOUSDT',cfg(funding_mode='estimate',funding_bps=2))
    assert len(bundle[1])==3;assert set(bundle[1].values())=={.0002}

def test_historical_funding_gap_rejected(tmp_path,monkeypatch):
    folder=cache(tmp_path,monkeypatch,day())
    pd.DataFrame({'timestamp':['2025-01-01T00:00:00Z'],'rate':[.0001]}).to_csv(folder/'2025-01.funding.csv',index=False)
    with pytest.raises(ValueError,match='funding coverage has a gap'): data.load_pair('JTOUSDT',cfg())

def test_complete_historical_funding_loaded(tmp_path,monkeypatch):
    folder=cache(tmp_path,monkeypatch,day())
    pd.DataFrame({'timestamp':pd.date_range('2025-01-01',periods=3,freq='8h',tz='UTC'),'rate':[.0001,-.0002,.0003]}).to_csv(folder/'2025-01.funding.csv',index=False)
    b=data.load_pair('JTOUSDT',cfg());assert len(b[1])==3;assert b[4]['missing_minutes']==0

@pytest.mark.parametrize('unit,scale',[('s',1),('ms',1000),('us',1000000),('ns',1000000000)])
def test_epoch_unit_detection(unit,scale):
    f=day();f['timestamp']=(f.timestamp.astype('int64')//10**9)*scale
    result=data.normalize(f);assert result.index[0]==pd.Timestamp('2025-01-01',tz='UTC')

def test_data_hash_changes_with_prices(tmp_path,monkeypatch):
    folder=cache(tmp_path,monkeypatch,day());a=data.load_pair('JTOUSDT',cfg(funding_mode='off'))[4]['sha256']
    f=day();f.loc[10,'close']=100.1;f.to_csv(folder/'2025-01.csv.gz',index=False,compression='gzip')
    b=data.load_pair('JTOUSDT',cfg(funding_mode='off'))[4]['sha256'];assert a!=b

def test_walkforward_selects_training_winner_only(monkeypatch):
    calls=[]
    monkeypatch.setattr(experiments,'load_pair',lambda p,c:None)
    initial=pd.Timestamp('2025-01-01').date()
    def fake(c,bundles,**kw):
        calls.append(c)
        train=c.start==initial
        # Stop=2 wins training; stop=1 would win testing. Selection must stay 2.
        delta=(20 if c.stop_atr==2 else 10) if train else (-5 if c.stop_atr==2 else 50)
        eq=[{'timestamp':pd.Timestamp(c.start).value//10**6,'equity':c.balance}, {'timestamp':pd.Timestamp(c.end).value//10**6,'equity':c.balance+delta}]
        m=metrics([],eq,c.balance,0);m['trade_count']=50
        return {'metrics':m,'equity':eq,'trades':[]}
    monkeypatch.setattr(experiments,'simulate',fake)
    c=Config(start='2025-01-01',end='2025-02-01')
    r=experiments.experiment(c,'walkforward',{'stop_atr':[1,2]},folds=2,min_trades=20)
    assert all(f['selected']['stop_atr']==2 for f in r['folds'])
    assert r['folds'][0]['test_end']==r['folds'][1]['test_start']
    assert r['metrics']['final_equity']==990
    assert len(calls)==6

def test_grid_bound_checked(monkeypatch):
    monkeypatch.setattr(experiments,'load_pair',lambda p,c:None)
    with pytest.raises(ValueError,match='Maximum 12'): experiments.experiment(Config(),'grid',{'stop_atr':[1]*13})

def test_short_validation_window_rejected(monkeypatch):
    monkeypatch.setattr(experiments,'load_pair',lambda p,c:None)
    with pytest.raises(ValueError,match='at least 10 days'): experiments.experiment(Config(start='2025-01-01',end='2025-01-03'),'grid')

def test_invalid_funding_import_does_not_write_candles(tmp_path,monkeypatch):
    monkeypatch.setattr(data,'DATA',tmp_path)
    with pytest.raises(ValueError): data.import_csv('JTOUSDT',day().to_csv(index=False),'timestamp,wrong\n2025-01-01T00:00:00Z,0.0001\n')
    assert not list(tmp_path.rglob('*.csv.gz'))

def test_csv_import_roundtrip(tmp_path,monkeypatch):
    monkeypatch.setattr(data,'DATA',tmp_path)
    funding=pd.DataFrame({'timestamp':pd.date_range('2025-01-01',periods=3,freq='8h',tz='UTC'),'rate':[.0001,.0001,.0001]})
    out=data.import_csv('JTOUSDT',day().to_csv(index=False),funding.to_csv(index=False))
    b=data.load_pair('JTOUSDT',cfg());assert out['rows']==1440;assert b[4]['funding_events']==3
