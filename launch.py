"""One-command launcher. From source it installs free dependencies in this project's own venv; as an installed app it just starts."""
from pathlib import Path
import argparse,hashlib,os,subprocess,sys,time,urllib.request,venv,webbrowser,threading

FROZEN=bool(getattr(sys,'frozen',False))
ROOT=Path(getattr(sys,'_MEIPASS',None) or Path(__file__).resolve().parent)

def running(url):
    try:
        with urllib.request.urlopen(url+'/api/meta',timeout=1): return True
    except OSError: return False

def main():
    parser=argparse.ArgumentParser(description='Start FibStein Lab locally.')
    parser.add_argument('--lan',action='store_true',help='Expose on your trusted local network; no authentication. Never port-forward this server.')
    parser.add_argument('--port',type=int,default=8765)
    parser.add_argument('--no-browser',action='store_true')
    parser.add_argument('--ready',action='store_true',help=argparse.SUPPRESS)
    args=parser.parse_args()
    if sys.version_info<(3,11): raise SystemExit('Python 3.11 or newer is required. Python 3.12 is recommended.')
    url=f'http://127.0.0.1:{args.port}'
    # A second double-click should open the app that is already running, not fail on the busy port.
    if not args.ready and running(url):
        print(f'FibStein Lab is already running: {url}',flush=True)
        if not args.no_browser: webbrowser.open(url)
        return
    if not 1024<=args.port<=65535: raise SystemExit('Use a port between 1024 and 65535.')
    os.chdir(ROOT)
    if not (ROOT/'static'/'index.html').exists(): raise SystemExit('Built dashboard missing. In frontend/, run npm ci and npm run build.')
    if not args.ready and not FROZEN:
        env=ROOT/'.venv'; python=env/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
        if not python.exists():
            print('Creating a private Python environment…',flush=True)
            venv.EnvBuilder(with_pip=True).create(env)
        digest=hashlib.sha256((ROOT/'requirements.txt').read_bytes()).hexdigest()
        marker=env/'fibstein-requirements.sha256'
        if not marker.exists() or marker.read_text()!=digest:
            print('Installing free, open-source dependencies. This is normally needed only once.',flush=True)
            subprocess.check_call([str(python),'-m','pip','install','-r',str(ROOT/'requirements.txt')])
            marker.write_text(digest)
        raise SystemExit(subprocess.call([str(python),str(Path(__file__)),*sys.argv[1:],'--ready']))
    import uvicorn
    from backend.server import app
    from backend import paths
    print(f'\nFibStein Lab: {url}\nKeep this window open. Ctrl+C stops the app.\nYour runs, saved scripts and candle cache are kept in: {paths.HOME}\n',flush=True)
    if args.lan: print('LAN MODE: Anyone on the reachable network can access your local app. Use trusted Wi-Fi only; never expose it to the public internet.',flush=True)
    if not args.no_browser:
        def open_when_ready():
            for _ in range(150):
                if running(url): webbrowser.open(url);return
                time.sleep(.2)
        threading.Thread(target=open_when_ready,daemon=True).start()
    uvicorn.run(app,host='0.0.0.0' if args.lan else '127.0.0.1',port=args.port,log_level='warning')

if __name__=='__main__':
    try: main()
    except (Exception,SystemExit) as e:
        # Started by a double-click, the window would close before the reason could be read.
        if not FROZEN or (isinstance(e,SystemExit) and not isinstance(e.code,str)): raise
        print(f'\nFibStein Lab could not start: {e}',flush=True); input('Press Enter to close this window.')
