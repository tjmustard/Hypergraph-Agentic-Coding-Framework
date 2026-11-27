#!/usr/bin/env bash
# dev/openrig_watch.sh [--registry <url>] [--rig <path>] [--check-env | --row-check |
#                       --failure-notify]
#
# One run of the daily OpenRig compat watcher (release-0.6.2 R16, R20). No model, no tokens. It
# asks npm for the latest @openrig/cli, runs dev/openrig_compat.sh <V> --accept on it under the
# memory rule, and tells the maintainer: one `notify:` line on stdout and in history.log, then the
# configured notifier, then one queue row to the configured destination through the host rig (the
# watcher's only daemon contact). A row is written to the outbox (<state>/outbox/) before it is sent,
# removed once it lands, and re-sent with its request id by the next run otherwise. The first setup
# error of a stream is one "checker broke" row. A PASS that is a patch of an accepted minor becomes one
# local commit on swimlane/openrig-accepted (made by the compat run; never pushed); REVIEW, a final
# FAIL and every refused accept become a decision row. A setup error is never reported as FAIL.
#
# The tail then runs after every outcome: it records the installed version (`rig --version`),
# self-tests the checker on it against an expectation keyed by that version, reconciles at most one
# verifiable upgrade request to the operator (raise, withdraw, supersede, expire; state
# upgrade.json), and reports a changed host version. The watcher only raises rows: it never upgrades
# or restarts anything.
#
# Env (the unit's EnvironmentFile, written by dev/openrig_watch_install.sh):
#   HACF_WATCH_WORKTREE  the watcher worktree (branch swimlane/openrig-accepted)
#   HACF_WATCH_RIG       the host rig's absolute path (--rig overrides it)
#   TMPDIR               on disk (the unit sets %h/.cache/hacf-tmp/openrig-watch)
# Settings (optional; each unset means its neutral default):
#   HACF_WATCH_NOTIFY_CMD       a notifier, run as <cmd> "<summary>" "<one line>" (default: none)
#   HACF_WATCH_ROW_DESTINATION  the session that gets one queue row per notification (default:
#                               none, and then the watcher's only rig call is `rig --version`,
#                               unless an upgrade destination is set)
#   HACF_COMPAT_EVIDENCE_ROOT   the evidence root (--evidence-root wins; default
#                               ${XDG_STATE_HOME:-$HOME/.local/state}/hacf/evidence)
#   HACF_WATCH_HARNESSES        comma list of community harnesses to smoke (default agy)
#   HACF_WATCH_UPGRADE_DESTINATION   the upgrade rows' session (default none)
#   HACF_WATCH_FAILURE_NOTIFY_CMD    run by --failure-notify (the unit's OnFailure=) as
#                               <cmd> "<title>" "<line>"; visible only while the user is logged in
#
# --check-env prints the resolved npm, node, uv, rig, the notifier, the row destination, the upgrade
# destination, the harnesses, the failure notifier, the evidence root and the worktree, and exits 0
# only when npm, node, uv, rig and the worktree resolve.
# --row-check sends one verified row to HACF_WATCH_ROW_DESTINATION through the host rig and writes no
# state (exit 2 when the destination is unset). --failure-notify runs the failure notifier once.
#
# Test seams: --compat <path> (default <worktree>/dev/openrig_compat.sh), --state-dir <dir>
# (~/.local/state/hacf-openrig-watch), --evidence-root <dir>, --meminfo <file> (/proc/meminfo),
# --gate-wait <s> (60), --gate-max <s> (1800), --deadline <s> (21000, the run's budget; the unit's
# TimeoutStartSec covers it), --compat-max <s> (5400, one compat run's limit), --now <iso>
# (YYYY-MM-DDTHH:MM:SSZ, the upgrade reconcile's clock).
#
# State (in --state-dir): latest.md (one line: version, outcome, time, evidence), history.log,
# verdicts/<V>.json (final verdicts), last-run, counters.json, installed.json, selftest/<V>.json
# (expectations), upgrade.json, outbox/, run.log (this run's log), lock.
# Exit: 0 (any outcome recorded, including deferred and setup errors), 1 a row or the state could
# not be written, 2 usage or a missing env variable.
set -uo pipefail

usage() {
    printf '%s\n' "usage: dev/openrig_watch.sh [--registry <url>] [--rig <path>]" \
        "           [--check-env | --row-check | --failure-notify]" \
        "  test seams: [--compat <path>] [--state-dir <dir>] [--evidence-root <dir>]" \
        "              [--meminfo <file>] [--gate-wait <s>] [--gate-max <s>]" \
        "              [--deadline <s>] [--compat-max <s>] [--now <iso>]" >&2
}
die_usage() {
    printf 'openrig_watch.sh: %s\n' "$1" >&2
    usage
    exit 2
}

registry="" rig_arg="" check_env=0 compat="" state="" evidence_root="" meminfo=/proc/meminfo
gate_wait=60 gate_max=1800 deadline=21000 compat_max=5400 row_check=0 failure_notify=0 now_arg=""
while [[ $# -gt 0 ]]; do
    case $1 in
    --check-env) check_env=1 ;;
    --row-check) row_check=1 ;;
    --failure-notify) failure_notify=1 ;;
    --registry | --rig | --compat | --state-dir | --evidence-root | --meminfo | --gate-wait | \
        --gate-max | --deadline | --compat-max | --now)
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
        --deadline) deadline=$2 ;;
        --compat-max) compat_max=$2 ;;
        --now) now_arg=$2 ;;
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
[[ $deadline =~ ^[0-9]{1,6}$ ]] || die_usage "--deadline must be whole seconds"
[[ $compat_max =~ ^[0-9]{1,6}$ ]] || die_usage "--compat-max must be whole seconds"
[[ -z $now_arg || $now_arg =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$ ]] ||
    die_usage "--now must be YYYY-MM-DDTHH:MM:SSZ"
((check_env + row_check + failure_notify <= 1)) ||
    die_usage "--check-env, --row-check and --failure-notify exclude each other"
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
state=${state:-${HOME-}/.local/state/hacf-openrig-watch}
now() { date -u +%Y-%m-%dT%H:%M:%SZ; }
upgrade_dest=${HACF_WATCH_UPGRADE_DESTINATION-}
failure_cmd=${HACF_WATCH_FAILURE_NOTIFY_CMD-}

# --- --failure-notify: the unit's OnFailure= (no lock, no TMPDIR, no rig) ----------------------------
if [[ $failure_notify -eq 1 ]]; then
    if [[ -z $failure_cmd ]]; then
        echo "failure-notify: no notifier"
        exit 0
    fi
    cmd=$(command -v -- "$failure_cmd" 2>/dev/null || true)
    if [[ -z $cmd ]]; then
        echo "failure-notify: unresolved $failure_cmd"
        exit 1
    fi
    line=$(tail -n 1 -- "$state/latest.md" 2>/dev/null)
    line="${line:-no latest.md}; see $state/history.log"
    timeout 30 "$cmd" "HACF OpenRig watcher: run failed" "${line:0:300}"
    rc=$?
    [[ ! -d $state ]] || printf -- '- failure-notify %s rc %s\n' "$(now)" "$rc" >>"$state/history.log"
    exit "$rc"
fi

harness_list=${HACF_WATCH_HARNESSES-agy}
name_re='[a-z0-9][a-z0-9-]{0,62}'
[[ $harness_list =~ ^${name_re}(,${name_re})*$ ]] ||
    die_usage "HACF_WATCH_HARNESSES must be a comma list of harness names"
harness_args=()
IFS=, read -r -a harness_names <<<"$harness_list"
for h in "${harness_names[@]}"; do harness_args+=(--harness "$h"); done

# send_row <dest> <summary> <evidence-ref> <body-file> <verify yes|no> <request-id or "">
# [<tags> <expires-at>] (the last two for an upgrade request only): the one place a queue row is
# created. The rig's output goes to $T and to the run log; sets
# last_request_id (empty when none was printed) and returns the command's exit status.
last_request_id="" last_rig_out="" row_log=""
send_row() {
    local dest=$1 summary=$2 ref=$3 file=$4 verify=$5 request=$6 tags=${7-} expires=${8-} rc
    local args=(queue create --source hacf-openrig-watch@hacf --destination "$dest"
        --summary "$summary" --body-file "$file" --evidence-ref "$ref")
    last_request_id=""
    last_rig_out=$(mktemp -- "$T/rig-XXXXXX.log") || return 1
    [[ $verify == yes ]] && args+=(--verify)
    [[ -n $request ]] && args+=(--id "$request")
    [[ -n $tags ]] && args+=(--tags "$tags")
    [[ -n $expires ]] && args+=(--expires-at "$expires")
    run_bg timeout 60 "$rig" "${args[@]}" >"$last_rig_out" 2>&1
    rc=$?
    cat -- "$last_rig_out" >>"${row_log:-/dev/null}"
    last_request_id=$(sed -nE 's/.*Queue create request ID: (qitem-[0-9]{14}-[0-9a-f]+).*/\1/p' \
        "$last_rig_out" | head -n 1)
    return "$rc"
}

child=""
# run_bg <command...>: run in the background and wait, so a TERM reaches the trap at once.
run_bg() {
    "$@" &
    child=$!
    wait "$child"
    local rc=$?
    child=""
    return "$rc"
}

# --- --row-check: one verified row, no state, no lock ------------------------------------------------
if [[ $row_check -eq 1 ]]; then
    [[ -n $row_dest ]] || die_usage "HACF_WATCH_ROW_DESTINATION is not set"
    [[ -n $rig && $rig == /* && -x $rig ]] || die_usage "the host rig is not an absolute executable"
    [[ -n ${TMPDIR-} ]] || die_usage "TMPDIR is not set (the unit sets it on disk)"
    mkdir -p -- "$TMPDIR" || exit 1
    T=$(mktemp -d -- "$TMPDIR/openrig-watch-XXXXXX") || exit 1
    trap 'rm -rf -- "${T:?}"' EXIT
    version_line=$(timeout 30 "$rig" --version 2>&1 | head -n 1)
    printf 'The OpenRig watcher install checks its row path.\n\nWorktree: %s\nState: %s\nRig: %s\nRig version: %s\n' \
        "${worktree:-?}" "$state" "$rig" "${version_line:-?}" >"$T/row-check.md"
    if send_row "$row_dest" "OpenRig watcher installed: row path check" "$state" \
        "$T/row-check.md" yes ""; then
        echo "row-check: sent ${last_request_id:-?}"
        exit 0
    fi
    echo "row-check: failed"
    tail -n 5 -- "$last_rig_out"
    exit 1
fi

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
    printf 'upgrade destination: %s\n' "${upgrade_dest:-none}"
    printf 'harnesses: %s\n' "$harness_list"
    if [[ -z $failure_cmd ]]; then
        printf 'failure notifier: none\n'
    elif path=$(command -v -- "$failure_cmd" 2>/dev/null) && [[ -n $path ]]; then
        printf 'failure notifier: %s\n' "$path"
    else
        printf 'failure notifier: unresolved %s\n' "$failure_cmd"
    fi
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
cleanup() { rm -rf -- "${T:?}"; }
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

: >"$state/run.log"
row_log="$state/run.log"
log() { printf '%s %s\n' "$(now)" "$*" >>"$state/run.log"; }
failed_io=0
V=""

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
# (HACF_WATCH_ROW_DESTINATION, when set; R20): into the outbox first, then sent. Unset settings make
# no call.
row_failed=0 rows_sent=0 notify_verify=no tried=" "
notify() {
    local summary=$1 line=$2 ref=$3 body_text=$4 body cmd out name
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
        return 1
    }
    printf '%s\n' "$body_text" >"$body"
    out=$(python3 "$helper" watch-outbox add --state-dir "$state" --destination "$row_dest" \
        --summary "$summary" --evidence-ref "$ref" --body-file "$body" --verify "$notify_verify" \
        2>>"$state/run.log")
    local added=$?
    rm -f -- "$body"
    if [[ $added -ne 0 ]]; then
        log "the row failed: $summary (not written to the outbox)"
        row_failed=1
        return 1
    fi
    [[ $out != *"dropped "* ]] || failed_io=1
    name=$(sed -n 's/^entry //p' <<<"$out")
    deliver "$name" "$row_dest" "$summary" "$ref" "$notify_verify" ""
    return 0
}

# deliver <name> <dest> <summary> <ref> <verify> <request-id>: send one outbox entry once; remove it
# when it landed, else keep it (with its request id) for the next run.
deliver() {
    local name=$1 dest=$2 summary=$3 ref=$4 verify=$5 request=$6 body
    tried+="$name "
    body=$(mktemp -- "$T/row-XXXXXX.md") || {
        row_failed=1
        return
    }
    if ! python3 "$helper" watch-outbox body --state-dir "$state" --name "$name" >"$body" \
        2>>"$state/run.log"; then
        row_failed=1
        rm -f -- "$body"
        return
    fi
    if send_row "$dest" "$summary" "$ref" "$body" "$verify" "$request"; then
        python3 "$helper" watch-outbox remove --state-dir "$state" --name "$name" \
            >>"$state/run.log" 2>&1 || failed_io=1
        rows_sent=$((rows_sent + 1))
        log "row sent: $summary"
    else
        if [[ -n $last_request_id ]]; then
            python3 "$helper" watch-outbox mark --state-dir "$state" --name "$name" \
                --request-id "$last_request_id" >>"$state/run.log" 2>&1 || failed_io=1
        fi
        printf 'row-failed %s %s\n' "$(now)" "$summary" >>"$state/history.log" || failed_io=1
        printf '%s row-failed %s %s\n' "${V:--}" "$(now)" "$summary" >"$state/latest.md" ||
            failed_io=1
        log "the row failed: $summary"
        row_failed=1
    fi
    rm -f -- "$body"
}

# flush_outbox [final]: send up to 20 waiting entries, oldest first, once each. The final flush
# skips what this run already tried and stops when the budget is nearly spent.
flush_outbox() {
    local final=${1-} waiting name dest summary ref verify request
    [[ -n $row_dest ]] || return 0
    waiting=$(python3 "$helper" watch-outbox list --state-dir "$state" 2>>"$state/run.log") || {
        failed_io=1
        return
    }
    while IFS=$'\t' read -r name dest summary ref verify request; do
        [[ -n $name ]] || continue
        if [[ -n $final ]]; then
            [[ $tried != *" $name "* ]] || continue
            ((deadline - SECONDS > 120)) || break
        fi
        [[ $request != - ]] || request=""
        deliver "$name" "$dest" "$summary" "$ref" "$verify" "$request"
    done <<<"$waiting"
}

end() {
    last_run
    [[ $row_failed -eq 0 && $failed_io -eq 0 ]] || exit 1
    exit 0
}

# no_verdict <deferred|setup> <why> <evidence|-> [<compat detail>]: count a run without a verdict.
# The first setup error of the stream is a "checker broke" row; a second deferral in a row is one
# "runs without a verdict" row (each once until a verdict resets the stream).
no_verdict() {
    local event=$1 why=$2 ev=$3 detail=${4-} out n ref
    finish "$event" "$ev" no
    out=$(python3 "$helper" watch-state --state-dir "$state" --event "$event" 2>>"$state/run.log") || {
        failed_io=1
        return
    }
    ref=${ev/#-/$state}
    n=${out##* }
    case $out in
    "notify-setup "*)
        if [[ $detail == harness:* ]]; then
            notify "OpenRig watcher: checker could not run the harness step on ${V:--}" "$why" "$ref" \
                "$(printf 'The checker could not run the harness step on OpenRig %s (for example the candidate ships no community-harness parser module daemon/dist/domain/community-harness-presets.js). This is not by itself evidence that the checker is broken.\n\nStream: new-version\nDetail: %s\nVersion: %s\nEvidence: %s\nState: %s\nHistory: %s/history.log' \
                    "${V:--}" "$detail" "${V:--}" "$ev" "$state" "$state")"
        else
            notify "OpenRig watcher: checker broke" "$why" "$ref" \
                "$(printf 'The OpenRig watcher could not produce a verdict: the checker broke.\n\nStream: new-version\nCause: %s\nVersion: %s\nEvidence: %s\nState: %s\nHistory: %s/history.log' \
                    "$why" "${V:--}" "$ev" "$state" "$state")"
        fi
        ;;
    "notify-deferred "*)
        notify "OpenRig watcher: $n runs without a verdict" "$why" "$ref" \
            "$(printf 'The OpenRig watcher has had %s runs in a row without a verdict.\n\nLast: %s\nVersion: %s\nState: %s\nHistory: %s/history.log' \
                "$n" "$why" "${V:--}" "$state" "$state")"
        ;;
    esac
}

# budget_ok: enough of the run's budget is left for a gate wait and one compat run.
budget_ok() { ((deadline - SECONDS >= gate_max + compat_max + 60 + 600)); }

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
step3_v="" step3_ev="-" step3_rc=-1
compat_run() { # compat_run <version> <accept yes|no>: sets rc, evidence, verdict, accept_result, detail
    local version=$1 outfile="$T/compat.out" line accept=()
    [[ $2 == yes ]] && accept=(--accept)
    evidence="-" verdict="SETUP-ERROR" accept_result="-" detail=""
    log "running $compat $version ${accept[*]}"
    HACF_COMPAT_EVIDENCE_ROOT="$evidence_root" run_bg timeout --kill-after=60 "$compat_max" \
        bash "$compat" "$version" "${accept[@]}" "${reg[@]}" "${harness_args[@]}" >"$outfile" 2>&1
    rc=$?
    log "compat exit $rc"
    [[ $2 != yes ]] || step3_v=$version step3_rc=$rc step3_ev="-"
    line=$(grep -E "^openrig-compat ${version//./\\.}: (PASS|FAIL|REVIEW|SETUP-ERROR|SEED)( [^ ]+)? \(.+\)$" \
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
    [[ $2 != yes ]] || step3_ev=$evidence
    if ((rc >= 124)); then # 124 timeout, 128+n a signal (137: the OOM killer): setup, never FAIL
        detail="compat exit $rc (a signal, a timeout or an OOM kill)"
        return
    fi
    local fields
    fields=$(python3 "$helper" watch-verdict --evidence "$evidence" --version "$version" --rc "$rc" \
        2>>"$state/run.log") || fields=$'SETUP-ERROR\t-\twatch-verdict failed'
    IFS=$'\t' read -r verdict accept_result detail <<<"$fields"
}

# --- steps 1-6: the new version (every early exit returns, and the tail still runs) ------------------
npm_failed=0 integrity_ok=0
new_version_step() {
    # --- 1. npm: the latest version and the accepted entries' integrity -----------------------------------
    reg=()
    [[ -n $registry ]] && reg=(--registry "$registry")
    npm_view() { run_bg timeout 120 npm view --cache "$T/npm-cache" "${reg[@]}" "$@"; }

    if ! out=$(npm_view @openrig/cli version 2>>"$state/run.log") ||
        ! V=$(printf '%s' "$out" | python3 "$helper" npm-version 2>>"$state/run.log"); then
        V=""
        log "npm view @openrig/cli version failed or printed no plain version"
        npm_failed=1
        no_verdict setup "npm view @openrig/cli version failed" -
        return
    fi
    log "latest: $V"

    if ! entries=$(python3 "$helper" contract-entries --root "$worktree" 2>>"$state/run.log"); then
        log "the worktree's contract file is unreadable"
        no_verdict setup "the watcher worktree's contract file is unreadable" -
        return
    fi
    listed=0
    : >"$T/integrity.tsv"
    while IFS=$'\t' read -r ver want by; do
        [[ -n $ver ]] || continue
        [[ $ver == "$V" ]] && listed=1
        if ! got=$(npm_view "@openrig/cli@$ver" dist.integrity 2>>"$state/run.log") ||
            ! got=$(printf '%s' "$got" | python3 "$helper" integrity 2>>"$state/run.log"); then
            log "npm view @openrig/cli@$ver dist.integrity failed"
            npm_failed=1
            no_verdict setup "npm view @openrig/cli@$ver dist.integrity failed" -
            return
        fi
        printf '%s\t%s\n' "$ver" "$got" >>"$T/integrity.tsv"
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
    integrity_ok=1

    # --- 2. a final verdict or an accepted version: nothing to do ------------------------------------------
    if [[ $listed -eq 1 ]]; then
        log "$V is already in the contract file: nothing to do"
        return
    fi
    if python3 "$helper" watch-final --state-dir "$state" --version "$V" 2>>"$state/run.log"; then
        log "$V already has a final verdict"
        return
    fi

    if ! budget_ok; then
        no_verdict deferred "budget: $((deadline - SECONDS))s left" -
        return
    fi
    if ! gate_wait_open; then
        no_verdict deferred "the memory gate stayed closed for ${gate_max}s" -
        return
    fi
    compat_run "$V" yes
    if [[ $verdict == FAIL ]]; then
        count=$(python3 "$helper" watch-state --state-dir "$state" --event fail --version "$V" \
            2>>"$state/run.log") || count=2
        if [[ $count == 1 ]]; then
            log "FAIL ($detail): retrying once"
            if budget_ok && gate_open; then
                compat_run "$V" yes
                if [[ $verdict == FAIL ]]; then
                    python3 "$helper" watch-state --state-dir "$state" --event fail --version "$V" \
                        >/dev/null 2>>"$state/run.log"
                fi
            else
                python3 "$helper" watch-state --state-dir "$state" --event verdict >/dev/null \
                    2>>"$state/run.log"
                finish fail-pending "$evidence" no
                return
            fi
        fi
    fi

    # --- 6. notify -------------------------------------------------------------------------------------------
    if [[ $verdict == SETUP-ERROR || $verdict == SEED ]]; then
        no_verdict setup "compat $V: ${detail:-setup error}" "$evidence" "$detail"
        return
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
        python3 - "$V" "$evidence/verdict.json" >>"$T/integrity.tsv" 2>>"$state/run.log" <<'PY'
import json, sys
print(sys.argv[1], json.load(open(sys.argv[2], encoding="utf-8"))["integrity"], sep="\t")
PY
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
}

# --- the tail: runs after every outcome of the new-version step -----------------------------------
history_line() { printf '%s\n' "$1" >>"$state/history.log" || failed_io=1; }
self_event() { # self_event <verdict|deferred|setup>: one self-test stream event; prints the answer
    python3 "$helper" watch-state --state-dir "$state" --stream self-test --event "$1" \
        2>>"$state/run.log"
}
dropped_count() { python3 "$helper" watch-outbox dropped --state-dir "$state" 2>>"$state/run.log"; }

# --- step 4: the installed version -----------------------------------------------------------------
installed="" changed_from="" unparsed_why=""
installed_step() {
    local raw out
    raw=$(timeout 30 "$rig" --version 2>&1 | head -c 200)
    if ! out=$(python3 "$helper" watch-installed --state-dir "$state" --raw="$raw" \
        2>>"$state/run.log"); then
        failed_io=1
        out=unparsed
    fi
    case $out in
    "first "* | "same "*) installed=${out#* } ;;
    "changed "*) read -r _ changed_from installed <<<"$out" ;;
    *)
        raw=${raw//$'\n'/ }
        unparsed_why="rig --version unparsable: ${raw:0:80}"
        ;;
    esac
    log "installed: $out"
}

# --- step 5: the checker self-test on the installed version ----------------------------------------
selftest_line="not run"
self_deferred() { # self_deferred <why>
    local why=$1 out
    selftest_line="deferred: $why"
    history_line "selftest deferred: $why"
    out=$(self_event deferred) || {
        failed_io=1
        return
    }
    [[ $out == "notify-deferred "* ]] || return 0
    notify "OpenRig checker self-test: ${out##* } runs without a verdict" "$why" "$state" \
        "$(printf 'The checker self-test has had %s runs in a row without a verdict.\n\nLast: %s\nState: %s\nHistory: %s/history.log' \
            "${out##* }" "$why" "$state" "$state")"
}
selftest_step() {
    local ev src_rc out result text ref latch
    if [[ -z $installed ]]; then
        self_deferred "$unparsed_why"
        return
    fi
    if ((npm_failed)); then
        self_deferred "npm unavailable"
        return
    fi
    if [[ $step3_v == "$installed" ]]; then
        ev=$step3_ev src_rc=$step3_rc
    else
        if ! budget_ok; then
            self_deferred "budget"
            return
        fi
        if ! gate_wait_open; then
            self_deferred "gate"
            return
        fi
        compat_run "$installed" no
        ev=$evidence src_rc=$rc
    fi
    if ! out=$(python3 "$helper" watch-selftest --state-dir "$state" --worktree "$worktree" \
        --version "$installed" --evidence "$ev" --rc "$src_rc" --harness "$harness_list" \
        2>>"$state/run.log"); then
        failed_io=1
        selftest_line="the result could not be judged (see run.log)"
        return
    fi
    result=${out%%$'\t'*} text=${out#*$'\t'}
    ref=${ev/#-/$state}
    selftest_line="$result: $text"
    log "checker self-test $installed: $selftest_line"
    local body
    body=$(printf 'OpenRig checker self-test of %s (HACF'\''s checker against a fresh npm copy, not the host'\''s install).\n\nResult: %s\n%s\n\nEvidence: %s\nExpectation: %s/selftest/%s.json\nHistory: %s/history.log' \
        "$installed" "$result" "$text" "$ev" "$state" "$installed" "$state")
    case $result in
    ok)
        history_line "selftest $installed ok $(now) $ev"
        self_event verdict >/dev/null || failed_io=1
        ;;
    recorded-*)
        history_line "selftest $installed $result $(now) $ev"
        [[ $result != recorded-first ]] || text="unchecked: taken from its own first result; $text"
        notify "OpenRig checker self-test: expectation recorded for $installed" "$text" "$ref" \
            "$body"$'\n\n'"$text"
        self_event verdict >/dev/null || failed_io=1
        ;;
    broke)
        history_line "selftest $installed broke $(now) $ev $text"
        out=$(self_event setup) || failed_io=1
        [[ $out != "notify-setup "* ]] ||
            notify "OpenRig watcher: checker broke (self-test $installed)" "$text" "$ref" "$body"
        ;;
    nobasis-fail)
        history_line "selftest $installed no-expectation FAIL $(now) $ev"
        latch="$state/selftest/$installed.nobasis"
        if [[ ! -e $latch ]] &&
            notify "OpenRig checker self-test: no expectation for $installed (a FAIL is never an expectation)" \
                "$text" "$ref" "$body"; then
            mkdir -p -- "$state/selftest" && : >"$latch" || failed_io=1
        fi
        ;;
    *) failed_io=1 ;;
    esac
}

# --- step 6: the upgrade reconcile; rows go out only through `pending`, after `plan`'s re-check -----
upgrade_step() {
    local integrity=- before after pending kind key body last summary ref tags expires ok
    ((integrity_ok)) && integrity="$T/integrity.tsv"
    before=$(dropped_count) || failed_io=1
    if ! python3 "$helper" watch-upgrade plan --state-dir "$state" --worktree "$worktree" \
        --installed "${installed:--}" --integrity-file "$integrity" --now "${now_arg:-$(now)}" \
        --upgrade-dest "${upgrade_dest:--}" --row-dest "${row_dest:--}" >>"$state/run.log" 2>&1; then
        log "upgrade: the reconcile failed; nothing is sent"
        history_line "upgrade: the reconcile failed $(now) (see run.log)"
        failed_io=1
        return
    fi
    after=$(dropped_count) || failed_io=1
    [[ $before == "$after" ]] || failed_io=1
    pending=$(python3 "$helper" watch-upgrade pending --state-dir "$state" --out-dir "$T" \
        2>>"$state/run.log") || {
        failed_io=1
        return
    }
    while IFS=$'\t' read -r kind key body last; do
        [[ -n $kind ]] || continue
        if [[ -z $upgrade_dest ]]; then
            history_line "upgrade: no destination for the owed $kind $key"
            continue
        fi
        summary=$(cat -- "${body%.md}.summary") ref=$(cat -- "${body%.md}.evidence")
        tags=$(cat -- "${body%.md}.tags" 2>/dev/null) expires=$(cat -- "${body%.md}.expires" 2>/dev/null)
        [[ $last != - ]] || last=""
        if send_row "$upgrade_dest" "$summary" "$ref" "$body" yes "$last" "$tags" "$expires"; then
            ok=yes
            rows_sent=$((rows_sent + 1))
            history_line "upgrade: sent $kind $key $(now)"
        else
            ok=no
            row_failed=1
            history_line "row-failed $(now) $summary"
        fi
        before=$(dropped_count) || failed_io=1
        python3 "$helper" watch-upgrade sent --state-dir "$state" --kind "$kind" --key "$key" \
            --ok "$ok" --request-id "${last_request_id:--}" --row-dest "${row_dest:--}" \
            >>"$state/run.log" 2>&1 || failed_io=1
        after=$(dropped_count) || failed_io=1
        [[ $before == "$after" ]] || failed_io=1
    done <<<"$pending"
}

# --- step 7: the host's version changed ------------------------------------------------------------
after_step() {
    local request
    [[ -n $changed_from ]] || return 0
    request=$(python3 "$helper" watch-upgrade fulfilled --state-dir "$state" 2>>"$state/run.log") ||
        request="unknown (upgrade.json unreadable)"
    notify_verify=yes
    notify "OpenRig host: $changed_from -> $installed" "request: $request" "$state" \
        "$(printf 'The host'\''s OpenRig changed from %s to %s.\n\nRequest: %s\nchecker self-test (HACF'\''s checker against a fresh npm copy of %s, not the host'\''s install): %s\n\nState: %s\nHistory: %s/history.log' \
            "$changed_from" "$installed" "$request" "$installed" "$selftest_line" "$state" "$state")"
    notify_verify=no
}

dropped_step() {
    local dropped
    dropped=$(dropped_count) || {
        failed_io=1
        return
    }
    if [[ $dropped =~ ^[1-9][0-9]*$ && $rows_sent -gt 0 ]]; then
        if notify "OpenRig watcher: $dropped alerts dropped (outbox full)" \
            "$dropped queue rows were dropped" "$state" \
            "$(printf 'The outbox was full (50 entries) and %s alerts were dropped, oldest first. history.log names each one.\n\nState: %s\nHistory: %s/history.log' \
                "$dropped" "$state" "$state")"; then
            python3 "$helper" watch-outbox dropped --state-dir "$state" --reset >/dev/null \
                2>>"$state/run.log" || failed_io=1
        fi
    fi
}

tail_steps() {
    log "tail: start"
    installed_step
    selftest_step
    upgrade_step
    after_step
    dropped_step
    flush_outbox final
}

flush_outbox
new_version_step
tail_steps
end
