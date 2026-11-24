#!/usr/bin/env bash
# HACF's full dev check: run dev/tests under the uv 3.11 venv and under the system Python 3.10
# that the skills actually use, both under pytest-xdist, then ruff check and ruff format --check.
# Ruff lints every tracked Python file on a fresh clone (no local main) or when HEAD is on main,
# and otherwise the Python files this branch changed since the merge-base with main; untracked
# Python files in both cases (hacf_ruff_files in dev/scripts/check_env.sh). Both ruff calls pass
# --force-exclude, so pyproject's extend-exclude (PRDs/) holds for the explicit paths.
# The fifth leg is the privacy scan of the work tree, `python3 dev/release_export.py scan --tree
# worktree` (the release gate's rules on the would-be export set; v064-upgrade-path R31). Its
# exit 3 (no digest file) is "unknown": named in the summary and the final line, neither a pass
# nor a failure.
#
#   bash dev/scripts/check.sh
#
# HACF_PY310       the 3.10 interpreter (default /usr/bin/python3). Its leg fails, never skips,
#                  when that interpreter cannot import pytest, yaml and xdist.
# HACF_TEST_WORKERS  the xdist worker count for both legs: unset or empty for the memory-headroom
#                  budget (dev/scripts/check_env.sh), a positive integer, `auto`, or 0 (serial).
#                  Unset while another (non-cooperating) heavy run is on the host, it is
#                  HACF_TEST_WORKERS_SHARED (default 4) instead.
# HACF_TEST_TMPDIR the test temp dir. Unset: an inherited TMPDIR is honoured unless it is /tmp
#                  itself or on a tmpfs; otherwise ${XDG_CACHE_HOME:-~/.cache}/hacf-tmp/check.
#                  A temp dir inside the work tree is refused.
#
# Host lock: right after the preflight, check.sh re-runs itself under
# `python3 dev/scripts/host_gate.py run --job check.sh`, which holds the per-user heavy-job lock
# (${XDG_STATE_HOME:-~/.local/state}/hacf/heavy-job.lock) for the whole run, so one HACF heavy job
# runs on the host at a time; it first waits for MemAvailable >= HACF_HOST_MIN_MB (6144). A run
# inside a lease (HACF_HEAVY_LOCK_HELD holds a valid token, as under the release gate) does not
# re-run itself. HACF_HEAVY_WAIT_S (3600) bounds the wait; `host_gate.py status` shows the holder.
# Exit 75 means the wait ran out: the host was busy, retry later. It is never a test verdict.
#
# Output: every leg runs, even after an earlier leg failed, and check.sh exits 1 at the end when
# any leg failed (the preflight, the temp and the worker refusals still stop it at once). Each
# leg's full output goes to a log, and pytest's junit XML next to it, in a run dir
# $HACF_LOG_DIR/<UTC %Y%m%dT%H%M%SZ>-<pid> (HACF_LOG_DIR defaults to
# ${XDG_STATE_HOME:-~/.local/state}/hacf/check-logs); only the newest 10 run dirs are kept (older
# ones are removed by exact name; nothing else there is touched). The terminal gets one summary
# per leg from dev/scripts/summarize_pytest.py: status, counts, failing ids with the tail of their
# tracebacks, and the log path; with no junit XML, the log's last 40 lines.
# HACF_CHECK_VERBOSE=1 also prints every leg's full output as it runs (the release gate sets it).
#
# The cache provider is on, so `uv run --locked pytest --lf` reruns the last failures
# (.pytest_cache/ at the repo root ignores itself). Every `uv run` uses --locked, so this script
# never rewrites uv.lock. Live tests (marker `live`) never run here.
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
cd "$ROOT"
PY310=${HACF_PY310:-/usr/bin/python3}
# The checks leave no bytecode in the work tree.
export PYTHONDONTWRITEBYTECODE=1

# --- 1. Preflight: the 3.10 leg must be able to run (before any pytest leg) ---
echo "==> [1/5] preflight: $PY310 can import pytest, yaml and xdist"
if ! "$PY310" -c 'import pytest, yaml, xdist'; then
    # The lock's versions, read only on this failure path.
    read -r xv ev < <(uv run --locked python -c \
        'import xdist, execnet; print(xdist.__version__, execnet.__version__)' 2>/dev/null ||
        echo "<lock> <lock>")
    echo "❌  check.sh: $PY310 cannot import pytest, yaml and xdist, so the Python 3.10 leg cannot run." >&2
    echo "    This is a failure, not a skip. Install them for that interpreter, for example:" >&2
    echo "      uv pip install --python \"$PY310\" 'pytest-xdist==$xv' 'execnet==$ev'" >&2
    echo "      \"$PY310\" -m pip install --user 'pytest>=8' 'pyyaml>=5.4' 'pytest-xdist==$xv' 'execnet==$ev'" >&2
    echo "    or point HACF_PY310 at a Python 3.10 that has them." >&2
    exit 1
fi
xdist_310=$("$PY310" -c 'import xdist; print(xdist.__version__)')
xdist_uv=$(uv run --locked python -c 'import xdist; print(xdist.__version__)')
echo "    xdist $xdist_310 ($PY310), $xdist_uv (uv venv)"
if [[ $xdist_310 != "$xdist_uv" ]]; then
    echo "    WARNING: the two legs run different pytest-xdist versions ($xdist_310 and $xdist_uv)."
fi

# --- The host heavy-job lock (dev/scripts/host_gate.py): re-run under it unless a lease is held ---
HOST_GATE="$ROOT/dev/scripts/host_gate.py"
heavy_py() {
    python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); import host_gate as h; '"$1" \
        "$ROOT/dev/scripts"
}
if ! heavy_py 'sys.exit(0 if h.token_valid() else 1)'; then
    exec python3 "$HOST_GATE" run --job check.sh -- bash "$ROOT/dev/scripts/check.sh" "$@"
fi

# shellcheck source=dev/scripts/check_env.sh
source "$ROOT/dev/scripts/check_env.sh"

# --- Test temp: on disk and outside the work tree (never /tmp's tmpfs) ---
if ! picked=$(hacf_pick_tmpdir "$ROOT"); then
    exit 1
fi
TMPDIR=${picked%%$'\t'*}
tmp_reason=${picked#*$'\t'}
mkdir -p -- "$TMPDIR"
export TMPDIR
echo "==> test temp: $TMPDIR ($tmp_reason)"
# Earlier runs' pytest-N dirs. pytest keeps the newest one and a killed run keeps its own; they
# are reported, never deleted here.
shopt -s nullglob
leftover=("$TMPDIR/pytest-of-$(id -un)"/pytest-[0-9]*)
shopt -u nullglob
if [[ ${#leftover[@]} -gt 0 ]]; then
    echo "    ${#leftover[@]} pytest-* dir(s) already under $TMPDIR/pytest-of-$(id -un) (killed runs keep theirs up to 3 days)"
fi

# --- Workers: the memory-headroom budget unless HACF_TEST_WORKERS overrides it ---
ncpu=$(nproc)
mem_mb=$(awk '/^MemAvailable:/ { print int($2 / 1024) }' /proc/meminfo 2>/dev/null || true)
# Another heavy run that does not take the lock (another project's pytest) only lowers the count.
hint="" others=""
if [[ -z ${HACF_TEST_WORKERS-} ]]; then
    # Prints "<workers> <count>" when another heavy run exists, nothing otherwise.
    read -r hint others < <(heavy_py \
        'n = h.non_cooperating(); n and print(h.workers_hint(n), len(n))') || true
    if [[ -n $hint ]]; then
        HACF_TEST_WORKERS=$hint
    fi
fi
if ! W=$(hacf_test_workers "$ncpu" "$mem_mb"); then
    exit 2
fi
if [[ -n $others ]]; then
    how="another heavy run: $others"
elif [[ -n ${HACF_TEST_WORKERS-} ]]; then
    how="HACF_TEST_WORKERS=$HACF_TEST_WORKERS"
else
    how="budget: nproc $ncpu, MemAvailable ${mem_mb:-unknown} MB, headroom $HACF_TEST_HEADROOM_MB MB, $HACF_TEST_PER_WORKER_MB MB per worker, two suites"
fi
if [[ -z $W ]]; then
    XDIST=()
    echo "==> workers: serial ($how)"
else
    XDIST=(-n "$W" --dist "$HACF_TEST_DIST")
    if [[ -n $others ]]; then
        echo "==> workers: $W ($how) --dist $HACF_TEST_DIST"
    else
        echo "==> workers: $W ($how; --dist $HACF_TEST_DIST)"
    fi
fi

# --- Logs: one run dir per run, the newest 10 kept ---
LOGROOT=${HACF_LOG_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/hacf/check-logs}
RUN_DIR=$LOGROOT/$(date -u +%Y%m%dT%H%M%SZ)-$$
mkdir -p -- "$LOGROOT"
mkdir -m 0700 -- "$RUN_DIR"
# Run dirs only: real directories (never a symlink) named like RUN_DIR, removed one exact path
# at a time; no glob reaches rm.
runs=()
while IFS= read -r -d '' name; do
    if [[ $name =~ ^[0-9]{8}T[0-9]{6}Z-[0-9]+$ && -d $LOGROOT/$name && ! -L $LOGROOT/$name ]]; then
        runs+=("$name")
    fi
done < <(find "$LOGROOT" -mindepth 1 -maxdepth 1 -type d -printf '%f\0' | sort -z)
for ((i = 0; i < ${#runs[@]} - 10; i++)); do
    rm -rf -- "${LOGROOT:?}/${runs[i]}"
done
echo "==> logs: $RUN_DIR"

SUMMARIZE=(python3 "$ROOT/dev/scripts/summarize_pytest.py")
failed=()
# run_leg <log> <command...>: run one leg command, its output appended to <log> (and printed too
# under HACF_CHECK_VERBOSE=1); its exit code in LEG_RC. Exit 75 (host busy) ends the run at once.
run_leg() {
    local log=$1
    shift
    set +e
    if [[ ${HACF_CHECK_VERBOSE-} == 1 ]]; then
        "$@" 2>&1 | tee -a -- "$log"
        LEG_RC=${PIPESTATUS[0]}
    else
        "$@" >>"$log" 2>&1
        LEG_RC=$?
    fi
    set -e
    if [[ $LEG_RC -eq 75 ]]; then
        echo "❌  check.sh: the host is busy (exit 75 from: $*); retry later. Log: $log" >&2
        exit 75
    fi
}
# pytest_leg <label> <stem> <command...>: one pytest leg, its summary, its verdict.
pytest_leg() {
    local label=$1 stem=$2
    shift 2
    echo "==> $label"
    run_leg "$RUN_DIR/$stem.log" "$@" --junitxml="$RUN_DIR/$stem.xml"
    "${SUMMARIZE[@]}" --label "$label" --exit "$LEG_RC" --log "$RUN_DIR/$stem.log" \
        --junitxml "$RUN_DIR/$stem.xml" || true
    if [[ $LEG_RC -ne 0 ]]; then
        failed+=("$label")
    fi
}

# --- 2. pytest under the uv-managed 3.11 venv ---
pytest_leg "[2/5] pytest (uv venv)" 2-pytest-uv uv run --locked pytest -m 'not live' "${XDIST[@]}"

# --- 3. pytest under the system 3.10 (the same binary the preflight checked) ---
pytest_leg "[3/5] pytest ($PY310)" 3-pytest-py310 "$PY310" -m pytest -m 'not live' "${XDIST[@]}"

# --- 4. ruff: every tracked *.py on a fresh clone or on main, else those changed since the
# merge-base with main; untracked *.py in both cases (hacf_ruff_mode, hacf_ruff_files) ---
echo "==> [4/5] ruff on Python files"
RUFF_LOG=$RUN_DIR/4-ruff.log
: >"$RUFF_LOG"
# The leg's own lines go where its commands' output goes.
ruff_say() {
    echo "$1" >>"$RUFF_LOG"
    if [[ ${HACF_CHECK_VERBOSE-} == 1 ]]; then
        echo "    $1"
    fi
}
ruff_rc=0
if ! ruff_mode=$(hacf_ruff_mode 2>>"$RUFF_LOG"); then
    # This leg's FAIL, not an early exit: the message is in its log and its summary.
    ruff_say "❌  check.sh: no merge-base between HEAD and main; cannot tell which files changed."
    ruff_rc=1
    ruff_note="no merge-base"
else
    ruff_files=()
    while IFS= read -r -d '' path; do
        ruff_files+=("$path")
    done < <(hacf_ruff_files 2>>"$RUFF_LOG")

    if [[ $ruff_mode == all && ${#ruff_files[@]} -eq 0 ]]; then
        # Never a vacuous pass: the all-files mode with no Python file is this leg's FAIL.
        ruff_say "❌  check.sh: vacuous: 0 tracked .py files"
        ruff_rc=1
        ruff_note="vacuous: 0 tracked .py files"
    elif [[ ${#ruff_files[@]} -eq 0 ]]; then
        ruff_say "no changed Python files; nothing to lint"
        ruff_note="no changed Python files"
    else
        if [[ $ruff_mode == all ]]; then
            ruff_note="all tracked Python files (${#ruff_files[@]}): fresh clone or HEAD on main"
        else
            ruff_note="${#ruff_files[@]} changed file(s) since the merge-base with main"
        fi
        ruff_say "$ruff_note"
        run_leg "$RUFF_LOG" uv run --locked ruff check --force-exclude -- "${ruff_files[@]}"
        ruff_rc=$LEG_RC
        run_leg "$RUFF_LOG" uv run --locked ruff format --check --force-exclude -- "${ruff_files[@]}"
        if [[ $ruff_rc -eq 0 ]]; then
            ruff_rc=$LEG_RC
        fi
    fi
fi
"${SUMMARIZE[@]}" --raw --label "[4/5] ruff" --exit "$ruff_rc" --note "$ruff_note" \
    --log "$RUFF_LOG" || true
if [[ $ruff_rc -ne 0 ]]; then
    failed+=("[4/5] ruff")
fi

# --- 5. privacy scan of the work tree: exit 0 pass, 1 or 2 fail, 3 unknown (no digest file) ---
echo "==> [5/5] privacy scan of the work tree"
SCAN_LOG=$RUN_DIR/5-privacy-scan.log
: >"$SCAN_LOG"
run_leg "$SCAN_LOG" python3 "$ROOT/dev/release_export.py" scan --tree worktree
scan_unknown=""
if [[ $LEG_RC -eq 3 ]]; then
    scan_unknown=1
    echo "    [5/5] privacy scan: unknown (no digest file)"
    echo "      log: $SCAN_LOG"
else
    scan_note=$(grep -m1 '^scan worktree: ' "$SCAN_LOG" || true)
    "${SUMMARIZE[@]}" --raw --label "[5/5] privacy scan" --exit "$LEG_RC" --note "$scan_note" \
        --log "$SCAN_LOG" || true
    if [[ $LEG_RC -ne 0 ]]; then
        failed+=("[5/5] privacy scan")
    fi
fi

if [[ ${#failed[@]} -gt 0 ]]; then
    joined=$(printf '%s, ' "${failed[@]}")
    echo "❌  check.sh failed: ${joined%, }" >&2
    exit 1
fi
if [[ -n $scan_unknown ]]; then
    echo "✅  check.sh passed (uv venv and $PY310 legs, ruff; privacy scan unknown)."
else
    echo "✅  check.sh passed (uv venv and $PY310 legs, ruff, privacy scan)."
fi
