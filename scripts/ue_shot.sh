#!/usr/bin/env bash
# Render Niagara systems to PNG stills offscreen (no window), so you can look at an effect.
#
#   ue_shot.sh <path/to/Project.uproject> <shot.json>
#
# See scripts/ue/vfx_shot.py for the config format. Writes shot_<time>.png and shot.log.txt into
# the config's out_dir. Uses a real GPU (unlike ue_run.sh), so shaders compile: allow the first
# shot a few seconds of lead time. Set UE_ROOT to override engine discovery.
set -u
if [ $# -lt 2 ]; then
  sed -n '2,8p' "$0"; exit 2
fi
here="$(cd "$(dirname "$0")" && pwd)"
uproject="$(cygpath -m "$(realpath "$1")" 2>/dev/null || realpath "$1")"
cfg="$(cygpath -m "$(realpath "$2")" 2>/dev/null || realpath "$2")"
script="$(cygpath -m "$here/ue/vfx_shot.py" 2>/dev/null || echo "$here/ue/vfx_shot.py")"

root="${UE_ROOT:-}"
if [ -z "$root" ]; then
  ver="$(sed -n 's/.*"EngineAssociation"[^"]*"\([^"]*\)".*/\1/p' "$uproject" | head -1)"
  for cand in "/c/Program Files/Epic Games/UE_$ver" "/d/Epic Games/UE_$ver" "/c/Epic Games/UE_$ver"; do
    [ -x "$cand/Engine/Binaries/Win64/UnrealEditor.exe" ] && root="$cand" && break
  done
fi
exe="$root/Engine/Binaries/Win64/UnrealEditor.exe"
if [ ! -x "$exe" ]; then
  echo "ue_shot: cannot find UnrealEditor.exe. Set UE_ROOT to the engine folder." >&2
  exit 2
fi

# A startup console command, not -ExecutePythonScript: that flag makes the editor exit a few
# frames after the script returns, before anything has simulated.
VFX_SHOT="$cfg" "$exe" "$uproject" -ExecCmds="py $script" -RenderOffscreen -unattended -nosplash \
  -culture=en -abslog="$cfg.log" > /dev/null 2>&1
out_dir="$(sed -n 's/.*"out_dir"[^"]*"\([^"]*\)".*/\1/p' "$cfg" | head -1)"
case "$out_dir" in /*|?:*) ;; *) out_dir="$(dirname "$cfg")/${out_dir:-shots}" ;; esac
cat "$out_dir/shot.log.txt" 2>/dev/null || { echo "ue_shot: no shot log; see $cfg.log" >&2; exit 1; }
ls "$out_dir"/*.png 2>/dev/null
