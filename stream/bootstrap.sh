#!/usr/bin/env bash
# One-shot installer for a fresh Ubuntu instance. Idempotent: re-run it after
# pulling new scripts. It never overwrites an existing stream.env, so re-running
# will not lose your stream key.
set -euo pipefail

src=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
root=${LOOPSTREAM_ROOT:-$HOME/loopstream}
unit=loopstream
user=$(id -un)

say() { printf '\n== %s\n' "$*"; }

[[ -d $src/bin ]] || { echo "bootstrap: run this from the stream/ directory" >&2; exit 1; }
command -v apt-get >/dev/null 2>&1 || { echo "bootstrap: this expects Ubuntu/Debian" >&2; exit 1; }
command -v systemctl >/dev/null 2>&1 || { echo "bootstrap: this expects systemd" >&2; exit 1; }

if [[ $user == root ]]; then
  echo "bootstrap: warning — running as root. On Oracle's Ubuntu image, log in"
  echo "bootstrap: as 'ubuntu' instead so the stream does not run as root."
fi

say "Installing packages"
if command -v ffmpeg >/dev/null 2>&1 && command -v tmux >/dev/null 2>&1; then
  echo "ffmpeg and tmux already present, skipping apt"
else
  sudo DEBIAN_FRONTEND=noninteractive apt-get update
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y ffmpeg tmux
fi
ffmpeg -version | head -1

say "Installing to $root"
mkdir -p -- "$root/bin" "$root/media"
install -m 755 -- "$src/bin/stream.sh" "$src/bin/prepare-video.sh" "$src/bin/streamctl" "$root/bin/"
install -m 644 -- "$src/bin/common.sh" "$root/bin/"
echo "scripts installed"

if [[ -f $root/stream.env ]]; then
  echo "stream.env already exists, leaving it alone"
else
  sed "s|__ROOT__|$root|g" -- "$src/stream.env.example" > "$root/stream.env"
  chmod 600 -- "$root/stream.env"
  echo "stream.env created (mode 600)"
fi

say "Installing the $unit service"
sed -e "s|__ROOT__|$root|g" -e "s|__USER__|$user|g" -- "$src/systemd/$unit.service" |
  sudo tee "/etc/systemd/system/$unit.service" >/dev/null
sudo systemctl daemon-reload
# Enable but do not start: there is no stream key and no video yet.
sudo systemctl enable "$unit" >/dev/null
echo "service installed and enabled at boot (not started)"

sudo ln -sfn -- "$root/bin/streamctl" /usr/local/bin/streamctl
echo "streamctl is on your PATH"

cat <<NEXT

== Installed. Three things left:

  1. streamctl video /path/to/your-video.mp4
       Builds the loop file. Run it on the file you uploaded.

  2. streamctl key
       Paste the key from YouTube Studio: Go Live -> Stream -> Stream key.

  3. streamctl start
       Then: streamctl status

No inbound ports are needed — the server connects out to YouTube, nothing
connects in. Leave the Oracle security list alone apart from SSH.
NEXT
