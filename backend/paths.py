"""Where the program's own files are, and where the user's research is kept.

From a source checkout both are the project folder, exactly as before. As an
installed app the program sits in a bundle that every update replaces, so the
workspace (runs, registry, saved scripts) and the candle cache live in a per-user
folder instead. FIBSTEIN_HOME moves that folder anywhere, for either kind of install.
"""
import os, shutil, sys
from pathlib import Path

FROZEN = bool(getattr(sys, 'frozen', False))
APP = Path(getattr(sys, '_MEIPASS', None) or Path(__file__).resolve().parents[1]).resolve()


def user_folder():
    if sys.platform == 'win32': return Path(os.environ.get('LOCALAPPDATA') or Path.home()/'AppData'/'Local')/'FibStein Lab'
    if sys.platform == 'darwin': return Path.home()/'Library'/'Application Support'/'FibStein Lab'
    return Path(os.environ.get('XDG_DATA_HOME') or Path.home()/'.local'/'share')/'fibstein-lab'


def resolve(env=None, frozen=None):
    env = os.environ if env is None else env
    if env.get('FIBSTEIN_HOME'): return Path(env['FIBSTEIN_HOME']).expanduser().resolve()
    return user_folder() if (FROZEN if frozen is None else frozen) else APP


HOME = resolve()
WORKSPACE = HOME/'workspace'
SAMPLE = APP/'sample-data'     # Verified sample candles a release build carries; never present in a source checkout.


def seed(home=None, sample=None):
    """Copy the bundled sample candles into an empty data folder. Existing data is never touched."""
    target = Path(home or HOME)/'data'; source = Path(sample or SAMPLE)
    if not source.is_dir() or (target.exists() and any(target.iterdir())): return False
    target.parent.mkdir(parents=True, exist_ok=True); shutil.copytree(source, target, dirs_exist_ok=True); return True


if HOME != APP:
    # data.py keeps its cache beside the program. Point it at the user's folder without changing how it reads or validates.
    from . import data
    data.DATA = HOME/'data'
    seed()
