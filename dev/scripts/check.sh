#!/usr/bin/env bash
# HACF v0.6.0 slice oracle: run dev/tests under the uv 3.11 venv and under the system Python
# 3.10 that the skills actually use, then ruff on the Python files this branch changed.
#
#   bash dev/scripts/check.sh
#
# The 3.10 interpreter is ${HACF_PY310:-/usr/bin/python3}. Its leg fails, never skips, when that
# interpreter cannot import pytest and yaml. Every `uv run` uses --locked, so this script never
# rewrites uv.lock. Live tests (marker `live`) never run here.
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
cd "$ROOT"
PY310=${HACF_PY310:-/usr/bin/python3}
# The checks leave no bytecode in the work tree.
export PYTHONDONTWRITEBYTECODE=1

# --- 1. Preflight: the 3.10 leg must be able to run (before any pytest leg) ---
echo "==> [1/4] preflight: $PY310 can import pytest and yaml"
if ! "$PY310" -c 'import pytest, yaml'; then
    echo "❌  check.sh: $PY310 cannot import pytest and yaml, so the Python 3.10 leg cannot run." >&2
    echo "    This is a failure, not a skip. Install them for that interpreter, for example:" >&2
    echo "      $PY310 -m pip install --user 'pytest>=8' 'pyyaml>=5.4'" >&2
    echo "    or point HACF_PY310 at a Python 3.10 that has them." >&2
    exit 1
fi

# --- 2. pytest under the uv-managed 3.11 venv ---
echo "==> [2/4] pytest (uv venv)"
uv run --locked pytest -m 'not live'

# --- 3. pytest under the system 3.10 (the same binary the preflight checked) ---
echo "==> [3/4] pytest ($PY310)"
"$PY310" -m pytest -m 'not live'

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
