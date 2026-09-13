import numpy as np
import pandas as pd
import pytest
from backend.data import aggregate, normalize
from backend import data
import gzip

@pytest.mark.parametrize('minutes',[5,15,30,60,240,1440])
def test_complete_utc_bars_preserve_ohlcv(minutes):
    n=2880
    values=np.arange(n,dtype=float)+100
    source=normalize(pd.DataFrame({'timestamp':pd.date_range('2024-02-28',periods=n,freq='1min',tz='UTC'),
        'open':values,'high':values+2,'low':values-1,'close':values+1,'volume':np.ones(n)}))
    out=aggregate(source,minutes)
    assert len(out)==n//minutes
    assert out.iloc[0].to_dict()=={'open':100.,'high':100+minutes+1.,'low':99.,'close':100+minutes,'volume':float(minutes)}
    assert out.volume.sum()==source.volume.sum()
    assert out.index[0]==source.index[0]
    assert len(aggregate(source.drop(source.index[1]),minutes))==len(out)-1

def test_interrupted_archive_cache_is_rebuilt(tmp_path,monkeypatch):
    monkeypatch.setattr(data,'DATA',tmp_path)
    idx=pd.date_range('2025-01-01',periods=1440,freq='1min',tz='UTC')
    frame=normalize(pd.DataFrame({'timestamp':idx,'open':100.,'high':101.,'low':99.,'close':100.,'volume':1.}))
    path=data.month_cache('JTOUSDT','binance_archive','2025-01')
    path.parent.mkdir(parents=True)
    path.write_bytes(gzip.compress(frame.to_csv().encode())[:-8])
    monkeypatch.setattr(data,'archive',lambda *args:b'verified archive fixture')
    monkeypatch.setattr(data,'read_archive_candles',lambda raw:frame)
    monkeypatch.setattr(data,'read_archive_funding',lambda raw:pd.DataFrame({'timestamp':[idx[0]],'rate':[0.0001]}))
    data.download_archive('JTOUSDT','2025-01-01','2025-01-02')
    assert len(normalize(pd.read_csv(path)))==1440
    assert not path.with_suffix('.part').exists()
