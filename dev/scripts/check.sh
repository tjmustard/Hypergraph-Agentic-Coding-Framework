#!/usr/bin/env bash
# HACF's full dev check: run dev/tests under the uv 3.11 venv and under the system Python 3.10
# that the skills actually use, both under pytest-xdist, then ruff on the Python files this
# branch changed.
#
#   bash dev/scripts/check.sh
#
# HACF_PY310       the 3.10 interpreter (default /usr/bin/python3). Its leg fails, never skips,
#                  when that interpreter cannot import pytest, yaml and xdist.
# HACF_TEST_WORKERS  the xdist worker count for both legs: unset or empty for the memory-headroom
#                  budget (dev/scripts/check_env.sh), a positive integer, `auto`, or 0 (serial).
# HACF_TEST_TMPDIR the test temp dir. Unset: an inherited TMPDIR is honoured unless it is /tmp
#                  itself or on a tmpfs; otherwise ${XDG_CACHE_HOME:-~/.cache}/hacf-tmp/check.
#                  A temp dir inside the work tree is refused.
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
echo "==> [1/4] preflight: $PY310 can import pytest, yaml and xdist"
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
if ! W=$(hacf_test_workers "$ncpu" "$mem_mb"); then
    exit 2
fi
if [[ -n ${HACF_TEST_WORKERS-} ]]; then
    how="HACF_TEST_WORKERS=$HACF_TEST_WORKERS"
else
    how="budget: nproc $ncpu, MemAvailable ${mem_mb:-unknown} MB, headroom $HACF_TEST_HEADROOM_MB MB, $HACF_TEST_PER_WORKER_MB MB per worker, two suites"
fi
if [[ -z $W ]]; then
    XDIST=()
    echo "==> workers: serial ($how)"
else
    XDIST=(-n "$W" --dist "$HACF_TEST_DIST")
    echo "==> workers: $W ($how; --dist $HACF_TEST_DIST)"
fi

# --- 2. pytest under the uv-managed 3.11 venv ---
echo "==> [2/4] pytest (uv venv)"
uv run --locked pytest -m 'not live' "${XDIST[@]}"

# --- 3. pytest under the system 3.10 (the same binary the preflight checked) ---
echo "==> [3/4] pytest ($PY310)"
"$PY310" -m pytest -m 'not live' "${XDIST[@]}"

# --- 4. ruff on *.py changed since the merge-base with main, plus working-tree changes ---
echo "==> [4/4] ruff on changed Python files"
if ! base=$(git merge-base HEAD main); then
    echo "❌  check.sh: no merge-base between HEAD and main; cannot tell which files changed." >&2
    exit 1
fi
changed=()
while IFS= read -r -d '' path; do
    # Deleted paths are skipped: --diff-filter=d drops tracked deletions, -f the rest.
    if [[ $path == *.py && -f $path ]]; then
        changed+=("$path")
    fi
done < <({
    git diff --name-only -z --diff-filter=d "$base" --
    git ls-files --others --exclude-standard -z
} | sort -zu)

if [[ ${#changed[@]} -eq 0 ]]; then
    echo "    no changed Python files; nothing to lint"
else
    echo "    ${#changed[@]} file(s)"
    uv run --locked ruff check -- "${changed[@]}"
    uv run --locked ruff format --check -- "${changed[@]}"
fi
echo "✅  check.sh passed (uv venv and $PY310 legs, ruff)."
