# shellcheck shell=bash
# Test-environment helpers for dev/scripts/check.sh, which sources this file.
#
# Sourcing it has no side effects: it defines three constants and six functions, and the
# functions only print or return non-zero. They never create a directory, export a variable or
# change the working directory; check.sh does that with what they print.

# Memory kept free for everything else on the host (the other seat, the desktop, another project's runs).
HACF_TEST_HEADROOM_MB=4096
# Peak resident memory one xdist worker is budgeted (slice 3 sets the measured value).
HACF_TEST_PER_WORKER_MB=256
# The xdist distribution mode (slice 3 sets the measured mode; `loadgroup` once any test uses
# xdist_group).
HACF_TEST_DIST=loadgroup

# hacf_worker_budget <nproc> <memavail_mb>
# Print max(1, min(nproc / 2, (memavail_mb - HEADROOM) / (2 * PER_WORKER))), integer division,
# a negative term counted as 0. The memory term is sized for two suites at once (the builder and
# the auditor). An empty memavail_mb (no /proc/meminfo) drops the memory term.
hacf_worker_budget() {
    local nproc=${1:-1} mem=${2-} n m
    n=$((nproc / 2))
    if [[ -n $mem ]]; then
        m=$(((mem - HACF_TEST_HEADROOM_MB) / (2 * HACF_TEST_PER_WORKER_MB)))
        if [[ $m -lt 0 ]]; then
            m=0
        fi
        if [[ $m -lt $n ]]; then
            n=$m
        fi
    fi
    if [[ $n -lt 1 ]]; then
        n=1
    fi
    printf '%s\n' "$n"
}

# hacf_test_workers <nproc> <memavail_mb>
# Print the -n value check.sh passes to both pytest legs. HACF_TEST_WORKERS unset or empty: the
# budget. `auto`: auto. A positive integer without leading zeros: itself. `0`: nothing (serial:
# no -n and no --dist). Anything else: a message on stderr and status 2.
hacf_test_workers() {
    local value=${HACF_TEST_WORKERS-}
    if [[ -z $value ]]; then
        hacf_worker_budget "${1-}" "${2-}"
    elif [[ $value == auto || $value =~ ^[1-9][0-9]*$ ]]; then
        printf '%s\n' "$value"
    elif [[ $value == 0 ]]; then
        return 0
    else
        printf '❌  check.sh: HACF_TEST_WORKERS=%s is not valid; use a positive integer, auto, 0 (serial) or leave it unset (the memory-headroom budget).\n' "$value" >&2
        return 2
    fi
}

# _hacf_fs_type <path>: the filesystem type of the nearest existing ancestor of <path>.
_hacf_fs_type() {
    local p=$1
    while [[ ! -e $p && $p != / ]]; do
        p=$(dirname -- "$p")
    done
    stat -f -c %T -- "$p" 2>/dev/null || true
}

# hacf_pick_tmpdir <root>
# Print "<dir>\t<reason>": HACF_TEST_TMPDIR if set; else an inherited TMPDIR that is neither /tmp
# itself nor on a tmpfs; else ${XDG_CACHE_HOME:-$HOME/.cache}/hacf-tmp/check. The path is resolved
# with `realpath -m`. A dir equal to or under <root> is refused on stderr with status 1.
hacf_pick_tmpdir() {
    local root dir reason inherited=${TMPDIR-}
    root=$(realpath -m -- "$1")
    if [[ -n ${HACF_TEST_TMPDIR-} ]]; then
        dir=$HACF_TEST_TMPDIR
        reason=HACF_TEST_TMPDIR
    else
        dir="${XDG_CACHE_HOME:-$HOME/.cache}/hacf-tmp/check"
        reason=default
        if [[ -n $inherited ]]; then
            if [[ $(realpath -m -- "$inherited") == /tmp ]]; then
                reason="default; inherited TMPDIR $inherited is /tmp"
            elif [[ $(_hacf_fs_type "$inherited") == tmpfs ]]; then
                reason="default; inherited TMPDIR $inherited is on tmpfs"
            else
                dir=$inherited
                reason="inherited TMPDIR"
            fi
        fi
    fi
    dir=$(realpath -m -- "$dir")
    if [[ $dir == "$root" || $dir == "$root"/* ]]; then
        printf 'test temp %s is inside the work tree %s; refusing\n' "$dir" "$root" >&2
        return 1
    fi
    printf '%s\t%s\n' "$dir" "$reason"
}

# hacf_ruff_mode (run in the repository's work tree)
# Print `all` when there is no local `main` (a fresh clone of another branch) or when HEAD is the
# merge-base of HEAD and main (HEAD on main); otherwise print that merge-base. A `main` that shares
# no merge-base with HEAD: git's message on stderr and status 1.
hacf_ruff_mode() {
    local base head
    if ! git rev-parse -q --verify refs/heads/main >/dev/null; then
        printf 'all\n'
        return 0
    fi
    base=$(git merge-base HEAD main) || return 1
    head=$(git rev-parse HEAD) || return 1
    if [[ $base == "$head" ]]; then
        printf 'all\n'
    else
        printf '%s\n' "$base"
    fi
}

# hacf_ruff_files (run in the repository's work tree)
# Print, NUL-separated and sorted, the Python files check.sh's ruff leg lints: in the `all` mode of
# hacf_ruff_mode every tracked *.py, otherwise the paths changed since the merge-base
# (--diff-filter=d); untracked *.py in both modes. Paths missing from the work tree are skipped.
# Status 1 when hacf_ruff_mode fails.
hacf_ruff_files() {
    local mode path
    mode=$(hacf_ruff_mode) || return 1
    while IFS= read -r -d '' path; do
        if [[ $path == *.py && -f $path ]]; then
            printf '%s\0' "$path"
        fi
    done < <({
        if [[ $mode == all ]]; then
            git ls-files -z -- '*.py'
        else
            git diff --name-only -z --diff-filter=d "$mode" --
        fi
        git ls-files --others --exclude-standard -z
    } | sort -zu)
}
