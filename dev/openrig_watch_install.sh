#!/usr/bin/env bash
# dev/openrig_watch_install.sh --worktree <path> --base <commit> [--notify-cmd <cmd>]
#     [--row-destination <session>] [--evidence-root <dir>] [--dry-run]
# dev/openrig_watch_install.sh --uninstall [--dry-run]
#
# Installs the daily OpenRig compat watcher for this user (release-0.6.2 R17, R19); idempotent, and
# it prints every path it writes or removes. Install:
#   1. resolves npm, node, uv and the host rig with `command -v` (any missing: exit 1, nothing
#      written), and --notify-cmd when given (unresolvable: exit 1, nothing written);
#   2. renders the host-only env file ~/.config/hacf-openrig-watch/env (PATH, HACF_WATCH_RIG,
#      HACF_WATCH_WORKTREE, and only when given HACF_WATCH_NOTIFY_CMD, HACF_WATCH_ROW_DESTINATION
#      and HACF_COMPAT_EVIDENCE_ROOT): the only place the host's tool paths and settings live.
#      Without them the watcher calls no notifier and no rig, and keeps its evidence under
#      ${XDG_STATE_HOME:-~/.local/state}/hacf/evidence;
#   3. creates the watcher worktree with `git worktree add -b swimlane/openrig-accepted <path>
#      <commit>` when <path> does not exist (an existing <path> must already be a worktree on that
#      branch); a write into the common .git;
#   4. copies dev/systemd/hacf-openrig-watch.{service,timer} to ~/.config/systemd/user/;
#   5. systemctl --user daemon-reload; systemctl --user enable --now hacf-openrig-watch.timer;
#   6. runs the watcher's --check-env under the unit's env file (systemd-run --user --wait --pipe)
#      and fails the install if it fails.
# --uninstall disables the timer and removes the two units and the env file, then reloads; it keeps
# the state dir, the evidence and the worktree with its branch (they may hold unmerged accept
# commits) and prints the `git worktree remove` command. --dry-run prints the actions and changes
# nothing: no file, no systemctl, no git. Linger stays off (the maintainer's choice).
set -uo pipefail

usage() {
    printf '%s\n' "usage: dev/openrig_watch_install.sh --worktree <path> --base <commit> [--dry-run]" \
        "           [--notify-cmd <cmd>] [--row-destination <session>] [--evidence-root <dir>]" \
        "       dev/openrig_watch_install.sh --uninstall [--dry-run]" >&2
}
die() {
    printf 'openrig_watch_install.sh: %s\n' "$1" >&2
    exit "${2:-1}"
}

worktree="" base="" dry=0 uninstall=0 notify_cmd="" row_dest="" evidence_root=""
while [[ $# -gt 0 ]]; do
    case $1 in
    --worktree | --base | --notify-cmd | --row-destination | --evidence-root)
        [[ $# -ge 2 && -n $2 && $2 != *$'\n'* ]] || {
            usage
            die "$1 needs a one-line value" 2
        }
        case $1 in
        --worktree) worktree=$2 ;;
        --base) base=$2 ;;
        --notify-cmd) notify_cmd=$2 ;;
        --row-destination) row_dest=$2 ;;
        --evidence-root) evidence_root=$2 ;;
        esac
        shift
        ;;
    --dry-run) dry=1 ;;
    --uninstall) uninstall=1 ;;
    -h | --help)
        usage
        exit 2
        ;;
    *)
        usage
        die "unknown argument $1" 2
        ;;
    esac
    shift
done

root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
[[ -n ${HOME-} && $HOME == /* ]] || die "HOME is not an absolute path"
conf_dir="$HOME/.config/hacf-openrig-watch"
env_file="$conf_dir/env"
unit_dir="$HOME/.config/systemd/user"
units=(hacf-openrig-watch.service hacf-openrig-watch.timer)
branch=swimlane/openrig-accepted
mode=""
[[ $dry -eq 1 ]] && mode="dry-run: "

say() { printf '%s%s\n' "$mode" "$*"; }
# act <description> <command...>: print it; run it unless --dry-run; a failure stops the install.
act() {
    local what=$1
    shift
    say "$what"
    [[ $dry -eq 1 ]] && return 0
    "$@" || die "failed: $what"
}

# --- --uninstall -------------------------------------------------------------------------------------
if [[ $uninstall -eq 1 ]]; then
    [[ -z $worktree$base$notify_cmd$row_dest$evidence_root ]] ||
        die "--uninstall takes no install option (--worktree, --base, --notify-cmd, ...)" 2
    old_worktree="" old_evidence=""
    if [[ -f $env_file ]]; then
        old_worktree=$(sed -n 's/^HACF_WATCH_WORKTREE=//p' -- "$env_file" | head -n 1)
        old_evidence=$(sed -n 's/^HACF_COMPAT_EVIDENCE_ROOT=//p' -- "$env_file" | head -n 1)
    fi
    old_evidence=${old_evidence:-${XDG_STATE_HOME:-$HOME/.local/state}/hacf/evidence}
    say "run systemctl --user disable --now hacf-openrig-watch.timer"
    if [[ $dry -eq 0 ]] && ! systemctl --user disable --now hacf-openrig-watch.timer; then
        say "the timer was not enabled (continuing: uninstall is idempotent)"
    fi
    for unit in "${units[@]}"; do
        act "remove $unit_dir/$unit" rm -f -- "$unit_dir/$unit"
    done
    act "remove $env_file" rm -f -- "$env_file"
    act "run systemctl --user daemon-reload" systemctl --user daemon-reload
    say "kept: $HOME/.local/state/hacf-openrig-watch, $old_evidence, the worktree and its branch $branch"
    if [[ -n $old_worktree ]]; then
        say "to remove the worktree (after merging any accept commits): git worktree remove $old_worktree"
    else
        say "to remove the worktree (after merging any accept commits): git worktree remove <path>"
    fi
    exit 0
fi

# --- install: arguments and tools, before anything is written -----------------------------------------
[[ -n $worktree && -n $base ]] || {
    usage
    die "install needs --worktree <path> and --base <commit>" 2
}
[[ $worktree == /* ]] || worktree="$(pwd)/$worktree"
worktree=$(realpath -m -- "$worktree")
[[ $base =~ ^[0-9a-fA-F]{7,64}$|^[A-Za-z0-9._/-]{1,200}$ ]] || die "--base $base is not a commit name" 2

missing=()
declare -A found=()
for tool in npm node uv rig; do
    path=$(command -v -- "$tool" 2>/dev/null || true)
    if [[ -z $path || $path != /* ]]; then
        missing+=("$tool")
    else
        found[$tool]=$(realpath -s -- "$path")
    fi
done
[[ ${#missing[@]} -eq 0 ]] || die "missing on PATH: ${missing[*]} (nothing written)"
notifier=""
if [[ -n $notify_cmd ]]; then
    notifier=$(command -v -- "$notify_cmd" 2>/dev/null || true)
    [[ -n $notifier && $notifier == /* ]] ||
        die "--notify-cmd $notify_cmd does not resolve on PATH (nothing written)"
fi
if [[ -n $evidence_root ]]; then
    [[ $evidence_root == /* ]] || evidence_root="$(pwd)/$evidence_root"
    evidence_root=$(realpath -m -- "$evidence_root")
fi

# PATH for the unit: npm's dir, node's dir (when npm's dir has no node), uv's, the notifier's (when
# --notify-cmd is given), then the system dirs; each once.
dirs=()
add_dir() {
    local d=$1 x
    for x in "${dirs[@]}"; do [[ $x == "$d" ]] && return; done
    dirs+=("$d")
}
add_dir "$(dirname -- "${found[npm]}")"
add_dir "$(dirname -- "${found[node]}")"
add_dir "$(dirname -- "${found[uv]}")"
[[ -n $notifier ]] && add_dir "$(dirname -- "$notifier")"
add_dir /usr/bin
add_dir /bin
unit_path=$(
    IFS=:
    printf '%s' "${dirs[*]}"
)
env_content=$(printf 'PATH=%s\nHACF_WATCH_RIG=%s\nHACF_WATCH_WORKTREE=%s\n' \
    "$unit_path" "${found[rig]}" "$worktree")
[[ -z $notify_cmd ]] || env_content+=$'\n'"HACF_WATCH_NOTIFY_CMD=$notify_cmd"
[[ -z $row_dest ]] || env_content+=$'\n'"HACF_WATCH_ROW_DESTINATION=$row_dest"
[[ -z $evidence_root ]] || env_content+=$'\n'"HACF_COMPAT_EVIDENCE_ROOT=$evidence_root"

# --- 2. the env file -----------------------------------------------------------------------------------
say "resolved: npm=${found[npm]} node=${found[node]} uv=${found[uv]} rig=${found[rig]} notifier=${notifier:-none}"
if [[ -f $env_file && $(cat -- "$env_file") == "$env_content" ]]; then
    say "unchanged $env_file"
else
    say "write $env_file:"
    printf '%s\n' "$env_content" | sed 's/^/    /'
    if [[ $dry -eq 0 ]]; then
        mkdir -p -- "$conf_dir" || die "cannot create $conf_dir"
        printf '%s\n' "$env_content" >"$env_file.tmp" && mv -f -- "$env_file.tmp" "$env_file" ||
            die "cannot write $env_file"
    fi
fi

# --- 3. the watcher worktree -----------------------------------------------------------------------------
if [[ -e $worktree ]]; then
    if [[ $dry -eq 1 ]]; then
        say "check $worktree is a worktree on $branch (git, skipped in --dry-run)"
    else
        top=$(git -C "$worktree" rev-parse --show-toplevel 2>/dev/null || true)
        on=$(git -C "$worktree" symbolic-ref --quiet --short HEAD 2>/dev/null || true)
        [[ $top == "$worktree" && $on == "$branch" ]] ||
            die "$worktree exists but is not a worktree on $branch"
        say "unchanged $worktree (a worktree on $branch)"
    fi
else
    act "run git -C $root worktree add -b $branch $worktree $base (writes $worktree and the common .git)" \
        git -C "$root" worktree add -b "$branch" "$worktree" "$base"
fi

# --- 4. the units -----------------------------------------------------------------------------------------
[[ $dry -eq 1 ]] || mkdir -p -- "$unit_dir" || die "cannot create $unit_dir"
for unit in "${units[@]}"; do
    src="$root/dev/systemd/$unit"
    [[ -f $src ]] || die "missing template $src"
    if [[ -f $unit_dir/$unit ]] && cmp -s -- "$src" "$unit_dir/$unit"; then
        say "unchanged $unit_dir/$unit"
    else
        act "copy $src -> $unit_dir/$unit" cp -- "$src" "$unit_dir/$unit"
    fi
done

# --- 5. systemd -------------------------------------------------------------------------------------------
act "run systemctl --user daemon-reload" systemctl --user daemon-reload
act "run systemctl --user enable --now hacf-openrig-watch.timer" \
    systemctl --user enable --now hacf-openrig-watch.timer

# --- 6. the watcher's own check, under the unit's env file ------------------------------------------------
act "run systemd-run --user --wait --pipe -p EnvironmentFile=$env_file /bin/bash $worktree/dev/openrig_watch.sh --check-env" \
    systemd-run --user --wait --pipe -p "EnvironmentFile=$env_file" /bin/bash \
    "$worktree/dev/openrig_watch.sh" --check-env
say "installed; disable with: systemctl --user disable --now hacf-openrig-watch.timer"
