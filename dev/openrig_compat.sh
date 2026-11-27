#!/usr/bin/env bash
# dev/openrig_compat.sh <M.m.p> [--seed] [--accept] [--registry <url>] [--package-dir <dir>]
#                       [--harness <name>]...
#
# Decides in minutes whether an @openrig/cli version works with HACF (release-0.6.2 R12-R14):
# installs it into a throwaway npm prefix, copies the tree under test (this repo) into the same
# temp dir, and runs the live contract suite, the capture, help and watched-file comparisons
# against the committed baselines (dev/tests/fixtures/openrig-compat/<v>/), `rig spec validate` of
# the shipped openrig/hacf/rig.yaml (v0.6.4; a FAIL `spec-invalid` when it refuses), the shipped
# probe, and HACF's verify and proof in delegate mode plus the openrig oracle. It prints one final
# line,
#
#     openrig-compat <V>: <VERDICT> (<evidence dir>)
#
# and writes verdict.json, REPORT.md, the diffs and baseline-candidate/ to
# <evidence root>/openrig-compat-<V>-<UTC>/. The comparison, normalisation and verdict logic
# is dev/openrig_compat.py.
#
#   --seed               steps 1-3, 5 and 7 only, nothing compared; verdict SEED when green.
#   --harness <name>     a community harness to smoke-test (repeatable; default agy). A changed,
#                        unreferenced or absent harness is REVIEW, a broken one FAIL.
#   --registry <url>     the npm registry to install from.
#   --package-dir <dir>  install that unpacked package dir (a copy) instead of the registry's
#                        (synthetic candidates); integrity `local`.
#   --accept             the watcher's auto-accept (R15): after a PASS, when this tree is a clean
#                        work tree on swimlane/openrig-accepted and <V> is a patch of an accepted
#                        minor, commit the contract entry and the fixture dir there (one local
#                        commit, never pushed) after the openrig tests pass on it. The result is
#                        `accept` in verdict.json; no commit otherwise.
#
# Exit: 0 PASS, 1 FAIL, 2 usage or SETUP-ERROR, 3 REVIEW, 4 SEED, 75 host busy (retry later; no
# verdict). With --accept the exit is the verdict's, except a PASS refused for the watcher streak
# (review-streak), which exits 3 (REVIEW).
#
# The evidence root: HACF_COMPAT_EVIDENCE_ROOT (a setting; the watcher passes its own root through
# it), else ${XDG_STATE_HOME:-$HOME/.local/state}/hacf/evidence.
#
# Isolation (R13): $TMPDIR must be set, not /tmp and not a tmpfs. Every candidate process runs
# with every OPENRIG_* and RIGGED_* variable dropped, OPENRIG_HOME, XDG_CACHE_HOME,
# NODE_COMPILE_CACHE and the npm cache under the run's temp dir (removed on exit), the candidate's
# .bin first on PATH, and OPENRIG_URL at a loopback listener this run opens: any connection to it
# is a FAIL (daemon-contact). The regular files directly in ~/.openrig/ (daemon logs and databases
# left out) are listed before and after; any change is a FAIL (host-touched). The host's rig is
# never called, and the repo this script runs from is never written. The uv package cache stays
# the user's (UV_CACHE_DIR), so the clone's venv needs no download.
#
# Host safety: run it under `systemd-run --user --scope -p MemoryMax=4G -p MemorySwapMax=0
# -p OOMPolicy=continue`. OOMPolicy=continue matters: systemd's default (stop) kills the whole
# scope, this script included, on the first OOM kill, so no verdict prints; with it, the killed
# step is recorded and the run ends SETUP-ERROR (exit 2). After its argument and TMPDIR checks the
# script re-runs itself under `python3 dev/scripts/host_gate.py run --job openrig_compat`, the host
# heavy-job lock: it waits for MemAvailable >= HACF_HOST_MIN_MB (6144) and for any other HACF heavy
# job, holds the lock for the whole run (its pytest legs inherit the lease's token) and exits 75
# when the bounded wait (HACF_HEAVY_WAIT_S) runs out. The scope's processes descend from the caller,
# so the token validates inside it.
set -uo pipefail

usage() {
    printf 'usage: dev/openrig_compat.sh <M.m.p> [--seed] [--accept] [--registry <url>]'\
' [--package-dir <dir>] [--harness <name>]...\n' >&2
}
die_usage() {
    printf 'openrig_compat.sh: %s\n' "$1" >&2
    usage
    exit 2
}

orig_args=("$@")
version="" seed=0 accept=0 registry="" package_dir=""
harnesses=()
while [[ $# -gt 0 ]]; do
    case $1 in
    --seed) seed=1 ;;
    --registry)
        [[ $# -ge 2 ]] || die_usage "--registry needs a URL"
        registry=$2
        shift
        ;;
    --package-dir)
        [[ $# -ge 2 ]] || die_usage "--package-dir needs a directory"
        package_dir=$2
        shift
        ;;
    --harness)
        [[ $# -ge 2 ]] || die_usage "--harness needs a name"
        [[ $2 =~ ^[a-z0-9][a-z0-9-]{0,62}$ ]] || die_usage "bad harness name '$2'"
        harnesses+=("$2")
        shift
        ;;
    --accept) accept=1 ;;
    -h | --help)
        usage
        exit 2
        ;;
    -*) die_usage "unknown option $1" ;;
    *)
        [[ -z $version ]] || die_usage "one version only"
        version=$1
        ;;
    esac
    shift
done
[[ -n $version ]] || die_usage "a version is required"
# harness list: default agy, duplicates dropped (the first occurrence wins)
[[ ${#harnesses[@]} -gt 0 ]] || harnesses=(agy)
harness_flags=()
declare -A harness_seen=()
for h in "${harnesses[@]}"; do
    [[ -n ${harness_seen[$h]:-} ]] && continue
    harness_seen[$h]=1
    harness_flags+=(--harness "$h")
done
# end of harness list
[[ $accept -eq 0 || $seed -eq 0 ]] || die_usage "--accept and --seed exclude each other"
[[ $accept -eq 0 || -z $package_dir ]] || die_usage "--accept never takes --package-dir"

root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
compat="$root/dev/openrig_compat.py"
export PYTHONDONTWRITEBYTECODE=1
python3 "$compat" check-version "$version" || die_usage "$version is not a plain M.m.p version"
if [[ -n $registry && ! $registry =~ ^https?://[^[:space:]]+$ ]]; then
    die_usage "--registry must be an http(s) URL"
fi
if [[ -n $package_dir ]]; then
    [[ -d $package_dir ]] || die_usage "--package-dir $package_dir is not a directory"
    package_dir=$(realpath -- "$package_dir")
fi

# --- The temp dir: on disk, never /tmp or a tmpfs ---------------------------------------------
tmpdir=${TMPDIR-}
[[ -n $tmpdir ]] || die_usage "TMPDIR is not set; point it at an on-disk directory (not /tmp)"
[[ $(realpath -m -- "$tmpdir") != /tmp ]] || die_usage "TMPDIR is /tmp; use an on-disk directory"
mkdir -p -- "$tmpdir" || die_usage "cannot create TMPDIR $tmpdir"
[[ $(stat -f -c %T -- "$tmpdir") != tmpfs ]] || die_usage "TMPDIR $tmpdir is on a tmpfs"
# --- Host safety: the heavy-job lock and memory floor (dev/scripts/host_gate.py) ---------------
if ! python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); import host_gate as h
sys.exit(0 if h.token_valid() else 1)' "$root/dev/scripts"; then
    exec python3 "$root/dev/scripts/host_gate.py" run --job openrig_compat -- \
        bash "$root/dev/openrig_compat.sh" "${orig_args[@]}"
fi

T=$(mktemp -d -- "$(realpath -- "$tmpdir")/openrig-compat-XXXXXX")
listener_pid=""
cleanup() {
    if [[ -n $listener_pid ]]; then
        kill -TERM "$listener_pid" 2>/dev/null
        wait "$listener_pid" 2>/dev/null
    fi
    rm -rf -- "$T"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# --accept: the tree's HEAD and cleanliness before the run, so the commit is of what was tested.
start_head="" start_clean=no
if [[ $accept -eq 1 ]]; then
    start_head=$(git -C "$root" rev-parse HEAD 2>/dev/null || true)
    if status=$(git -C "$root" status --porcelain=v1 --untracked-files=all 2>/dev/null) &&
        [[ -z $status ]]; then
        start_clean=yes
    fi
fi
# The git identity for the accept commit is the machine's own: saved before the candidate's
# environment hides it (GIT_CONFIG_GLOBAL=/dev/null below).
had_gcg=${GIT_CONFIG_GLOBAL+1} orig_gcg=${GIT_CONFIG_GLOBAL-}
had_gcns=${GIT_CONFIG_NOSYSTEM+1} orig_gcns=${GIT_CONFIG_NOSYSTEM-}

utc=$(date -u +%Y%m%dT%H%M%SZ)
evidence_root=${HACF_COMPAT_EVIDENCE_ROOT:-${XDG_STATE_HOME:-$HOME/.local/state}/hacf/evidence}
evidence="$evidence_root/openrig-compat-$version-$utc"
n=1
while ! mkdir -p -- "$(dirname -- "$evidence")" || ! mkdir -- "$evidence" 2>/dev/null; do
    n=$((n + 1))
    [[ $n -le 9 ]] || {
        printf 'openrig_compat.sh: cannot create the evidence dir %s\n' "$evidence" >&2
        exit 2
    }
    evidence="$evidence_root/openrig-compat-$version-$utc-$n"
done
mkdir -p -- "$evidence/logs" "$T/state" "$T/tmp" "$T/home" "$T/cache" "$T/hacf"
steps="$T/state/steps.tsv"
: >"$steps"

# The host's ~/.openrig before anything runs (read only: names, mtimes, sizes).
host_openrig="$HOME/.openrig"
python3 "$compat" openrig-list "$host_openrig" >"$evidence/openrig-home-before.txt"

# The user's uv package cache, resolved before XDG_CACHE_HOME moves.
uv_cache=${UV_CACHE_DIR:-$(uv cache dir 2>/dev/null || true)}

# --- The daemon-contact listener ----------------------------------------------------------------
python3 "$compat" listen --port-file "$T/state/port" --log "$T/state/connections.log" &
listener_pid=$!
for _ in $(seq 1 100); do
    [[ -s $T/state/port ]] && break
    sleep 0.1
done
port=$(cat -- "$T/state/port" 2>/dev/null || true)
if [[ ! $port =~ ^[0-9]{1,5}$ ]]; then
    printf 'openrig_compat.sh: SETUP-ERROR: the loopback listener did not start\n' >&2
    exit 2
fi

# --- The candidate environment ------------------------------------------------------------------
while IFS= read -r name; do
    case $name in OPENRIG_* | RIGGED_*) unset "$name" ;; esac
done < <(compgen -e)
export OPENRIG_URL="http://127.0.0.1:$port"
export OPENRIG_HOME="$T/home" XDG_CACHE_HOME="$T/cache" NODE_COMPILE_CACHE="$T/node-cache"
export npm_config_cache="$T/npm-cache" npm_config_update_notifier=false npm_config_fund=false
export npm_config_audit=false NO_UPDATE_NOTIFIER=1
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1
export PATH="$T/npm/node_modules/.bin:$PATH" TMPDIR="$T/tmp"
[[ -n $uv_cache ]] && export UV_CACHE_DIR="$uv_cache"
unset VIRTUAL_ENV UV_PROJECT_ENVIRONMENT
package="$T/npm/node_modules/@openrig/cli"
integrity=unknown rig_shown="" tree_sha="" baselines=""

oom_count() { python3 "$compat" oom-count 2>/dev/null || echo unknown; }

# step <name> <command...>: run it with its output in logs/<name>.log; record name, exit code,
# seconds and the cgroup's oom_kill growth in steps.tsv. When memory.events cannot be read before
# and after, the growth is `unknown`, which the report counts as a setup error ("not measured" is
# never "no OOM").
step() {
    local name=$1 o0 o1 t0 t1 rc delta=unknown detail=""
    shift
    o0=$(oom_count)
    t0=$(date +%s.%N)
    "$@" >>"$evidence/logs/$name.log" 2>&1
    rc=$?
    t1=$(date +%s.%N)
    o1=$(oom_count)
    if [[ $o0 =~ ^[0-9]+$ && $o1 =~ ^[0-9]+$ ]]; then
        delta=$((o1 - o0))
    fi
    if [[ $rc -ne 0 ]]; then
        detail=$(tail -n 1 -- "$evidence/logs/$name.log" 2>/dev/null | tr '\t\r' '  ' | cut -c1-300)
    fi
    printf '%s\t%s\t%s\t%s\t%s\n' "$name" "$rc" \
        "$(awk -v a="$t0" -v b="$t1" 'BEGIN {printf "%.3f", b - a}')" "$delta" "$detail" >>"$steps"
    return "$rc"
}

# --- 1. install -----------------------------------------------------------------------------------
install_candidate() {
    local reg=() out
    [[ -n $registry ]] && reg=(--registry "$registry")
    if [[ -n $package_dir ]]; then
        cp -a -- "$package_dir" "$T/pkg" || return 2
        rm -rf -- "$T/pkg/node_modules"
        npm i --prefix "$T/npm" --cache "$T/npm-cache" --install-links --no-audit --no-fund \
            --loglevel=error "${reg[@]}" "$T/pkg" || return 2
        integrity=local
    else
        npm i --prefix "$T/npm" --cache "$T/npm-cache" --no-audit --no-fund --loglevel=error \
            "${reg[@]}" "@openrig/cli@$version" || return 2
        out=$(npm view --cache "$T/npm-cache" "${reg[@]}" "@openrig/cli@$version" dist.integrity) ||
            return 2
        integrity=$(printf '%s' "$out" | python3 "$compat" integrity) || return 2
    fi
    python3 "$compat" package-meta --package "$package" --version "$version" || return 2
    if [[ $(command -v rig) != "$T/npm/node_modules/.bin/rig" ]]; then
        echo "rig resolves to $(command -v rig), not the isolated install"
        return 2
    fi
    rig_shown=$(timeout 60 rig --version 2>&1 | head -c 200) || return 2
    echo "rig --version: $rig_shown"
    [[ $rig_shown =~ ^${version//./\\.}([^0-9.]|$) ]] || {
        echo "rig --version printed '$rig_shown', not $version"
        return 2
    }
}

# --- 2. tree --------------------------------------------------------------------------------------
copy_tree() {
    local listing="$T/state/tree-list"
    {
        git -C "$root" ls-files -co --exclude-standard -z || return 2
        [[ -f $root/spec/compiled/architecture.yml ]] && printf 'spec/compiled/architecture.yml\0'
    } >"$listing"
    # The clone shares the repo's objects read-only (alternates) and starts at its HEAD, so
    # proofs' candidate shas and the oracle's `git show <release>:...` stay reachable; the tree
    # under test is then one commit on top. Nothing is written to the repo.
    local base
    base=$(git -C "$root" rev-parse HEAD) || return 2
    rmdir -- "$T/hacf" 2>/dev/null
    git clone -q --shared --no-checkout -- "$(git -C "$root" rev-parse --path-format=absolute \
        --git-common-dir)" "$T/hacf" || return 2
    git -C "$T/hacf" update-ref refs/heads/openrig-compat "$base" || return 2
    git -C "$T/hacf" symbolic-ref HEAD refs/heads/openrig-compat || return 2
    git -C "$T/hacf" read-tree --empty || return 2
    python3 "$compat" copy-tree --src "$root" --dst "$T/hacf" <"$listing" || return 2
    (cd -- "$T/hacf" && python3 -c 'import os,sys
names=[p for p in open(sys.argv[1],"rb").read().split(b"\0") if p and os.path.lexists(p)]
sys.stdout.buffer.write(b"\0".join(names))' "$listing") >"$T/state/tree-add" || return 2
    git -C "$T/hacf" add --force --pathspec-from-file="$T/state/tree-add" --pathspec-file-nul || return 2
    git -C "$T/hacf" -c user.name="HACF openrig-compat" -c user.email=openrig-compat@hacf.invalid \
        -c commit.gpgsign=false commit -q --allow-empty -m "tree under test" || return 2
    tree_sha=$(git -C "$T/hacf" rev-parse HEAD) || return 2
    python3 "$compat" provision-ledger --root "$T/hacf" || return 2
    (cd -- "$T/hacf" && timeout 600 uv sync --locked --quiet) || return 2
}

# --- 3. live --------------------------------------------------------------------------------------
live_suite() {
    (cd -- "$T/hacf" && HACF_LIVE_OPENRIG=1 HACF_LIVE_ACCEPT_EXTRA="$version" \
        HACF_LIVE_RECORD_DIR="$T/state/recorded" timeout --kill-after=10 900 \
        uv run --locked pytest -q -p no:cacheprovider -m 'live and openrig' \
        dev/tests/contract/test_openrig_live.py)
    local rc=$?
    if [[ $rc -eq 0 && ! -f $T/state/recorded/verdict.json ]]; then
        echo "the live suite exited 0 but recorded nothing (skipped?)"
        return 1
    fi
    return "$rc"
}

# --- 7. delegate ----------------------------------------------------------------------------------
contract_state=""
delegate_probe() {
    local inside listed
    inside=$(python3 "$compat" in-range --root "$T/hacf" --version "$version") || return 2
    cp -- "$T/hacf/.agents/config/openrig_contract.yml" "$T/state/contract.orig" || return 2
    contract_state=$(python3 "$compat" contract-unlist --root "$T/hacf" --version "$version") ||
        return 2
    echo "in range: $inside; contract: $contract_state"
    listed=$inside
    [[ $contract_state == sole ]] && listed=no # still listed: there is nothing to probe (n/a)
    (cd -- "$T/hacf" && timeout --kill-after=10 300 \
        python3 .agents/scripts/hyper_openrig.py contract probe --json) >"$T/state/probe.json"
    local prc=$?
    cp -- "$T/state/probe.json" "$evidence/probe.json"
    [[ $prc -lt 124 ]] || return "$prc"
    python3 "$compat" probe-result --file "$T/state/probe.json" --in-range "$listed"
}

delegate_contract() {
    case $contract_state in
    unlisted | sole) cp -- "$T/state/contract.orig" "$T/hacf/.agents/config/openrig_contract.yml" ;;
    not-listed)
        python3 "$compat" contract-add --root "$T/hacf" --version "$version" \
            --integrity "$integrity" --verdict "openrig-compat-run"
        ;;
    *) return 2 ;;
    esac
}

delegate_verify() {
    (
        cd -- "$T/hacf" || exit 2
        python3 - <<'PY' || exit 1
import sys
sys.path.insert(0, ".agents/scripts")
import hyper_openrig as h
plan = h.plan_rig()
print(f"plan: {plan.mode} {plan.version!r} {plan.warning or ''}")
sys.exit(0 if plan.mode == "delegate" else 1)
PY
        out=$(timeout --kill-after=10 600 python3 .agents/scripts/hyper_migrate.py verify 2>&1)
        rc=$?
        printf '%s\n' "$out"
        [[ $rc -eq 0 ]] || exit "$rc"
        grep -q '^ok: rig scope ' <<<"$out" || {
            echo "verify ran no rig scope audit (not delegate mode)"
            exit 1
        }
        if grep -q 'skipped: rig' <<<"$out"; then
            echo "verify skipped the rig audit"
            exit 1
        fi
    )
}

delegate_proof() {
    (
        cd -- "$T/hacf" || exit 2
        printf 'openrig_compat.sh delegate-mode proof of rig %s.\n' "$version" >"$T/state/proof-body.md"
        # A scratch mission and slice in the clone, made by HACF's own scope commands.
        python3 .agents/scripts/hyper_scope.py mission create --name openrig-compat-scratch \
            --intent "Scratch mission for the compat run's delegate-mode proof" || exit $?
        python3 .agents/scripts/hyper_scope.py slice create --mission openrig-compat-scratch \
            --name 01-scratch --intent "Scratch slice for the compat run's delegate-mode proof" ||
            exit $?
        slice=missions/openrig-compat-scratch/slices/01-scratch
        timeout --kill-after=10 300 python3 .agents/scripts/hyper_spec.py proof --slice "$slice" \
            --artifact-type qa --verdict PASS --candidate-sha "$tree_sha" \
            --money-evidence "openrig_compat.sh: hyper_spec.py proof in delegate mode" \
            --file "$T/state/proof-body.md" --name 01-qa.md || exit $?
        test -f "$slice/proof/01-qa.md" || exit 1
        head -n 20 -- "$slice/proof/01-qa.md"
    )
}

delegate_oracle() {
    # The oracle's tests assume the contract lists exactly what was shipped; the marker names the
    # watcher entries this run vouches for (the tree's own, plus the one contract_add just added),
    # so the exact contract check tolerates those and nothing else (OPR.99.0.12.2).
    local allowed plus=() oracle_env=()
    [[ $contract_state == not-listed ]] && plus=(--plus "$version")
    allowed=$(python3 "$compat" watcher-versions --contract "$T/state/contract.orig" \
        --root "$T/hacf" "${plus[@]}") || return 2
    [[ -n $allowed ]] && oracle_env=(env "HACF_COMPAT_WATCHER_VERSIONS=$allowed")
    (cd -- "$T/hacf" && timeout --kill-after=10 900 "${oracle_env[@]}" \
        uv run --locked pytest -q -p no:cacheprovider \
        dev/tests/openrig dev/tests/private_records/installer_records/test_contract_pin_upgrade.py)
}

stop_listener() {
    kill -TERM "$listener_pid" 2>/dev/null
    wait "$listener_pid" 2>/dev/null
    listener_pid=""
    cp -- "$T/state/connections.log" "$evidence/connections.log" 2>/dev/null
    local count
    count=$(python3 "$compat" connections --log "$T/state/connections.log")
    echo "connections: $count"
    [[ $count == 0 ]]
}

host_unchanged() {
    python3 "$compat" openrig-list "$host_openrig" >"$evidence/openrig-home-after.txt"
    diff -- "$evidence/openrig-home-before.txt" "$evidence/openrig-home-after.txt"
}

# --- 4. baselines: an unreadable choice or an empty list is a setup error, never a skipped compare
choose_baselines() {
    python3 "$compat" select-baselines --root "$T/hacf" --version "$version" \
        --out "$T/state/baselines.json" || return 2
    baselines=$(python3 "$compat" baseline-list --file "$T/state/baselines.json") || return 2
    echo "baselines: $baselines"
    [[ -n $baselines ]] || {
        echo "the baseline list is empty"
        return 2
    }
}

# --- The run ---------------------------------------------------------------------------------------
seed_flag=()
[[ $seed -eq 1 ]] && seed_flag=(--seed)
if step install install_candidate && step tree copy_tree; then
    if [[ $seed -eq 0 ]]; then
        step baselines choose_baselines
    fi
    if [[ $seed -eq 1 || -n $baselines ]]; then
        step live live_suite
        if [[ $seed -eq 0 ]]; then
            step captures python3 "$compat" captures --root "$T/hacf" --recorded "$T/state/recorded" \
                --baselines "$baselines" --root-alias "$T" --out "$T/state/captures.json"
        fi
        step help python3 "$compat" help --root "$T/hacf" --rig "$T/npm/node_modules/.bin/rig" \
            --out-dir "$T/state/help" --baselines "$baselines" --out "$T/state/help.json" \
            "${seed_flag[@]}"
        step spec-validate python3 "$compat" spec-validate --rig "$T/npm/node_modules/.bin/rig" \
            --spec "$T/hacf/openrig/hacf/rig.yaml" --out "$T/state/spec_validate.json"
        if [[ $seed -eq 0 ]]; then
            step watched python3 "$compat" watched --root "$T/hacf" --package "$package" \
                --baselines "$baselines" --out "$T/state/watched.json" --diff-dir "$evidence/diffs"
        fi
        harness_baselines=$baselines
        [[ $seed -eq 1 ]] && harness_baselines=""
        step harness timeout --kill-after=10 300 python3 "$compat" harness --root "$T/hacf" \
            --package "$package" --baselines "$harness_baselines" --work "$T/harness" \
            --out "$T/state/harness.json" --diff-dir "$evidence/diffs" "${harness_flags[@]}" \
            "${seed_flag[@]}"
        if step delegate-probe delegate_probe; then :; fi
        if step delegate-contract delegate_contract; then
            step delegate-verify delegate_verify
            step delegate-proof delegate_proof
            step delegate-oracle delegate_oracle
        fi
    fi
fi
step daemon-contact stop_listener
step host-touched host_unchanged
for f in captures.json help.json watched.json baselines.json spec_validate.json harness.json; do
    [[ -f $T/state/$f ]] && cp -- "$T/state/$f" "$evidence/$f"
done
cp -- "$steps" "$evidence/steps.tsv"

python3 "$compat" report --state "$T/state" --evidence "$evidence" --version "$version" \
    --integrity "$integrity" --rig-version-output "$rig_shown" --package "$package" \
    --tree-sha "$tree_sha" --root-alias "$T" "${harness_flags[@]}" "${seed_flag[@]}"
rc=$?
[[ $accept -eq 1 ]] || exit "$rc"

# --- --accept (R15): python3 commits only after a schema-valid PASS; anything else is recorded --
accept_env=(env -u GIT_CONFIG_GLOBAL -u GIT_CONFIG_NOSYSTEM)
[[ -n $had_gcg ]] && accept_env+=("GIT_CONFIG_GLOBAL=$orig_gcg")
[[ -n $had_gcns ]] && accept_env+=("GIT_CONFIG_NOSYSTEM=$orig_gcns")
# accept's own test run inherits the marker: the tree's watcher entries plus the one being added
refuse=()
if allowed=$(python3 "$compat" watcher-versions \
    --contract "$root/.agents/config/openrig_contract.yml" --plus "$version"); then
    accept_env+=("HACF_COMPAT_WATCHER_VERSIONS=$allowed")
else
    refuse=(--refuse "watcher-versions failed: the tree's contract could not be read")
fi
expect=()
[[ -n $start_head ]] && expect=(--expect-head "$start_head")
"${accept_env[@]}" python3 "$compat" accept --root "$root" --version "$version" \
    --evidence "$evidence" "${expect[@]}" --started-clean "$start_clean" "${refuse[@]}" -- \
    uv run --locked pytest -q dev/tests/openrig dev/tests/private_records/installer_records/test_contract_pin_upgrade.py
if [[ $rc -eq 0 ]] && grep -q '"result": "review-streak"' -- "$evidence/verdict.json"; then
    exit 3
fi
exit "$rc"
