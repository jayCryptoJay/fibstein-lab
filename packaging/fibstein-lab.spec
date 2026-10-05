# PyInstaller recipe for the one-folder app. Run through scripts/build_app.py, which prepares what it bundles.
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).parent
BUILD = ROOT/'build'
datas = [(str(ROOT/'static'), 'static'), (str(ROOT/'presets'), 'presets'), (str(ROOT/'docs'), 'docs'), (str(ROOT/'LICENSE'), '.'), (str(ROOT/'README.md'), '.')]
# The engine fingerprint is a hash of these sources, so they travel with the app as files, not only as bytecode.
datas += [(str(p), 'backend') for p in sorted((ROOT/'backend').glob('*.py'))]
for folder, target in ((BUILD/'sample-data', 'sample-data'), (BUILD/'licenses', 'licenses')):
    if folder.is_dir(): datas.append((str(folder), target))

a = Analysis([str(ROOT/'launch.py')], pathex=[str(ROOT)], datas=datas,
             hiddenimports=['backend.server', *collect_submodules('uvicorn')],
             excludes=['tkinter', 'matplotlib', 'IPython', 'pytest', 'PyInstaller'], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='FibStein Lab', console=True, upx=False)
COLLECT(exe, a.binaries, a.datas, upx=False, name='FibStein Lab')
