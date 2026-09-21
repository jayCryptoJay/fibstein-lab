#!/usr/bin/env bash
# Pushes VIDEO to a live RTMP/RTMPS ingest on an endless loop.
#
# Two supervision layers, because they fail differently: systemd restarts this
# script if the machine or the script dies, and the loop below restarts ffmpeg
# if the stream drops. The second case is the common one — a transient network
# blip or an ingest hiccup kills ffmpeg but leaves everything else healthy, and
# a systemd-level restart would be a slower, noisier way to recover from it.
set -uo pipefail

self_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
root=${LOOPSTREAM_ROOT:-$(dirname -- "$self_dir")}
env_file=${LOOPSTREAM_ENV:-$root/stream.env}

# systemd supplies these through EnvironmentFile; sourcing covers running this
# by hand in tmux, where nothing has populated the environment.
# shellcheck source=common.sh
. "$self_dir/common.sh"
load_env "$env_file"

: "${STREAM_KEY:=}"
: "${INGEST_URL:=rtmps://a.rtmps.youtube.com:443/live2}"
: "${VIDEO:=$root/media/loop.mp4}"
: "${MODE:=copy}"
: "${VIDEO_BITRATE_K:=4500}"
: "${AUDIO_BITRATE_K:=128}"
: "${WIDTH:=1920}"
: "${HEIGHT:=1080}"
: "${FPS:=30}"
: "${MIN_RUN_SECONDS:=60}"
: "${MAX_BACKOFF_SECONDS:=300}"

log() { printf '%s loopstream: %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*"; }
die() { log "error: $*" >&2; exit 1; }

# Everything ffmpeg writes passes through here. ffmpeg echoes the full output
# URL in several of its error messages, and that URL contains the stream key —
# without this the key ends up in the journal, which is world-readable on a
# default Ubuntu image.
redact() {
  if [[ -n $STREAM_KEY ]]; then
    sed -u "s|$STREAM_KEY|<stream-key>|g"
  else
    cat
  fi
}

preflight() {
  command -v ffmpeg >/dev/null 2>&1 || die "ffmpeg is not installed. Run bootstrap.sh."
  command -v ffprobe >/dev/null 2>&1 || die "ffprobe is not installed. Run bootstrap.sh."

  [[ -n $STREAM_KEY ]] || die "STREAM_KEY is empty. Set it with: streamctl key"
  # Also guarantees the key is safe to use unescaped as a sed pattern in redact().
  [[ $STREAM_KEY =~ ^[A-Za-z0-9_-]{8,}$ ]] ||
    die "STREAM_KEY does not look like a stream key (expected letters, digits and dashes). Re-copy it and run: streamctl key"

  [[ -f $VIDEO ]] || die "video not found: $VIDEO — put a file there, or run: streamctl video SOURCE"
  [[ -r $VIDEO ]] || die "video is not readable: $VIDEO"

  case $MODE in
    copy|encode) ;;
    *) die "MODE must be 'copy' or 'encode', got '$MODE'" ;;
  esac
}

probe() {
  ffprobe -v error -select_streams "$1" -show_entries "stream=$2" \
    -of default=nw=1:nk=1 "$VIDEO" 2>/dev/null | head -1
}

# Largest gap between keyframes in the first 12 seconds. YouTube wants one at
# least every 4 seconds; in copy mode nothing re-encodes, so whatever the file
# has is what the ingest gets.
keyframe_gap() {
  ffprobe -v error -select_streams v:0 -show_entries packet=pts_time,flags \
    -read_intervals '%+12' -of csv=p=0 "$VIDEO" 2>/dev/null |
    awk -F, '$2 ~ /K/ { if (prev != "") { d = $1 - prev; if (d > max) max = d } prev = $1 }
             END { printf "%.1f", max + 0 }'
}

vcodec=""
acodec=""
pixfmt=""

inspect() {
  vcodec=$(probe v:0 codec_name)
  acodec=$(probe a:0 codec_name)
  pixfmt=$(probe v:0 pix_fmt)

  [[ -n $vcodec ]] || die "no video stream in $VIDEO"

  log "source: $vcodec/${acodec:-no-audio} $pixfmt $(probe v:0 width)x$(probe v:0 height)"

  if [[ $MODE == copy ]]; then
    # Copy mode hands the file's existing streams straight to the ingest, so a
    # mismatch here is not a quality issue — YouTube rejects the stream outright
    # and the symptom is a connection that opens and then goes nowhere.
    [[ $vcodec == h264 ]] ||
      die "copy mode needs H.264 video, found '$vcodec'. Run: streamctl video SOURCE"
    [[ -n $acodec ]] ||
      die "copy mode needs an audio track and $VIDEO has none. Run: streamctl video SOURCE"
    [[ $acodec == aac ]] ||
      die "copy mode needs AAC audio, found '$acodec'. Run: streamctl video SOURCE"
    [[ $pixfmt == yuv420p ]] ||
      die "copy mode needs yuv420p, found '$pixfmt'. Run: streamctl video SOURCE"

    local gap
    gap=$(keyframe_gap)
    if awk -v g="$gap" 'BEGIN { exit !(g > 4) }'; then
      log "warning: keyframes up to ${gap}s apart; YouTube wants one every 4s or less."
      log "warning: re-encode with 'streamctl video SOURCE' if the stream is flagged unstable."
    fi
  fi
}

build_args() {
  args=(
    -hide_banner
    -nostdin
    -nostats
    -loglevel warning
    # Looping a file restarts its timestamps from zero on every pass; +genpts
    # rebuilds them so the ingest sees one continuous stream instead of a jump
    # backwards every time the clip repeats.
    -fflags +genpts
    # Feed at wall-clock speed. Without -re, ffmpeg pushes the file as fast as
    # it can read it and the ingest drops the connection.
    -re
    -stream_loop -1
    -i "$VIDEO"
  )

  if [[ $MODE == copy ]]; then
    args+=(
      -c copy
      -f flv
      -flvflags no_duration_filesize
    )
  else
    # A live ingest needs continuous audio. anullsrc is infinite, so it never
    # ends the output the way a finite silent track would.
    if [[ -z $acodec ]]; then
      args+=(-f lavfi -i "anullsrc=channel_layout=stereo:sample_rate=44100")
      args+=(-map 0:v:0 -map 1:a:0)
    else
      args+=(-map 0:v:0 -map 0:a:0)
    fi

    args+=(
      -vf "scale=$WIDTH:$HEIGHT:force_original_aspect_ratio=decrease,pad=$WIDTH:$HEIGHT:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=$FPS,format=yuv420p"
      -c:v libx264
      -preset veryfast
      -b:v "${VIDEO_BITRATE_K}k"
      -maxrate "${VIDEO_BITRATE_K}k"
      -bufsize "$((VIDEO_BITRATE_K * 2))k"
      # Fixed two-second GOP. -sc_threshold 0 stops x264 inserting extra
      # keyframes on scene changes, which would make the bitrate spiky.
      -g "$((FPS * 2))"
      -keyint_min "$((FPS * 2))"
      -sc_threshold 0
      -c:a aac
      -b:a "${AUDIO_BITRATE_K}k"
      -ar 44100
      -ac 2
      -f flv
    )
  fi

  args+=("$INGEST_URL/$STREAM_KEY")
}

stopping=0
child=""
trap 'stopping=1' TERM INT

# bash defers a trap until the current foreground command returns, but `wait`
# is interruptible. Every blocking point below therefore backgrounds its work
# and waits on it, so a stop is acted on immediately instead of after a
# five-minute backoff has run its course.
run_ffmpeg() {
  local rc
  # stderr through redact(), not a pipeline, so we keep ffmpeg's own PID and
  # its real exit status.
  ffmpeg "${args[@]}" 2> >(redact >&2) &
  child=$!
  wait "$child"
  rc=$?
  # Keep $child set when a trap cut the wait short: ffmpeg is still alive and
  # stop_child needs the PID to reach it.
  ((stopping)) || child=""
  return "$rc"
}

nap() {
  local sleeper
  sleep "$1" &
  sleeper=$!
  wait "$sleeper" 2>/dev/null || true
  kill "$sleeper" 2>/dev/null || true
}

stop_child() {
  [[ -n $child ]] || return 0
  kill -TERM "$child" 2>/dev/null || true
  wait "$child" 2>/dev/null || true
  child=""
}

main() {
  preflight
  inspect

  if [[ ${1:-} == --check ]]; then
    log "check passed"
    return 0
  fi

  local args=()
  build_args

  log "ingest: $INGEST_URL (mode=$MODE)"

  local backoff=5 started ran rc
  while ((!stopping)); do
    started=$(date +%s)
    log "starting ffmpeg"

    rc=0
    run_ffmpeg || rc=$?

    if ((stopping)); then
      # The wait above returns as soon as the trap fires, which can be before
      # ffmpeg has actually gone.
      stop_child
      break
    fi

    ran=$(( $(date +%s) - started ))
    # A run that lasted a while and then dropped is a blip: retry promptly. A
    # run that died immediately is usually a bad key, a revoked stream, or a
    # file problem, and hammering the ingest makes that worse.
    if ((ran >= MIN_RUN_SECONDS)); then
      backoff=5
    else
      backoff=$((backoff * 2))
      ((backoff > MAX_BACKOFF_SECONDS)) && backoff=$MAX_BACKOFF_SECONDS
    fi

    log "ffmpeg exited (status $rc) after ${ran}s; retrying in ${backoff}s"
    nap "$backoff"
  done

  log "stopped"
}

main "$@"
