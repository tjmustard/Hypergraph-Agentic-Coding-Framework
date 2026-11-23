#!/usr/bin/env bash
# dev/openrig_probe_record.sh <version>: print the range probe's expectation file for one rig.
#
# Installs @openrig/cli@<version> into an isolated npm prefix under $TMPDIR, runs the shipped
# probe (hyper_openrig.run_probe, record mode) twice with that binary first on PATH, marks as
# volatile_keys every C1 header key whose value differed between the two runs, keeps the JSON key
# paths HACF's checks read (hyper_openrig.PROBE_READ_PATHS; a later rig may print more) and
# prints the expectation JSON on stdout (release-0.6.2 R11). Regenerating the committed file is a
# person's commit in a HACF release:
#
#     bash dev/openrig_probe_record.sh 0.7.21 > .agents/config/openrig_probe_expect.json
#
# Isolation (R6, R13): $TMPDIR must be on disk (not /tmp, not a tmpfs); every OPENRIG_* and
# RIGGED_* variable is dropped; OPENRIG_URL is the dead discard port; OPENRIG_HOME, the npm
# cache, XDG_CACHE_HOME and NODE_COMPILE_CACHE live under the run's temp dir, which is removed on
# exit. The host's own rig is never called. Host safety: MemAvailable must be at least 6144 MB.
#
# Exit codes: 0 printed, 1 the probe or the two runs disagree, 2 usage or setup error.
set -euo pipefail

die() {
    printf 'openrig_probe_record.sh: %s\n' "$1" >&2
    exit "${2:-2}"
}

[[ $# -eq 1 && $1 =~ ^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]] ||
    die "usage: dev/openrig_probe_record.sh <M.m.p>"
version=$1
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)

# Host safety: an on-disk TMPDIR and enough free memory.
tmpdir=${TMPDIR-}
[[ -n $tmpdir ]] || die "TMPDIR is not set; point it at an on-disk directory (not /tmp)"
[[ $(realpath -m -- "$tmpdir") != /tmp ]] || die "TMPDIR is /tmp; use an on-disk directory"
mkdir -p -- "$tmpdir"
[[ $(stat -f -c %T -- "$tmpdir") != tmpfs ]] || die "TMPDIR $tmpdir is on a tmpfs"
avail=$(awk '/^MemAvailable:/ {print int($2 / 1024)}' /proc/meminfo 2>/dev/null || true)
[[ -n $avail && $avail -ge 6144 ]] || die "MemAvailable is ${avail:-unknown} MB (< 6144); wait"

T=$(mktemp -d -- "$tmpdir/hacf-probe-record-XXXXXX")
trap 'rm -rf -- "$T"' EXIT

while IFS= read -r name; do
    case $name in OPENRIG_* | RIGGED_*) unset "$name" ;; esac
done < <(compgen -e)
export OPENRIG_URL=http://127.0.0.1:9
export OPENRIG_HOME="$T/home" XDG_CACHE_HOME="$T/cache" NODE_COMPILE_CACHE="$T/node-cache"
export npm_config_cache="$T/npm-cache" npm_config_update_notifier=false TMPDIR="$T/tmp"
mkdir -p -- "$TMPDIR"

npm i --prefix "$T/npm" --cache "$T/npm-cache" --no-audit --no-fund --loglevel=error \
    "@openrig/cli@$version" >&2 || die "npm i @openrig/cli@$version failed"
integrity=$(
    node -e 'const l=require(process.argv[1]);const p=l.packages["node_modules/@openrig/cli"];
             process.stdout.write((p&&p.integrity)||"unknown")' "$T/npm/package-lock.json"
) || integrity=unknown

PATH="$T/npm/node_modules/.bin:$PATH" python3 - "$root" "$version" "$integrity" "$T" <<'PY'
import json
import os
import sys

root, version, integrity, tmp = sys.argv[1:5]
sys.path.insert(0, os.path.join(root, ".agents", "scripts"))
import hyper_openrig as h  # noqa: E402

prefix = h.resolve_rig()
if not prefix or not os.path.realpath(prefix[0]).startswith(os.path.realpath(tmp) + os.sep):
    sys.exit(f"openrig_probe_record.sh: rig resolved to {prefix}, not the isolated install")
shown = h.run_rig([*prefix, "--version"], timeout=60).stdout.strip()
if h.parse_version(shown) != tuple(int(x) for x in version.split(".")):
    sys.exit(f"openrig_probe_record.sh: rig --version printed {shown!r}, not {version}")
runs = [h.run_probe(prefix, shown, record=True, timeout=120) for _ in range(2)]
for run in runs:
    if run.outcome != "pass":
        sys.exit(f"openrig_probe_record.sh: the record run ended {run.outcome}: {run.detail}")
a, b = (run.observed for run in runs)
for key in ("exit_codes", "write_set", "help_flags"):
    if a[key] != b[key]:
        sys.exit(f"openrig_probe_record.sh: the two runs differ in {key}: {a[key]} / {b[key]}")
if a["rewritten"] or b["rewritten"]:
    sys.exit(f"openrig_probe_record.sh: rig rewrote HACF-rendered files: {a['rewritten']}")
if a["c1_header"] is None or b["c1_header"] is None:
    sys.exit("openrig_probe_record.sh: proof add wrote no readable artifact")
for step, paths in a["json_key_paths"].items():
    if paths is None or b["json_key_paths"][step] is None:
        sys.exit(f"openrig_probe_record.sh: {step} printed no JSON")
keys = sorted(set(a["c1_header"]) | set(b["c1_header"]))
volatile = [k for k in keys if a["c1_header"].get(k) != b["c1_header"].get(k)]
header = {k: h.PROBE_VOLATILE if k in volatile else v for k, v in a["c1_header"].items()}
# The key paths HACF's checks read (a later rig may print more); each must be in both runs.
paths = {}
for step in h.PROBE_JSON_STEPS:
    seen = set(a["json_key_paths"][step]) & set(b["json_key_paths"][step])
    missing = sorted(set(h.PROBE_READ_PATHS[step]) - seen)
    if missing:
        sys.exit(f"openrig_probe_record.sh: {step} JSON lacks {', '.join(missing)}")
    paths[step] = sorted(h.PROBE_READ_PATHS[step])
expect = {
    "rig_version": version,
    "probe_schema": h.load_contract().probe_schema,
    "generator": (
        f"dev/openrig_probe_record.sh {version}: @openrig/cli@{version} ({integrity}), "
        f"rig --version {shown!r}; two record runs; volatile_keys are the C1 header keys whose "
        "value differed between them"
    ),
    "exit_codes": a["exit_codes"],
    "write_set": a["write_set"],
    "volatile_keys": volatile,
    "c1_header": header,
    "json_key_paths": paths,
    "help_flags": a["help_flags"],
}
print(json.dumps(expect, indent=2, sort_keys=True))
PY
