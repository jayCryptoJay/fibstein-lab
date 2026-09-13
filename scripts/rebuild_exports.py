"""Rebuild normalized caches and derived exports from verified local archives."""
from pathlib import Path
import sys, json, hashlib
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.data import DATA, archive, read_archive_candles, aggregate, save_candles
from scripts.expand_data import FRAMES

if __name__=='__main__':
    report=json.loads((DATA/'coverage-2024-2025.json').read_text())
    for record in report['months']:
        pair,stamp=record['pair'],record['month']
        frame=read_archive_candles(archive(pair,'klines','monthly',stamp))
        path=DATA/'binance_archive'/pair/f'{stamp}.csv.gz'
        save_candles(frame,path)
        record['source_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
        for label,minutes in FRAMES.items():
            out=aggregate(frame,minutes)
            save_candles(out,DATA/'timeframes'/pair/label/f'{stamp}.csv.gz')
        print(f'REBUILT {pair} {stamp}',flush=True)
    (DATA/'coverage-2024-2025.json').write_text(json.dumps(report,indent=2))
