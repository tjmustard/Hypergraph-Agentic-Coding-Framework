#!/usr/bin/env bash
# dev/openrig_watch.sh [--registry <url>] [--rig <path>] [--check-env]
#
# One run of the daily OpenRig compat watcher (release-0.6.2 R16, R20). No model, no tokens. It
# asks npm for the latest @openrig/cli, runs dev/openrig_compat.sh <V> --accept on it under the
# memory rule, and tells the maintainer: one `notify:` line on stdout and in history.log, then the
# configured notifier, then one queue row to the configured destination through the host rig (the
# watcher's only daemon contact). A PASS that is a patch of an accepted minor becomes one
# local commit on swimlane/openrig-accepted (made by the compat run; never pushed); REVIEW, a final
# FAIL and every refused accept become a decision row. A setup error is never reported as FAIL.
#
# Env (the unit's EnvironmentFile, written by dev/openrig_watch_install.sh):
#   HACF_WATCH_WORKTREE  the watcher worktree (branch swimlane/openrig-accepted)
#   HACF_WATCH_RIG       the host rig's absolute path (--rig overrides it)
#   TMPDIR               on disk (the unit sets %h/.cache/hacf-tmp/openrig-watch)
# Settings (optional; each unset means its neutral default):
#   HACF_WATCH_NOTIFY_CMD       a notifier, run as <cmd> "<summary>" "<one line>" (default: none)
#   HACF_WATCH_ROW_DESTINATION  the session that gets one queue row per notification (default:
#                               none, and then the watcher never calls rig)
#   HACF_COMPAT_EVIDENCE_ROOT   the evidence root (--evidence-root wins; default
#                               ${XDG_STATE_HOME:-$HOME/.local/state}/hacf/evidence)
#
# --check-env prints the resolved npm, node, uv, rig, the notifier, the row destination, the
# evidence root and the worktree, and exits 0 only when npm, node, uv, rig and the worktree resolve.
#
# Test seams: --compat <path> (default <worktree>/dev/openrig_compat.sh), --state-dir <dir>
# (~/.local/state/hacf-openrig-watch), --evidence-root <dir>, --meminfo <file> (/proc/meminfo),
# --gate-wait <s> (60), --gate-max <s> (1800).
#
# State (in --state-dir): latest.md (one line: version, outcome, time, evidence), history.log,
# verdicts/<V>.json (final verdicts), last-run, counters.json, run.log (this run's log), lock.
# Exit: 0 (any outcome recorded, including deferred and setup errors), 1 a row or the state could
# not be written, 2 usage or a missing env variable.
set -uo pipefail

usage() {
    printf '%s\n' "usage: dev/openrig_watch.sh [--registry <url>] [--rig <path>] [--check-env]" \
        "  test seams: [--compat <path>] [--state-dir <dir>] [--evidence-root <dir>]" \
        "              [--meminfo <file>] [--gate-wait <s>] [--gate-max <s>]" >&2
}
die_usage() {
    printf 'openrig_watch.sh: %s\n' "$1" >&2
    usage
    exit 2
}

registry="" rig_arg="" check_env=0 compat="" state="" evidence_root="" meminfo=/proc/meminfo
gate_wait=60 gate_max=1800
while [[ $# -gt 0 ]]; do
    case $1 in
    --check-env) check_env=1 ;;
    --registry | --rig | --compat | --state-dir | --evidence-root | --meminfo | --gate-wait | --gate-max)
        [[ $# -ge 2 ]] || die_usage "$1 needs a value"
        case $1 in
        --registry) registry=$2 ;;
        --rig) rig_arg=$2 ;;
        --compat) compat=$2 ;;
        --state-dir) state=$2 ;;
        --evidence-root) evidence_root=$2 ;;
        --meminfo) meminfo=$2 ;;
        --gate-wait) gate_wait=$2 ;;
        --gate-max) gate_max=$2 ;;
        esac
        shift
        ;;
    -h | --help)
        usage
        exit 2
        ;;
    *) die_usage "unknown argument $1" ;;
    esac
    shift
done
[[ $gate_wait =~ ^[0-9]{1,5}$ ]] || die_usage "--gate-wait must be whole seconds"
[[ $gate_max =~ ^[0-9]{1,5}$ ]] || die_usage "--gate-max must be whole seconds"
if [[ -n $registry && ! $registry =~ ^https?://[^[:space:]]+$ ]]; then
    die_usage "--registry must be an http(s) URL"
fi

worktree=${HACF_WATCH_WORKTREE-}
rig=${rig_arg:-${HACF_WATCH_RIG-}}
notify_cmd=${HACF_WATCH_NOTIFY_CMD-}
row_dest=${HACF_WATCH_ROW_DESTINATION-}
evidence_root=${evidence_root:-${HACF_COMPAT_EVIDENCE_ROOT:-${XDG_STATE_HOME:-$HOME/.local/state}/hacf/evidence}}
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
helper="$here/openrig_compat.py"
export PYTHONDONTWRITEBYTECODE=1

# --- --check-env ------------------------------------------------------------------------------------
resolve() { command -v -- "$1" 2>/dev/null || echo missing; }
if [[ $check_env -eq 1 ]]; then
    ok=0
    for tool in npm node uv; do
        path=$(resolve "$tool")
        printf '%s: %s\n' "$tool" "$path"
        [[ $path != missing ]] || ok=1
    done
    path=missing
    [[ -n $rig && $rig == /* && -x $rig && -f $rig ]] && path=$rig
    printf 'rig: %s\n' "$path"
    [[ $path != missing ]] || ok=1
    if [[ -z $notify_cmd ]]; then
        printf 'notifier: none\n'
    elif path=$(command -v -- "$notify_cmd" 2>/dev/null) && [[ -n $path ]]; then
        printf 'notifier: %s\n' "$path"
    else
        printf 'notifier: unresolved %s\n' "$notify_cmd"
    fi
    printf 'row destination: %s\n' "${row_dest:-none}"
    printf 'evidence root: %s\n' "$evidence_root"
    path=missing
    [[ -n $worktree && -f $worktree/dev/openrig_compat.sh ]] && path=$worktree
    printf 'worktree: %s\n' "$path"
    [[ $path != missing ]] || ok=1
    exit "$ok"
fi

[[ -n $worktree ]] || die_usage "HACF_WATCH_WORKTREE is not set (the unit's env file sets it)"
[[ -n $rig ]] || die_usage "HACF_WATCH_RIG is not set (the unit's env file sets it)"
[[ -d $worktree ]] || die_usage "HACF_WATCH_WORKTREE $worktree is not a directory"
[[ $rig == /* && -x $rig ]] || die_usage "the host rig $rig is not an absolute executable path"
[[ -n ${TMPDIR-} ]] || die_usage "TMPDIR is not set (the unit sets it on disk)"
compat=${compat:-$worktree/dev/openrig_compat.sh}
state=${state:-$HOME/.local/state/hacf-openrig-watch}

mkdir -p -- "$state/verdicts" "$TMPDIR" "$evidence_root" || {
    printf 'openrig_watch.sh: cannot create %s, %s or %s\n' "$state" "$TMPDIR" "$evidence_root" >&2
    exit 1
}

# --- One run at a time --------------------------------------------------------------------------------
exec 9>"$state/lock" || exit 1
if ! flock -n 9; then
    printf 'openrig_watch.sh: another run holds %s/lock\n' "$state" >&2
    exit 0
fi

T=$(mktemp -d -- "$TMPDIR/openrig-watch-XXXXXX") || exit 1
# npm and node write nothing outside $T, and npm makes no update-notifier request.
export NODE_COMPILE_CACHE="$T/node-cache" npm_config_update_notifier=false npm_config_fund=false
export npm_config_audit=false NO_UPDATE_NOTIFIER=1
child=""
cleanup() { rm -rf -- "$T"; }
on_signal() {
    if [[ -n $child ]]; then
        kill -TERM "$child" 2>/dev/null
        wait "$child" 2>/dev/null
    fi
    log "interrupted by SIG$1"
    printf '%s interrupted %s -\n' "${V:--}" "$(now)" >>"$state/history.log"
    cleanup
    trap - EXIT
    exit "$2"
}
trap cleanup EXIT
trap 'on_signal TERM 143' TERM
trap 'on_signal INT 130' INT

now() { date -u +%Y-%m-%dT%H:%M:%SZ; }
: >"$state/run.log"
log() { printf '%s %s\n' "$(now)" "$*" >>"$state/run.log"; }
failed_io=0
V=""

# run_bg <command...>: run in the background and wait, so a TERM reaches the trap at once.
run_bg() {
    "$@" &
    child=$!
    wait "$child"
    local rc=$?
    child=""
    return "$rc"
}

finish() { # finish <outcome> <evidence|-> <final yes|no>
    local outcome=$1 ev=$2 final=$3 line
    line="${V:--} $outcome $(now) $ev"
    printf '%s\n' "$line" >"$state/latest.md.tmp" && mv -f -- "$state/latest.md.tmp" "$state/latest.md" ||
        failed_io=1
    printf '%s\n' "$line" >>"$state/history.log" || failed_io=1
    if [[ -n $V ]]; then
        python3 "$helper" watch-record --state-dir "$state" --version "$V" --outcome "$outcome" \
            --evidence "$ev" --final "$final" >>"$state/run.log" 2>&1 || failed_io=1
    fi
    log "outcome: $outcome ($ev)"
}

last_run() { now >"$state/last-run" || failed_io=1; }

# notify <summary> <one line> <evidence ref> <body>: never silent. One `notify:` line on stdout and
# in history.log, then the notifier (HACF_WATCH_NOTIFY_CMD, when set), then the row
# (HACF_WATCH_ROW_DESTINATION, when set; R20). Unset settings make no call.
row_failed=0
notify() {
    local summary=$1 line=$2 ref=$3 body_text=$4 body cmd
    printf 'notify: %s | %s\n' "$summary" "$line"
    printf 'notify: %s | %s\n' "$summary" "$line" >>"$state/history.log" || failed_io=1
    if [[ -n $notify_cmd ]]; then
        cmd=$(command -v -- "$notify_cmd" 2>/dev/null || true)
        if [[ -n $cmd ]]; then
            "$cmd" "$summary" "$line" >>"$state/run.log" 2>&1 || log "notifier $notify_cmd failed"
        else
            log "notifier $notify_cmd not found (not fatal)"
        fi
    fi
    [[ -n $row_dest ]] || return 0
    body=$(mktemp -- "$T/row-XXXXXX.md") || {
        row_failed=1
        return
    }
    printf '%s\n' "$body_text" >"$body"
    if "$rig" queue create --source hacf-openrig-watch@hacf --destination "$row_dest" \
        --summary "$summary" --body-file "$body" --evidence-ref "$ref" >>"$state/run.log" 2>&1; then
        log "row sent: $summary"
    else
        log "the row failed: $summary"
        row_failed=1
    fi
    rm -f -- "$body"
}

end() {
    last_run
    [[ $row_failed -eq 0 && $failed_io -eq 0 ]] || exit 1
    exit 0
}

# no_verdict <deferred|setup> <why> <evidence|->: count a run without a verdict; on the second in a
# row, one update row (once until a verdict resets it).
no_verdict() {
    local event=$1 why=$2 ev=$3 out n
    finish "$event" "$ev" no
    out=$(python3 "$helper" watch-state --state-dir "$state" --event "$event" 2>>"$state/run.log") || {
        failed_io=1
        return
    }
    if [[ $out == notify* ]]; then
        n=${out#notify }
        notify "OpenRig watcher: $n runs without a verdict" "$why" "${ev/#-/$state}" \
            "$(printf 'The OpenRig watcher has had %s runs in a row without a verdict.\n\nLast: %s\nVersion: %s\nState: %s\nHistory: %s/history.log' \
                "$n" "$why" "${V:--}" "$state" "$state")"
    fi
}

# --- 1. npm: the latest version and the accepted entries' integrity -----------------------------------
reg=()
[[ -n $registry ]] && reg=(--registry "$registry")
npm_view() { run_bg timeout 120 npm view --cache "$T/npm-cache" "${reg[@]}" "$@"; }

if ! out=$(npm_view @openrig/cli version 2>>"$state/run.log") ||
    ! V=$(printf '%s' "$out" | python3 "$helper" npm-version 2>>"$state/run.log"); then
    V=""
    log "npm view @openrig/cli version failed or printed no plain version"
    no_verdict setup "npm view @openrig/cli version failed" -
    end
fi
log "latest: $V"

if ! entries=$(python3 "$helper" contract-entries --root "$worktree" 2>>"$state/run.log"); then
    log "the worktree's contract file is unreadable"
    no_verdict setup "the watcher worktree's contract file is unreadable" -
    end
fi
listed=0
while IFS=$'\t' read -r ver want by; do
    [[ -n $ver ]] || continue
    [[ $ver == "$V" ]] && listed=1
    if ! got=$(npm_view "@openrig/cli@$ver" dist.integrity 2>>"$state/run.log") ||
        ! got=$(printf '%s' "$got" | python3 "$helper" integrity 2>>"$state/run.log"); then
        log "npm view @openrig/cli@$ver dist.integrity failed"
        no_verdict setup "npm view @openrig/cli@$ver dist.integrity failed" -
        end
    fi
    if [[ $got != "$want" ]]; then
        seen=$(python3 "$helper" watch-state --state-dir "$state" --event integrity --version "$ver" \
            --value "$got" 2>>"$state/run.log") || failed_io=1
        log "integrity of accepted $ver ($by) changed: $got"
        if [[ $seen == new ]]; then
            notify "OpenRig $ver: integrity changed: decision needed" \
                "the registry's integrity for accepted $ver changed" "$state" \
                "$(printf 'The npm registry now reports a different dist.integrity for @openrig/cli@%s, which the contract file accepts (accepted_by: %s).\n\nContract: %s\nRegistry: %s\n\nNothing was changed. Decide whether the entry stays accepted.' \
                    "$ver" "$by" "$want" "$got")"
        fi
    fi
done <<<"$entries"

# --- 2. a final verdict or an accepted version: nothing to do ------------------------------------------
if [[ $listed -eq 1 ]]; then
    log "$V is already in the contract file: nothing to do"
    end
fi
if python3 "$helper" watch-final --state-dir "$state" --version "$V" 2>>"$state/run.log"; then
    log "$V already has a final verdict"
    end
fi

# --- 3. the memory gate --------------------------------------------------------------------------------
gate_open() {
    local avail user=${USER:-$(id -un)}
    avail=$(awk '/^MemAvailable:[[:space:]]+[0-9]+[[:space:]]+kB$/ {print int($2 / 1024)}' \
        "$meminfo" 2>/dev/null | head -n 1)
    if [[ ! $avail =~ ^[0-9]+$ || $avail -lt 6144 ]]; then
        log "gate: MemAvailable ${avail:-unknown} MB (< 6144)"
        return 1
    fi
    if pgrep -u "$user" -x pytest >/dev/null ||
        pgrep -u "$user" -f '^([^ ]*/)?python[0-9.]* -m pytest( |$)' >/dev/null ||
        pgrep -u "$user" -f '^([^ ]*/)?bash [^ ]*dev/scripts/check\.sh( |$)' >/dev/null; then
        log "gate: another pytest or check.sh runs"
        return 1
    fi
    return 0
}
gate_wait_open() {
    local start=$SECONDS
    until gate_open; do
        ((SECONDS - start < gate_max)) || return 1
        run_bg sleep "$gate_wait"
    done
}

# --- 4./5. the compat run; a FAIL is retried once at once ----------------------------------------------
compat_run() { # sets rc, evidence, verdict, accept_result, detail
    local outfile="$T/compat.out" line
    evidence="-" verdict="SETUP-ERROR" accept_result="-" detail=""
    log "running $compat $V --accept"
    HACF_COMPAT_EVIDENCE_ROOT="$evidence_root" run_bg bash "$compat" "$V" --accept "${reg[@]}" \
        >"$outfile" 2>&1
    rc=$?
    log "compat exit $rc"
    line=$(grep -E "^openrig-compat ${V//./\\.}: (PASS|FAIL|REVIEW|SETUP-ERROR|SEED)( [^ ]+)? \(.+\)$" \
        "$outfile" | tail -n 1)
    if [[ -n $line ]]; then
        evidence=${line##* (}
        evidence=${evidence%)}
    fi
    if [[ $evidence != - ]]; then
        cp -- "$outfile" "$evidence/watch-compat.log" 2>/dev/null
    else
        cp -- "$outfile" "$state/last-compat.log" 2>/dev/null
    fi
    if [[ $evidence == - || $evidence != "$evidence_root"/* || ! -d $evidence ]]; then
        detail="compat exit $rc and no evidence dir under $evidence_root"
        evidence="-"
        return
    fi
    if ((rc >= 124)); then # 124 timeout, 128+n a signal (137: the OOM killer): setup, never FAIL
        detail="compat exit $rc (a signal, a timeout or an OOM kill)"
        return
    fi
    local fields
    fields=$(python3 "$helper" watch-verdict --evidence "$evidence" --version "$V" --rc "$rc" \
        2>>"$state/run.log") || fields=$'SETUP-ERROR\t-\twatch-verdict failed'
    IFS=$'\t' read -r verdict accept_result detail <<<"$fields"
}

if ! gate_wait_open; then
    no_verdict deferred "the memory gate stayed closed for ${gate_max}s" -
    end
fi
compat_run
if [[ $verdict == FAIL ]]; then
    count=$(python3 "$helper" watch-state --state-dir "$state" --event fail --version "$V" \
        2>>"$state/run.log") || count=2
    if [[ $count == 1 ]]; then
        log "FAIL ($detail): retrying once"
        if gate_open; then
            compat_run
            if [[ $verdict == FAIL ]]; then
                python3 "$helper" watch-state --state-dir "$state" --event fail --version "$V" \
                    >/dev/null 2>>"$state/run.log"
            fi
        else
            python3 "$helper" watch-state --state-dir "$state" --event verdict >/dev/null \
                2>>"$state/run.log"
            finish fail-pending "$evidence" no
            end
        fi
    fi
fi

# --- 6. notify -------------------------------------------------------------------------------------------
if [[ $verdict == SETUP-ERROR || $verdict == SEED ]]; then
    no_verdict setup "compat $V: ${detail:-setup error}" "$evidence"
    end
fi
python3 "$helper" watch-state --state-dir "$state" --event verdict >/dev/null 2>>"$state/run.log" ||
    failed_io=1
python3 "$helper" watch-state --state-dir "$state" --event final --version "$V" \
    >/dev/null 2>>"$state/run.log" || failed_io=1
diffs=$(find "$evidence/diffs" -maxdepth 1 -type f 2>/dev/null | sort)
body() { # body <headline>
    printf '%s\n\nVersion: %s\nVerdict: %s\nAccept: %s\nDetail: %s\nEvidence: %s\nReport: %s/REPORT.md\nDiffs:\n%s\n' \
        "$1" "$V" "$verdict" "$accept_result" "${detail:--}" "$evidence" "$evidence" "${diffs:-  (none)}"
}
case $verdict:$accept_result in
PASS:accepted)
    sha=$(python3 - "$evidence/verdict.json" <<'PY' 2>/dev/null
import json, sys
print(json.load(open(sys.argv[1], encoding="utf-8"))["accept"]["sha"])
PY
    )
    finish accepted "$evidence" yes
    notify "OpenRig $V: accepted locally (watcher)" "PASS; commit ${sha:0:12} on swimlane/openrig-accepted" \
        "$evidence" "$(body "OpenRig $V passed the compat run and was accepted by one local commit (${sha:-?}) on swimlane/openrig-accepted in $worktree. Nothing was pushed; it reaches a project only through a later HACF release.")"
    ;;
PASS:not-a-patch)
    finish not-a-patch "$evidence" yes
    notify "OpenRig $V: not a patch: decision needed" "PASS, but not a patch of an accepted minor" \
        "$evidence" "$(body "OpenRig $V passed the compat run but is not a patch of an accepted minor, so it was not accepted. Decide whether to accept it.")"
    ;;
PASS:*)
    finish "$accept_result" "$evidence" yes
    notify "OpenRig $V: REVIEW: decision needed" "PASS, not accepted: $accept_result" \
        "$evidence" "$(body "OpenRig $V passed the compat run but the watcher did not accept it ($accept_result). Decide whether to accept it.")"
    ;;
REVIEW:*)
    finish REVIEW "$evidence" yes
    notify "OpenRig $V: REVIEW: decision needed" "every step green; watched files differ" \
        "$evidence" "$(body "OpenRig $V ran green but a watched file differs from a baseline. Read the diffs and decide.")"
    ;;
FAIL:*)
    finish FAIL "$evidence" yes
    notify "OpenRig $V: FAIL: decision needed" "FAIL twice: $detail" \
        "$evidence" "$(body "OpenRig $V failed the compat run twice (failed step: $detail).")"
    ;;
*)
    no_verdict setup "compat $V: unexpected verdict $verdict" "$evidence"
    ;;
esac
end
