#!/usr/bin/env bash
# Run a VFX job inside a headless Unreal editor.
#
#   ue_run.sh <path/to/Project.uproject> <job.json> [extra UnrealEditor-Cmd args]
#
# Enables NiagaraToolsets + ToolsetRegistry for this run only (the .uproject is not edited),
# runs scripts/ue/vfx_job.py against the job, prints <job>.result.json and exits non-zero
# when the job reported errors. Set UE_ROOT to override engine discovery.
set -u
if [ $# -lt 2 ]; then
  sed -n '2,8p' "$0"; exit 2
fi
here="$(cd "$(dirname "$0")" && pwd)"
uproject="$(cygpath -m "$(realpath "$1")" 2>/dev/null || realpath "$1")"
job="$(cygpath -m "$(realpath "$2")" 2>/dev/null || realpath "$2")"
shift 2
script="$(cygpath -m "$here/ue/vfx_job.py" 2>/dev/null || echo "$here/ue/vfx_job.py")"

root="${UE_ROOT:-}"
if [ -z "$root" ]; then
  ver="$(sed -n 's/.*"EngineAssociation"[^"]*"\([^"]*\)".*/\1/p' "$uproject" | head -1)"
  for cand in "/c/Program Files/Epic Games/UE_$ver" "/d/Epic Games/UE_$ver" "/c/Epic Games/UE_$ver"; do
    [ -x "$cand/Engine/Binaries/Win64/UnrealEditor-Cmd.exe" ] && root="$cand" && break
  done
fi
if [ -z "$root" ]; then
  dat="/c/ProgramData/Epic/UnrealEngineLauncher/LauncherInstalled.dat"
  if [ -f "$dat" ]; then
    loc="$(tr -d '\r' < "$dat" | grep -B4 "\"AppName\": \"UE_$ver\"" | sed -n 's/.*"InstallLocation": "\(.*\)",/\1/p' | head -1)"
    [ -n "$loc" ] && root="$(cygpath -u "$(printf '%s' "$loc" | sed 's/\\\\/\\/g')")"
  fi
fi
cmd="$root/Engine/Binaries/Win64/UnrealEditor-Cmd.exe"
if [ ! -x "$cmd" ]; then
  echo "ue_run: cannot find UnrealEditor-Cmd.exe for engine '$ver'. Set UE_ROOT to the engine folder." >&2
  exit 2
fi

rm -f "$job.result.json"
log="$job.log"
# Full editor rather than -run=pythonscript: Niagara's compile and stack-issue checks need Slate.
# Forward slashes everywhere: Unreal eats backslashes in script paths.
VFX_JOB="$job" VFX_MODE=editor "$cmd" "$uproject" -ExecutePythonScript="$script" \
  -EnablePlugins=NiagaraToolsets,ToolsetRegistry -unattended -nosplash -nullrhi -culture=en -stdout "$@" \
  > "$log" 2>&1
code=$?
if [ -f "$job.result.json" ]; then
  cat "$job.result.json"; echo
  grep -q '"ok": true' "$job.result.json" && exit 0
  exit 1
fi
echo "ue_run: no result file; the editor failed before the job ran (exit $code). Last log lines:" >&2
grep -E "Error|Traceback|error:" "$log" | tail -20 >&2
exit 1
