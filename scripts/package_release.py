"""Build a reproducible distributable without environments, secrets or QA state."""
from pathlib import Path
import json,zipfile,hashlib

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT.parent/'FibStein-Lab.zip'
notices=['THIRD-PARTY NOTICES\nOriginal source: MIT. Market data: Binance public archive, provider terms apply.\nPython dependencies are installed separately; consult each installed package license.\n']
for p in sorted((ROOT/'frontend'/'node_modules').rglob('*')):
    if p.is_file() and p.name.lower() in ('license','license.txt','license.md','licence','copying','notice','notice.txt'):
        notices.append('\n\n--- '+str(p.relative_to(ROOT/'frontend'/'node_modules'))+' ---\n'+p.read_text(errors='replace'))
(ROOT/'THIRD_PARTY_NOTICES.txt').write_text(''.join(notices))
allowed={'backend','frontend','static','presets','docs','scripts','tests','data'}
excluded={'node_modules','.venv','__pycache__','.pytest_cache','archive','workspace'}
files=[]
for p in sorted(ROOT.rglob('*')):
    if not p.is_file(): continue
    rel=p.relative_to(ROOT)
    if any(x in excluded for x in rel.parts) or p.suffix in {'.pyc','.part'}: continue
    if len(rel.parts)>1 and rel.parts[0] not in allowed: continue
    files.append(p)
manifest={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
with zipfile.ZipFile(OUT,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
    for p in files: z.write(p,'fibstein-lab/'+str(p.relative_to(ROOT)))
    z.writestr('fibstein-lab/SHA256SUMS.json',json.dumps(manifest,indent=2))
with zipfile.ZipFile(OUT) as z:
    assert z.testzip() is None
    for name,digest in manifest.items(): assert hashlib.sha256(z.read('fibstein-lab/'+name)).hexdigest()==digest
print(json.dumps({'path':str(OUT),'files':len(files)+1,'bytes':OUT.stat().st_size,'sha256':hashlib.sha256(OUT.read_bytes()).hexdigest()},indent=2))
