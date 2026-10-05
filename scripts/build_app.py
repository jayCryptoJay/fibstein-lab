"""Build the one-folder app for this operating system, try it, and pack it.

    python -m pip install -r requirements-build.txt
    python scripts/build_app.py            # add --no-sample to skip the bundled candles, --no-test to skip the trial start

Output: dist/FibStein-Lab-<system>-<machine>.zip (.tar.gz on Linux) and its .sha256.
The archive holds one folder. Research data never lives in it: see backend/paths.py.
"""
import argparse, hashlib, json, os, platform, shutil, subprocess, sys, tempfile, time, urllib.request
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT/'build'; DIST = ROOT/'dist'; NAME = 'FibStein Lab'
SAMPLE_MONTH = ('2025-01-01', '2025-02-01')     # The window the first-run screen and the sample presets use.
PORT = 8799
SCRIPT = '//@version=6\nstrategy("Build check")\nif ta.crossover(ta.ema(close, 5), ta.ema(close, 20))\n    strategy.entry("L", strategy.long)\nif ta.crossunder(ta.ema(close, 5), ta.ema(close, 20))\n    strategy.close("L")\n'


def say(text): print(f'[build] {text}', flush=True)


def sample():
    """Fetch the sample month with the app's own checksum-verified downloader. Candles are never stored in git."""
    home = BUILD/'seed'; code = ('import sys; from datetime import date; from backend.config import PAIRS; from backend.data import download_archive\n'
                                 f'for pair in PAIRS: download_archive(pair, date.fromisoformat("{SAMPLE_MONTH[0]}"), date.fromisoformat("{SAMPLE_MONTH[1]}"), lambda m: None)\n')
    done = subprocess.run([sys.executable, '-c', code], cwd=ROOT, env={**os.environ, 'FIBSTEIN_HOME': str(home)})
    cache = home/'data'/'binance_archive'
    if done.returncode or not cache.is_dir(): return False
    shutil.copytree(cache, BUILD/'sample-data'/'binance_archive'); return True


def licenses():
    """The app carries its dependencies, so it carries their licence texts."""
    out = BUILD/'licenses'; out.mkdir(parents=True); skip = {'pip', 'setuptools', 'wheel', 'pyinstaller', 'pyinstaller-hooks-contrib', 'altgraph', 'pytest'}
    for dist in sorted(metadata.distributions(), key=lambda d: (d.metadata['Name'] or '').lower()):
        name = dist.metadata['Name']
        if not name or name.lower() in skip: continue
        texts = []
        for f in dist.files or []:
            if f.name.upper().startswith(('LICENSE', 'LICENCE', 'COPYING', 'NOTICE')):
                try: texts.append(f.locate().read_text(encoding='utf-8', errors='replace'))
                except OSError: pass
        body = '\n\n'.join(texts) or f'Licence as declared by the package: {dist.metadata.get("License-Expression") or dist.metadata.get("License") or "see the project page"}'
        (out/f'{name}-{dist.version}.txt').write_text(f'{name} {dist.version}\n{dist.metadata.get("Home-page") or ""}\n\n{body}\n', encoding='utf-8')
    return len(list(out.iterdir()))


def api(path, body=None):
    request = urllib.request.Request(f'http://127.0.0.1:{PORT}/api{path}', data=None if body is None else json.dumps(body).encode(),
                                     headers={'Content-Type': 'application/json'} if body is not None else {})
    with urllib.request.urlopen(request, timeout=60) as response: return json.loads(response.read())


def trial(folder, with_sample):
    """Start the built app the way a user would and make it do real work before anything is shipped."""
    sys.path.insert(0, str(ROOT))
    from backend import registry
    exe = folder/(NAME + ('.exe' if os.name == 'nt' else ''))
    with tempfile.TemporaryDirectory() as home:
        app = subprocess.Popen([str(exe), '--no-browser', '--port', str(PORT)], env={**os.environ, 'FIBSTEIN_HOME': home}, stdin=subprocess.DEVNULL)
        try:
            started = time.time(); meta = None
            while meta is None:
                if app.poll() is not None: raise SystemExit(f'The built app exited with code {app.returncode} before answering.')
                if time.time() - started > 180: raise SystemExit('The built app did not answer within 180 seconds.')
                try: meta = api('/meta')
                except OSError: time.sleep(1)
            say(f'app answered after {time.time() - started:.0f}s')
            assert meta['installed'] and Path(meta['home']).resolve() == Path(home).resolve(), 'the app is not using the per-user folder'
            # Same sources, same fingerprint: evidence produced by the installed app and by a checkout must agree.
            assert meta['engine_sha256'] == registry.engine_fingerprint(ROOT), 'engine fingerprint differs from the source tree'
            report = api('/pine/check', {'source': SCRIPT}); assert report['ok'] and report['look_ahead']['ok'], report['refused']
            assert len(api('/presets')) >= 1 and (Path(home)/'workspace'/'lab.sqlite3').exists()
            if with_sample:
                assert (Path(home)/'data'/'binance_archive').is_dir(), 'sample candles were not copied to the user folder'
                job = api('/run', {'config': meta['sample'], 'mode': 'backtest'})['job_id']
                while True:
                    state = api(f'/jobs/{job}')
                    if state['status'] in ('error', 'cancelled'): raise SystemExit(f'Sample backtest failed: {state["message"]}')
                    if state['status'] == 'done': break
                    time.sleep(1)
                result = api(f'/runs/{state["result"]["run_id"]}'); assert result['metrics']['trade_count'] > 0 and result['engine_sha256'] == meta['engine_sha256']
                say(f'sample backtest: {result["metrics"]["trade_count"]} trades')
        finally:
            app.terminate()
            try: app.wait(20)
            except subprocess.TimeoutExpired: app.kill()


def pack(folder):
    system = {'Windows': 'windows', 'Darwin': 'macos', 'Linux': 'linux'}[platform.system()]; stem = DIST/f'FibStein-Lab-{system}-{platform.machine().lower()}'
    if system == 'macos':     # ditto keeps the links and permissions a macOS bundle relies on.
        archive = stem.with_name(stem.name + '.zip'); subprocess.check_call(['ditto', '-c', '-k', '--keepParent', str(folder), str(archive)])
    elif system == 'linux': archive = Path(shutil.make_archive(str(stem), 'gztar', DIST, NAME))
    else: archive = Path(shutil.make_archive(str(stem), 'zip', DIST, NAME))
    digest = hashlib.sha256(archive.read_bytes()).hexdigest(); Path(str(archive) + '.sha256').write_text(f'{digest}  {archive.name}\n')
    return archive, digest


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0]); p.add_argument('--no-sample', action='store_true'); p.add_argument('--no-test', action='store_true')
    p.add_argument('--require-sample', action='store_true', help='Fail instead of building without the bundled candles.')
    a = p.parse_args()
    for folder in (BUILD, DIST): shutil.rmtree(folder, ignore_errors=True)
    BUILD.mkdir()
    if not (ROOT/'static'/'index.html').exists(): raise SystemExit('Built dashboard missing. In frontend/, run npm ci and npm run build.')
    with_sample = not a.no_sample and sample()
    if not with_sample and a.require_sample: raise SystemExit('Sample candles could not be fetched and verified.')
    say('sample candles bundled' if with_sample else 'building WITHOUT sample candles: the first-run sample will need a download')
    say(f'{licenses()} dependency licence files collected')
    subprocess.check_call([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--distpath', str(DIST), '--workpath', str(BUILD/'pyinstaller'), str(ROOT/'packaging'/'fibstein-lab.spec')], cwd=ROOT)
    folder = DIST/NAME
    if not a.no_test: trial(folder, with_sample)
    archive, digest = pack(folder)
    say(f'{archive.name}  {archive.stat().st_size / 1e6:.0f} MB  sha256 {digest}')


if __name__ == '__main__': main()
