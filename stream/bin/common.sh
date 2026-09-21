# Sourced by stream.sh and prepare-video.sh. Not executable on its own.

# Reads a systemd-style KEY=value file without letting it clobber variables that
# are already set, so `MODE=encode ./stream.sh` overrides the file the way every
# other tool behaves. Under systemd this is a no-op: EnvironmentFile has already
# put the same values in the environment.
load_env() {
  local file=$1 line key value
  [[ -r $file ]] || return 0
  while IFS= read -r line || [[ -n $line ]]; do
    [[ $line =~ ^[[:space:]]*(#|$) ]] && continue
    [[ $line == *=* ]] || continue
    key=${line%%=*}
    value=${line#*=}
    key=${key//[[:space:]]/}
    [[ $key =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
    [[ -n ${!key+set} ]] && continue
    printf -v "$key" '%s' "$value"
    export "${key?}"
  done < "$file"
}
