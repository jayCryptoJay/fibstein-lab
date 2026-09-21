#!/usr/bin/env bash
# Turns any source file into a loop file the streamer can push with -c copy.
#
# Doing this once, offline, is what lets the 24/7 stream run at near-zero CPU:
# ffmpeg then only remuxes. The alternative — encoding live, forever — is the
# expensive path, and on a 2-OCPU Ampere instance it is a tight fit at 1080p.
set -euo pipefail

self_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
root=${LOOPSTREAM_ROOT:-$(dirname -- "$self_dir")}
env_file=${LOOPSTREAM_ENV:-$root/stream.env}

# shellcheck source=common.sh
. "$self_dir/common.sh"
load_env "$env_file"

: "${VIDEO:=$root/media/loop.mp4}"
: "${VIDEO_BITRATE_K:=4500}"
: "${AUDIO_BITRATE_K:=128}"
: "${WIDTH:=1920}"
: "${HEIGHT:=1080}"
: "${FPS:=30}"

src=${1:-}
dest=${2:-$VIDEO}

if [[ -z $src ]]; then
  cat >&2 <<USAGE
usage: prepare-video.sh SOURCE [DEST]

Re-encodes SOURCE into DEST (default: $VIDEO) as H.264/AAC in yuv420p at
${WIDTH}x${HEIGHT}, ${FPS}fps, with a keyframe every two seconds. Silent audio
is added when SOURCE has none, because a live ingest needs an audio track.
USAGE
  exit 2
fi

[[ -f $src ]] || { echo "prepare-video: source not found: $src" >&2; exit 1; }
command -v ffmpeg >/dev/null 2>&1 || { echo "prepare-video: ffmpeg is not installed" >&2; exit 1; }

# ffmpeg -y would happily open the destination for writing while still reading
# it, and the result is a truncated file rather than an error.
if [[ -e $dest ]] && [[ $(readlink -f -- "$src") == $(readlink -f -- "$dest") ]]; then
  echo "prepare-video: source and destination are the same file ($dest)." >&2
  echo "prepare-video: pass the original video as SOURCE, not the loop file." >&2
  exit 1
fi

mkdir -p -- "$(dirname -- "$dest")"

has_audio=$(ffprobe -v error -select_streams a -show_entries stream=codec_type \
  -of default=nw=1:nk=1 "$src" 2>/dev/null | head -1)

args=(-hide_banner -y -i "$src")

if [[ -z $has_audio ]]; then
  echo "prepare-video: source has no audio track, adding silence"
  # -shortest is safe here and required: anullsrc never ends on its own.
  args+=(-f lavfi -i "anullsrc=channel_layout=stereo:sample_rate=44100" -shortest)
  args+=(-map 0:v:0 -map 1:a:0)
else
  args+=(-map 0:v:0 -map 0:a:0)
fi

args+=(
  -vf "scale=$WIDTH:$HEIGHT:force_original_aspect_ratio=decrease,pad=$WIDTH:$HEIGHT:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=$FPS,format=yuv420p"
  -c:v libx264
  -preset medium
  -b:v "${VIDEO_BITRATE_K}k"
  -maxrate "${VIDEO_BITRATE_K}k"
  -bufsize "$((VIDEO_BITRATE_K * 2))k"
  -g "$((FPS * 2))"
  -keyint_min "$((FPS * 2))"
  -sc_threshold 0
  -c:a aac
  -b:a "${AUDIO_BITRATE_K}k"
  -ar 44100
  -ac 2
  -movflags +faststart
  "$dest"
)

echo "prepare-video: $src -> $dest"
echo "prepare-video: this runs faster than real time but is not instant; leave it going."
ffmpeg "${args[@]}"

duration=$(ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 "$dest" 2>/dev/null)
printf 'prepare-video: done — %s, %.0fs, %s\n' \
  "$dest" "${duration:-0}" "$(du -h -- "$dest" | cut -f1)"
echo "prepare-video: restart the stream to pick it up — streamctl restart"
