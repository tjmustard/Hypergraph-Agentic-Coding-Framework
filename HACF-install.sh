#!/usr/bin/env bash
# Platform guard (R24): the first command, in bash-3.2 syntax, before any bash-4 construct.
if [ "${HACF_TEST:-}" = "1" ] && [ -n "${HACF_TEST_BASH_MAJOR:-}" ]; then
  _hacf_bash_major="$HACF_TEST_BASH_MAJOR"   # test-only override (BASH_VERSINFO is read-only)
else
  _hacf_bash_major="${BASH_VERSINFO[0]:-0}"
fi
case "$_hacf_bash_major" in ''|*[!0-9]*) _hacf_bash_major=0 ;; esac
if [ "$_hacf_bash_major" -lt 4 ]; then
  echo "❌  HACF-install.sh needs bash 4 or newer; this is bash ${BASH_VERSION:-unknown}." >&2
  echo "    macOS ships bash 3.2: install a newer one with Homebrew (brew install bash)," >&2
  echo "    then run: \"\$(brew --prefix)/bin/bash\" HACF-install.sh" >&2
  echo "    Nothing was written." >&2
  exit 1
fi
set -euo pipefail
# The runs write no __pycache__ into the project (D1).
export PYTHONDONTWRITEBYTECODE=1

# Hypergraph Agentic Coding Framework — consent-first installer and upgrader (v0.6.0)
#
# Supported platforms: Linux (Ubuntu 22.04+, Debian 12+) and WSL2. macOS is best-effort (needs
# bash >= 4 from Homebrew). Hashing uses Python hashlib; no GNU-only tool is required.
#
# Fresh install (interactive):
#   curl -sSL https://raw.githubusercontent.com/tjmustard/Hypergraph-Agentic-Coding-Framework/main/HACF-install.sh -o HACF-install.sh && bash HACF-install.sh
# Fresh install (non-interactive; downloads nothing third-party and consents to nothing):
#   curl -sSL https://raw.githubusercontent.com/tjmustard/Hypergraph-Agentic-Coding-Framework/main/HACF-install.sh | bash -s -- -y --ides=claude
# From a local checkout (one commit object, never its working tree; no network):
#   bash /path/to/hacf/HACF-install.sh --source=/path/to/hacf [--ref=<commit|branch|tag>]
#   --ref defaults to HEAD and is printed as a full sha before any write. --source cannot be
#   combined with --repo-url or --branch, and --dry-run with --source still fetches into the
#   temp dir. The absolute path goes only to the gitignored .agents/state/install/source.json.
# Re-running on a project installed from a local checkout (D1, v0.6.3):
#   bash HACF-install.sh                      reinstalls the manifest's installed_commit; refuses
#                                             (writes nothing) when the recorded checkout's HEAD moved
#   bash HACF-install.sh --ref=<commit>       moves to that commit of the recorded checkout
#   --source=<the recorded checkout> without --ref is pinned the same way; another checkout is a
#   new source (its HEAD). This protects re-runs of a v0.6.3+ installer; a checkout moved to an
#   older commit runs that commit's older installer.
#
# Consent: -y and a run without a terminal never consent to a download, a third-party install
# or a user-level change. Each such action runs only when its own flag is given, or when you
# answer y on /dev/tty. Every consent prompt shows the upstream URL and commit.
#
# Design: the installer section of HACF's design notes (MiniPRD S12b).
#
# Manifest (.agents/hacf-manifest.json), read by the uninstaller and /hyper-update:
#   {version, installed_commit, installed_at,
#    files: {path: {sha256, owner}},                 framework files this installer copied
#    settings_rules: {allow|ask|deny: [rule, ..]},   .claude/settings.json rules HACF added
#    hooks: {<settings event>: [command, ..],        .claude/settings.json hooks HACF added
#            git: {<hook>: {path, sha256}}},         git hooks this installer installed
#    blocks: {path: {block id: body sha256}},        HACF managed blocks (hyper_blocks body_sha256)
#    mcp_entries: {path: {server: entry}},           MCP entries HACF merged
#    source: {kind: local, commit} | {kind: remote, url, branch, commit},
#    ides: [id, ..],                                 the IDE selection (sorted; absent when none
#                                                    was ever recorded); an upgrade keeps it
#    rig_spec: [{path, commit} | {outside_project: true, commit}, ..]}
#                                                    rig spec copies made by --rig (absent when
#                                                    none); the copy is the user's, never refreshed
# `source` is the provenance recorded at install time; `installed_commit` is the authority, and a
# reader treats a `source.commit` that differs from it as stale. An upgrade with no --source,
# --repo-url or --branch reuses a recorded local source (the checkout's current HEAD), and one
# without --ides reuses `ides`.

usage() {
  cat <<'USAGE'
Usage: bash HACF-install.sh [options]

Supported: Linux (Ubuntu 22.04+, Debian 12+) and WSL2. macOS: best-effort, needs bash >= 4.

Selection:
  -y, --yes                  answer every question with its default; consent to nothing
  --ides=a,b | all           IDEs: claude, antigravity, windsurf, cursor, cline, roo, universal
  --mode=M                   upgrade mode: full | skills | system | ide | repair | dry-run
  --files=a,b                install named files only, skipping existing ones
  --preserve-custom          leave AGENTS.md, CLAUDE.md and GEMINI.md untouched
  --dry-run                  print every write, merge and download; write nothing

Consent flags (each one is the consent for its action):
  --install-deps             pip install --user pyyaml when PyYAML is missing
  --code-graph=none|codebase-memory-mcp   select the code-graph backend
  --install-code-graph       install codebase-memory-mcp 0.12.1 (uv tool, uvx or vendor script)
  --statusline               add a statusLine running `npx -y ccstatusline@latest`
  --migrate                  print (or, interactively, run) the v0.6.0 migration plan
  --rig[=DEST]  copy HACF's OpenRig team spec to openrig/hacf/ (or DEST); never over an existing path

Other:
  --firewall=auto|off        render .agentignore into each selected IDE (default auto)
  --no-mcp-entry             write no codebase-memory-mcp MCP entry and leave an existing one
                             alone; recorded, so a plain re-run keeps skipping it
  --mcp-entry                write the entry again (reverses a recorded --no-mcp-entry)
  --repo-url=URL             upstream repository (https:// only)
  --branch=NAME              upstream branch (default main)
  --source=PATH              install from a commit of a local HACF git checkout (never its
                             working tree); cannot be combined with --repo-url or --branch
  --ref=REF                  commit, branch or tag in --source (default HEAD); printed as a
                             full sha before any write. Without --source on a project installed
                             from a local checkout: moves to REF there. A re-run without --ref
                             (or with --source= that checkout) reinstalls installed_commit and
                             refuses when the checkout's HEAD moved
                             (--dry-run with --source still fetches into the temp dir)
  -h, --help                this text
USAGE
}

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
HACF_VERSION="0.6.7"
DEFAULT_REPO_URL="https://github.com/tjmustard/Hypergraph-Agentic-Coding-Framework.git"
DEFAULT_BRANCH="main"
CBM_VERSION="0.12.1"
CBM_VENDOR_URL="https://raw.githubusercontent.com/DeusData/codebase-memory-mcp/v${CBM_VERSION}/install.sh"
CBM_DOWNLOAD_URL="https://github.com/DeusData/codebase-memory-mcp/releases/download/v${CBM_VERSION}"
STATUSLINE_COMMAND="npx -y ccstatusline@latest"
MANIFEST_REL=".agents/hacf-manifest.json"
SOURCE_FILE_REL=".agents/state/install/source.json"   # per-machine, gitignored (--source path)
SOURCE_SCHEMA="hacf.install-source/v1"
# C2 (OPR.99.0.10.3): the installer copy each install saves, checked against the manifest's
# installer_sha256 when it is re-run (corruption detection, not a security boundary).
SAVED_INSTALLER_REL=".agents/state/hacf/installer/HACF-install.sh"
SELF_SCRIPT="${BASH_SOURCE[0]:-}"   # this script's absolute path; empty under `curl | bash`
if [ -n "$SELF_SCRIPT" ] && [ -f "$SELF_SCRIPT" ]; then
  SELF_SCRIPT="$(cd -- "$(dirname -- "$SELF_SCRIPT")" && pwd -P)/$(basename -- "$SELF_SCRIPT")"
else
  SELF_SCRIPT=""
fi
FRAGMENT_REL=".agents/install-templates/claude-settings.fragment.json"

# IDE definitions: "id|Display Name|directories (space-sep)|files (space-sep)".
# The claude entry installs .claude/commands only; .claude/settings.json is merged (FLOW 9).
IDE_DEFS=(
  "claude|Claude Code|.claude/commands|CLAUDE.md"
  "antigravity|Antigravity / Gemini CLI||GEMINI.md"
  "windsurf|Windsurf|.windsurf|"
  "cursor|Cursor|.cursor|"
  "cline|Cline|.clinerules|"
  "roo|Roo Code|.roo|"
  "universal|Universal — AGENTS.md  (GitHub Copilot, Zed, and others)||AGENTS.md"
)

# IDE subdirectories with per-skill bridge files, synced with .agents/skills in skills mode.
SKILL_BRIDGE_DIRS=(".claude/commands" ".windsurf/workflows")

# The explicit copy list (Task 34): framework dirs under .agents/ and the root files below.
AGENT_SYSTEM_SUBDIRS=("scripts" "schemas" "config" "rules" "install-templates")
ROOT_COPY_FILES=(".agentignore" "HACF-uninstall.sh")
INSTRUCTION_FILES=("AGENTS.md" "CLAUDE.md" "GEMINI.md")

# NEVER_COPY (target layout in HACF's design notes): never copied into a project, even
# when listed above. A trailing / means the directory and everything under it. Any other root
# file not in ROOT_COPY_FILES is refused as well.
NEVER_COPY=(
  "SPEC.md" "project.yaml" "workspace.yaml" "lore_hypergraph.yml" ".gitattributes"
  "lore/" "missions/" "exhaust/" ".openrig/" "openrig/"
  "spec/" "tests/"
  "dev/" "PRDs/" "docs/"
  ".agents/local/" ".agents/state/" ".agents/.backup/" ".agents/logs/"
  ".agents/memory/"
  ".claude/settings*.json" ".mcp.json" ".cursor/mcp.json" ".roo/mcp.json" ".gitignore"
  "CHANGELOG.md" "README.md" "CITATION.cff" "pyproject.toml" "uv.lock" "requirements.txt"
  ".agent"
)

# sha256 of every distinct HACF-uninstall.sh blob in `git log 984ca46 -- HACF-uninstall.sh`:
# one blob, introduced by efb81aa (v0.5.3, "Rename installers to HACF-install/uninstall") and
# unchanged through 984ca46 (v0.5.9). A saved copy deletes every project path that exists in
# HACF main, so the installer offers to replace it (FLOW 15, R10).
V05X_UNINSTALLER_SHAS=(
  "d200470dbcb32844a538aa003060c3c1a995ddfef1660c245d061c1ab95107f4"  # efb81aa..984ca46
)

# Named targets for --files= (repair semantics: an existing file is never overwritten).
# Format: "src_in_clone|dst_in_project|chmod_x". A dst starting with "hooks/" goes to the git
# common dir's hooks directory.
declare -A FILE_TARGETS=(
  ["pre-commit"]=".agents/scripts/pre-commit|hooks/pre-commit|yes"
  ["commit-msg"]=".agents/scripts/commit-msg|hooks/commit-msg|yes"
  ["pre-push"]=".agents/scripts/pre-push|hooks/pre-push|yes"
  ["pyproject-template"]=".agents/schemas/project-templates/pyproject.toml|.agents/schemas/project-templates/pyproject.toml|no"
  ["python-rules"]=".agents/rules/python.md|.agents/rules/python.md|no"
  ["testing-rules"]=".agents/rules/testing.md|.agents/rules/testing.md|no"
  ["security-rules"]=".agents/rules/security.md|.agents/rules/security.md|no"
  ["package-rules"]=".agents/rules/package-management.md|.agents/rules/package-management.md|no"
  ["git-workflow-rules"]=".agents/rules/git-workflow.md|.agents/rules/git-workflow.md|no"
)
GIT_HOOKS=("pre-commit" "commit-msg" "pre-push")

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------
AUTO_YES=false
PRESELECTED_IDES=""
PRESERVE_CUSTOM=false
DRY_RUN=false
MODE=""
FILES_TARGET=""
INSTALL_DEPS=false
CODE_GRAPH=""
INSTALL_CODE_GRAPH=false
FIREWALL="auto"
MCP_ENTRY_FLAG=""      # --no-mcp-entry (skip) | --mcp-entry (write) | "" (the manifest's record)
MIGRATE=false
STATUSLINE=false
FLAG_REPO_URL=""
FLAG_BRANCH=""
FLAG_SOURCE=""
FLAG_REF=""
SOURCE_GIVEN=false
REF_GIVEN=false
RIG_REQUESTED=false    # --rig or --rig=DEST was given
RIG_DEST=""            # the destination as given (default openrig/hacf)

SOURCE_KIND="remote"   # remote | local (--source)
SOURCE_REAL=""         # the --source checkout, realpath'd; the only form passed to git
SOURCE_REF=""          # the --ref as given (default HEAD)
SRC_COMMIT=""          # the full sha SOURCE_REF names in SOURCE_REAL
PIN_COMMIT=""          # D1: the installed_commit a re-run without --ref must match ("" = none)
PIN_PATH=""            # D1: the recorded checkout path shown in the refusal
PIN_SOURCE_ARG=""      # D1: " --source=<p>" when the re-run named the recorded checkout
REPO_URL=""
BRANCH=""
UPSTREAM_SHA=""
INSTALL_VERSION="$HACF_VERSION"   # the manifest's version: the fetched installer's (C4)
ANNOUNCED_SHA=""
INTERACTIVE=false
UPGRADE_MODE=false
SELECTED_IDE_IDS=""
RECORDED_IDE_IDS=""    # the previous install's manifest `ides` (upgrade only; known ids)
IDES_FROM_RECORD=false
LS_REASON=""           # why check_local_path refused a path
LS_BARE=""
LS_COMMON=""
LS_TOP=""
TMP_DIR=""
SRC=""
BACKUP_TS=""
CODE_GRAPH_BACKEND="none"
CODE_GRAPH_METHOD=""
REPORT=()
DECLINED=()
RIG_COPY=false         # copy the rig spec this run (--rig, or yes to the interactive question)
RIG_ABS=""             # the resolved destination (check_rig_dest)
RIG_REL=""             # its project-relative POSIX path; empty when outside the project
RIG_SLUG=""            # the project slug; the copy's rig name is <slug>-hacf
RIG_JSON="[]"          # this run's manifest rig_spec entry, as a JSON list

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
die() { echo "❌  $*" >&2; exit 1; }
usage_error() { echo "❌  $*" >&2; echo "" >&2; usage >&2; exit 2; }
report() { REPORT+=("$1"); }
upstream_label() {
  if [ "$SOURCE_KIND" = "local" ]; then
    echo "local $(printf '%q' "$SOURCE_REAL") @ ${UPSTREAM_SHA:-$SRC_COMMIT}"
  else
    echo "$REPO_URL @ ${UPSTREAM_SHA:-${ANNOUNCED_SHA:-(commit not fetched yet)}}"
  fi
}

cleanup() { if [ -n "$TMP_DIR" ]; then rm -rf "$TMP_DIR"; fi; }

# True when /dev/tty can be opened. `[ -r /dev/tty ]` is not enough: it is true without a
# controlling terminal, while opening fails.
tty_available() { ( exec 3</dev/tty ) 2>/dev/null; }

# tty_ask <prompt>: print the prompt on /dev/tty and echo the answer read from /dev/tty
# (empty when the terminal cannot be opened or read).
tty_ask() {
  local prompt="$1" answer=""
  answer=$(
    { exec 3</dev/tty; } 2>/dev/null || exit 0
    printf '%s' "$prompt" >/dev/tty 2>/dev/null || exit 0
    IFS= read -r line <&3 || line=""
    printf '%s' "$line"
  ) || answer=""
  printf '%s' "$answer"
}

# prompt_consent <flag-given> <message>: yes when the action's own flag was given; otherwise
# no under -y or without a terminal; otherwise ask on /dev/tty with default No (R24.2). The
# prompt shows the effective upstream URL and commit (R12). Never used for anything else.
prompt_consent() {
  local given="$1" msg="$2" answer
  if [ "$given" = true ]; then
    echo "    ✅  $msg — consent given by flag (upstream $(upstream_label))"
    return 0
  fi
  if $AUTO_YES; then
    echo "    ⏭️   $msg — declined (-y never consents; pass the action's own flag)"
    return 1
  fi
  if ! tty_available; then
    echo "    ⏭️   $msg — declined (no terminal to ask on)"
    return 1
  fi
  answer=$(tty_ask "    $msg
    upstream: $(upstream_label)
    Proceed? [y/N] ")
  if [[ "$answer" == "y" || "$answer" == "Y" ]]; then
    echo "    ✅  $msg — consent given on the terminal"
    return 0
  fi
  echo "    ⏭️   $msg — declined"
  return 1
}

# ask_yn <message> <default y|n>: a local, non-consent question (never a download or a
# user-level change). Interactive runs ask on /dev/tty; otherwise the default is taken.
ask_yn() {
  local msg="$1" default="${2:-n}" answer hint="[y/N]"
  [ "$default" = "y" ] && hint="[Y/n]"
  if ! $INTERACTIVE; then
    echo "    $msg $hint $default (non-interactive)"
    [ "$default" = "y" ]
    return
  fi
  answer=$(tty_ask "    $msg $hint ")
  answer="${answer:-$default}"
  [[ "$answer" == "y" || "$answer" == "Y" ]]
}

ide_id()      { echo "${1%%|*}"; }
ide_display() { echo "${1}" | cut -d'|' -f2; }
ide_dirs()    { echo "${1}" | cut -d'|' -f3; }
ide_files()   { echo "${1}" | cut -d'|' -f4; }
all_ide_ids() { local def out=""; for def in "${IDE_DEFS[@]}"; do out="$out $(ide_id "$def")"; done; echo "$out"; }
contains_id() { local id="$1"; shift; [[ " $* " == *" $id "* ]]; }

# read_hacf_version <text>: the HACF_VERSION="..." an installer's text assigns, or "".
read_hacf_version() {
  if [[ "$1" =~ (^|$'\n')HACF_VERSION=\"([^\"]*)\" ]]; then printf '%s' "${BASH_REMATCH[2]}"; fi
}

py_sha() { python3 -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$1" </dev/null; }

in_list() { local needle="$1"; shift; local item; for item in "$@"; do [ "$item" = "$needle" ] && return 0; done; return 1; }

# hx <command> [args]: the installer's Python helper (hashlib, json, temp file + os.replace).
# It never starts a shell or evaluates code; paths come in as argv.
hx() {
  HACF_NEVER_COPY="$(printf '%s\n' "${NEVER_COPY[@]}")" \
  HACF_ROOT_COPY="$(printf '%s\n' "${ROOT_COPY_FILES[@]}")" \
  HACF_IDE_IDS="$(all_ide_ids)" \
  python3 - "$@" <<'PY'
import fnmatch
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile

NEVER = [p for p in os.environ.get("HACF_NEVER_COPY", "").split("\n") if p]
ROOT_COPY = [p for p in os.environ.get("HACF_ROOT_COPY", "").split("\n") if p]
MANIFEST = ".agents/hacf-manifest.json"


def sha_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write(path, data, mode=None):
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".hacf-", dir=directory)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if mode is None:
            mode = os.stat(path).st_mode & 0o777 if os.path.exists(path) else 0o644
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def never_copy(rel):
    for pattern in NEVER:
        if pattern.endswith("/"):
            if rel == pattern[:-1] or rel.startswith(pattern):
                return True
        elif any(c in pattern for c in "*?["):
            if fnmatch.fnmatchcase(rel, pattern):
                return True
        elif rel == pattern:
            return True
    return "/" not in rel and rel not in ROOT_COPY


def load_json(path):
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def old_manifest():
    data = load_json(MANIFEST)
    return data if isinstance(data, dict) else {}


def append_record(records, record):
    with open(records, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


def walk(src, rel):
    top = os.path.join(src, rel)
    if os.path.isfile(top) and not os.path.islink(top):
        return [rel]
    found = []
    for base, dirs, files in os.walk(top):
        dirs[:] = sorted(d for d in dirs if d != "__pycache__")
        for name in sorted(files):
            if name.endswith((".pyc", ".pyo")):
                continue
            path = os.path.join(base, name)
            if os.path.islink(path) or not os.path.isfile(path):
                continue
            found.append(os.path.relpath(path, src).replace(os.sep, "/"))
    return found


def cmd_sync(args):
    """sync SRC MODE DRY BACKUP RECORDS OWNER REL...: per-file, manifest-aware copy."""
    src, mode, dry, backup, records, owner = args[:6]
    rels = args[6:]
    dry = dry == "1"
    owned = old_manifest().get("files") or {}
    counts = {"installed": 0, "updated": 0, "unchanged": 0, "kept": 0}
    for top in rels:
        if not os.path.exists(os.path.join(src, top)):
            continue
        for rel in walk(src, top):
            if never_copy(rel):
                print(f"    🚫  refused (NEVER_COPY): {rel}")
                continue
            source = os.path.join(src, rel)
            new_sha = sha_file(source)
            new_mode = os.stat(source).st_mode & 0o777
            if os.path.islink(rel) or (os.path.lexists(rel) and not os.path.isfile(rel)):
                print(f"    ⚠️   left (not a regular file): {rel}")
                continue
            if os.path.exists(rel):
                current = sha_file(rel)
                if current == new_sha:
                    counts["unchanged"] += 1
                    if not dry:
                        append_record(records, {"path": rel, "sha256": new_sha, "owner": owner})
                    continue
                if mode == "repair":
                    counts["kept"] += 1
                    continue
                entry = owned.get(rel) if isinstance(owned, dict) else None
                reference = entry.get("sha256") if isinstance(entry, dict) else new_sha
                basis = "manifest" if isinstance(entry, dict) else "upstream"
                if current != reference:
                    target = os.path.join(backup, rel)
                    if dry:
                        print(f"    [DRY-RUN] would back up {rel} -> {target}")
                    else:
                        os.makedirs(os.path.dirname(target), exist_ok=True)
                        shutil.copy2(rel, target)
                        print(f"    💾  backed up before replacing (differs from the {basis} copy): {rel} -> {target}")
                if dry:
                    print(f"    [DRY-RUN] would update {rel}")
                else:
                    with open(source, "rb") as handle:
                        atomic_write(rel, handle.read(), new_mode)
                counts["updated"] += 1
            else:
                if dry:
                    print(f"    [DRY-RUN] would install {rel}")
                else:
                    with open(source, "rb") as handle:
                        atomic_write(rel, handle.read(), new_mode)
                counts["installed"] += 1
            if not dry:
                append_record(records, {"path": rel, "sha256": new_sha, "owner": owner})
    summary = ", ".join(f"{n} {k}" for k, n in counts.items() if n)
    print(f"    {'[DRY-RUN] ' if dry else ''}{', '.join(rels)}: {summary or 'nothing to copy'}")


BARE_CBM_ENTRY = {"type": "stdio", "command": "codebase-memory-mcp", "args": []}
UVX_CBM_ARG = re.compile(r"codebase-memory-mcp@0\.[0-9]+\.[0-9]+")


def hacf_mcp_entry(current, previous):
    """Whether an existing MCP entry is HACF's (F21, OPR.99.0.10.4); the one ownership rule.

    HACF's: the manifest-recorded entry, the bare uv-tool form, or a `uvx
    codebase-memory-mcp@<0.x.y>` form. Anything else is the user's and is never rewritten.
    """
    if previous is not None and current == previous:
        return True
    if current == BARE_CBM_ENTRY:
        return True
    return (
        isinstance(current, dict)
        and set(current) == {"type", "command", "args"}
        and current["type"] == "stdio"
        and current["command"] == "uvx"
        and isinstance(current["args"], list)
        and len(current["args"]) == 1
        and isinstance(current["args"][0], str)
        and UVX_CBM_ARG.fullmatch(current["args"][0]) is not None
    )


def _argv(entry):
    if isinstance(entry, dict) and isinstance(entry.get("command"), str):
        args = entry.get("args") if isinstance(entry.get("args"), list) else []
        return " ".join([entry["command"], *map(str, args)])
    return json.dumps(entry, sort_keys=True)


def cmd_mcp(args):
    """mcp FILE SERVER ENTRY_JSON RECORDS DRY [STATUS]: merge one MCP server entry; keep others.

    STATUS, when given, gets one `FILE<TAB>written|kept (user's)|unchanged<TAB>argv` line (MR 3).
    """
    path, server, entry_json, records, dry = args[:5]
    status_path = args[5] if len(args) > 5 else None

    def status(word, which):
        if status_path and dry != "1":
            with open(status_path, "a", encoding="utf-8") as handle:
                handle.write(f"{path}\t{word}\t{_argv(which)}\n")

    entry = json.loads(entry_json)
    previous = ((old_manifest().get("mcp_entries") or {}).get(path) or {}).get(server)
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
    except FileNotFoundError:
        text = None
    if text is None:
        data = {}
    else:
        try:
            data = json.loads(text, object_pairs_hook=_unique)
        except ValueError as exc:
            print(f"    ❌  {path}: invalid JSON ({exc}); left unchanged")
            return 3
        if not isinstance(data, dict):
            print(f"    ❌  {path}: not a JSON object; left unchanged")
            return 3
    servers = data.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        print(f"    ❌  {path}: mcpServers is not an object; left unchanged")
        return 3
    current = servers.get(server)
    if current == entry:
        print(f"    ✓  {path}: {server} already registered")
        status("unchanged", entry)
    elif current is not None and not hacf_mcp_entry(current, previous):
        print(f"    ⚠️   {path}: {server} exists with your own settings; left unchanged")
        status("kept (user's)", current)
        return 0
    elif dry == "1":
        print(f"    [DRY-RUN] would merge {server} into {path}")
        return 0
    else:
        servers[server] = entry
        atomic_write(path, (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode())
        print(f"    ✅  {path}: {server} merged ({len(servers) - 1} other server(s) kept)")
        status("written", entry)
    if dry != "1":
        append_record(records, {"path": path, "server": server, "entry": entry})
    return 0


def _unique(pairs):
    seen = {}
    for key, value in pairs:
        if key in seen:
            raise ValueError(f"duplicate key {key!r}")
        seen[key] = value
    return seen


def cmd_localcfg(args):
    """localcfg FILE DRY KEY=VALUE[!] ...: set code_graph keys; `!` = given by flag (overwrite)."""
    import yaml

    path, dry = args[0], args[1] == "1"
    wanted = []
    for item in args[2:]:
        force = item.endswith("!")
        key, _, value = item.rstrip("!").partition("=")
        wanted.append((key, value, force))
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
    except FileNotFoundError:
        text = ""
    data = yaml.safe_load(text) if text.strip() else {}
    if not isinstance(data, dict):
        print(f"    ❌  {path}: not a YAML mapping; left unchanged")
        return 3
    section = data.get("code_graph")
    if section is not None and not isinstance(section, dict):
        print(f"    ❌  {path}: code_graph is not a mapping; left unchanged")
        return 3
    section = dict(section or {})
    changes = {k: v for k, v, force in wanted if (force or k not in section) and section.get(k) != v}
    if not changes:
        print(f"    ✓  {path}: code_graph already set")
        return 0
    lines = text.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    start = next((i for i, line in enumerate(lines) if re.match(r"code_graph:\s*(#.*)?$", line)), None)
    if start is None:
        lines += ["code_graph:\n"] + [f"  {k}: {v}\n" for k, v in changes.items()]
    else:
        end = start + 1
        while end < len(lines) and (lines[end].startswith((" ", "\t")) or not lines[end].strip()):
            end += 1
        for key, value in changes.items():
            pattern = re.compile(r"(\s+)" + re.escape(key) + r":")
            hit = next((i for i in range(start + 1, end) if pattern.match(lines[i])), None)
            if hit is None:
                lines.insert(start + 1, f"  {key}: {value}\n")
                end += 1
            else:
                indent = pattern.match(lines[hit]).group(1)
                lines[hit] = f"{indent}{key}: {value}\n"
    new = "".join(lines)
    check = yaml.safe_load(new)
    others_same = {k: v for k, v in check.items() if k != "code_graph"} == {
        k: v for k, v in data.items() if k != "code_graph"
    }
    expected = {**section, **changes}
    if not others_same or check.get("code_graph") != expected:
        print(f"    ❌  {path}: could not set code_graph without touching other keys; left unchanged")
        return 3
    if dry:
        print(f"    [DRY-RUN] would set code_graph {changes} in {path}")
        return 0
    atomic_write(path, new.encode())
    print(f"    ✅  {path}: code_graph {', '.join(f'{k}={v}' for k, v in changes.items())}")
    return 0


def cmd_yamlget(args):
    """yamlget FILE SECTION KEY: print a value, or nothing."""
    import yaml

    try:
        with open(args[0], encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
    except (OSError, yaml.YAMLError):
        return 0
    section = data.get(args[1]) if isinstance(data, dict) else None
    value = section.get(args[2]) if isinstance(section, dict) else None
    if value is not None:
        print(value)
    return 0


def cmd_jsonget(args):
    """jsonget FILE KEY...: print a nested value, or nothing."""
    value = load_json(args[0])
    for key in args[1:]:
        value = value.get(key) if isinstance(value, dict) else None
    if value is not None:
        print(value if isinstance(value, str) else json.dumps(value))
    return 0


def recorded_rig_specs(old):
    """Return the old manifest's well-formed rig_spec entries (R4), in order."""
    entries = []
    listed = old.get("rig_spec")
    for entry in listed if isinstance(listed, list) else []:
        if not isinstance(entry, dict) or not isinstance(entry.get("commit"), str):
            continue
        if entry.get("outside_project") is True and "path" not in entry:
            entries.append({"outside_project": True, "commit": entry["commit"]})
        elif isinstance(entry.get("path"), str) and relative_inside(entry["path"]):
            entries.append({"path": entry["path"], "commit": entry["commit"]})
    return entries


def relative_inside(rel):
    """True for a plain relative POSIX path that stays inside the project."""
    parts = rel.split("/")
    return bool(rel) and not rel.startswith("/") and ".." not in parts and "" not in parts


def cmd_manifest(args):
    """manifest VERSION COMMIT RECORDS OWNED MCP HOOKS TEMPLATES_DIR SOURCE_JSON IDES RIG_JSON
    INSTALLER_SHA.

    INSTALLER_SHA is the sha256 of the saved installer copy (C2); `installer_sha256` when set.
    MCP_ENTRY is "skipped" under --no-mcp-entry or its record (`mcp_entry: skipped`), else "".

    SOURCE_JSON is the provenance recorded as the manifest's `source` key: {"kind": "local",
    "commit"} or {"kind": "remote", "url", "branch", "commit"}; never a local path. IDES is the
    space-separated IDE selection: recorded sorted, de-duplicated and known ids only; when it is
    blank (skills and system modes select no IDEs) the old manifest's `ides` is carried forward,
    and no `ides` key is written when there is none. Never `ides: []`. RIG_JSON is this run's
    `--rig` entry as a JSON list (empty when nothing was copied): `rig_spec` is the old list
    carried forward, minus inside entries whose directory is gone, plus this run's entry. An
    inside entry is {"path": <project-relative POSIX path>, "commit"}, an outside one
    {"outside_project": true, "commit"}; never an absolute path, and no `rig_spec` key when the
    list is empty. The copies are the user's: nothing here refreshes or checks their content.
    """
    (version, commit, records, owned_path, mcp_path, hooks_path, templates, source_json, ides,
     rig_json, installer_sha, mcp_entry) = args
    source = json.loads(source_json)
    sys.path.insert(0, os.path.abspath(".agents/scripts"))
    import _runtime
    import hyper_blocks

    old = old_manifest()
    files = {}
    for rel, entry in (old.get("files") or {}).items():
        if isinstance(entry, dict) and os.path.isfile(rel) and sha_file(rel) == entry.get("sha256"):
            files[rel] = {"sha256": entry["sha256"], "owner": entry.get("owner", "hacf")}

    def lines(path):
        try:
            with open(path, encoding="utf-8") as handle:
                return [json.loads(line) for line in handle if line.strip()]
        except FileNotFoundError:
            return []

    for record in lines(records):
        rel = record["path"]
        if os.path.isfile(rel) and sha_file(rel) == record["sha256"]:
            files[rel] = {"sha256": record["sha256"], "owner": record["owner"]}

    owned = lines(owned_path)
    rules = {} if owned else dict(old.get("settings_rules") or {})
    hooks = {} if owned else {k: v for k, v in (old.get("hooks") or {}).items() if k != "git"}
    for item in owned:
        for bucket, values in (item.get("settings_rules") or {}).items():
            if isinstance(values, list):
                rules.setdefault(bucket, [])
                rules[bucket] += [v for v in values if v not in rules[bucket]]
        for event, values in (item.get("hooks") or {}).items():
            if isinstance(values, list):
                hooks.setdefault(event, [])
                hooks[event] += [v for v in values if v not in hooks[event]]

    git_hooks = dict((old.get("hooks") or {}).get("git") or {})
    for record in lines(hooks_path):
        git_hooks[record["name"]] = {"path": record["path"], "sha256": record["sha256"]}
    git_hooks = {
        name: entry
        for name, entry in git_hooks.items()
        if isinstance(entry, dict)
        and os.path.isfile(entry.get("path", ""))
        and sha_file(entry["path"]) == entry.get("sha256")
    }
    if git_hooks:
        hooks["git"] = git_hooks

    mcp = {}
    for path, servers in (old.get("mcp_entries") or {}).items():
        current = (load_json(path) or {}).get("mcpServers") or {}
        for server, entry in (servers or {}).items():
            if current.get(server) == entry:
                mcp.setdefault(path, {})[server] = entry
    for record in lines(mcp_path):
        mcp.setdefault(record["path"], {})[record["server"]] = record["entry"]

    blocks = {}
    old_blocks = old.get("blocks") or {}
    for rel in (
        "AGENTS.md", "CLAUDE.md", "GEMINI.md", ".gitignore",
        ".cursorignore", ".rooignore", ".codeiumignore", ".geminiignore",
    ):
        if not os.path.isfile(rel) or os.path.islink(rel):
            continue
        with open(rel, "rb") as handle:
            doc = hyper_blocks.parse_doc(handle.read())
        try:
            found = hyper_blocks.scan(doc, hyper_blocks.style_of(hyper_blocks.Path(rel)))
        except hyper_blocks.BlocksError:
            continue
        for block in found:
            if block.kind != hyper_blocks.MANAGED:
                continue
            sha = hyper_blocks.body_sha256(hyper_blocks.body_of(doc, block))
            template = os.path.join(templates, rel)
            previous = (old_blocks.get(rel) or {}).get(block.block_id)
            if rel in ("AGENTS.md", "CLAUDE.md", "GEMINI.md") and os.path.isfile(template):
                with open(template, "rb") as handle:
                    expected = hyper_blocks.body_sha256(hyper_blocks.body_lines(handle.read()))
                if sha != expected and previous:
                    sha = previous  # a user-edited block stays recorded as HACF last wrote it
            blocks.setdefault(rel, {})[block.block_id] = sha

    manifest = {
        "version": version,
        "installed_commit": commit,
        **({"installer_sha256": installer_sha} if installer_sha else {}),
        **({"mcp_entry": "skipped"} if mcp_entry == "skipped" else {}),
        "installed_at": _runtime.utc_now_iso(),
        "files": dict(sorted(files.items())),
        "settings_rules": rules,
        "hooks": hooks,
        "blocks": blocks,
        "mcp_entries": mcp,
        "source": source,
    }
    known = set(os.environ.get("HACF_IDE_IDS", "").split())
    selected = sorted({ide for ide in ides.split() if ide in known})
    if ides.strip():
        if selected:
            manifest["ides"] = selected
    elif isinstance(old.get("ides"), list) and old["ides"]:
        manifest["ides"] = old["ides"]
    rig_spec = [
        entry
        for entry in recorded_rig_specs(old)
        if entry.get("outside_project") or os.path.isdir(entry["path"])
    ]
    rig_spec += json.loads(rig_json)
    if rig_spec:
        manifest["rig_spec"] = rig_spec
    data = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    atomic_write(MANIFEST, data)
    print(f"    ✅  {MANIFEST}: {len(files)} file(s), installed_commit {commit}")
    return 0


def cmd_sourcefile(args):
    """sourcefile PATH SOURCE REF COMMIT: write the per-machine local-source record atomically."""
    import datetime

    path, source, ref, commit = args
    record = {
        "schema": "hacf.install-source/v1",
        "kind": "local",
        "path": source,
        "ref": ref,
        "commit": commit,
        "written_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
    }
    atomic_write(path, (json.dumps(record, indent=2) + "\n").encode(), mode=0o644)
    print(f"    ✅  {path}: local source recorded (per-machine, gitignored)")
    return 0


def cmd_rigdest(args):
    """rigdest DEST HOME PROJECT FORBIDDEN...: resolve and check a --rig destination (R3).

    Prints `ok\0<resolved>\0<project-relative path or empty>\0` or `refuse\0<reason>\0`.
    FORBIDDEN are directories DEST may not lie in (git dir, common dir, source, temp dir).
    """
    dest, home, project = args[:3]
    forbidden = [os.path.realpath(path) for path in args[3:] if path]
    shown = json.dumps(dest, ensure_ascii=True)

    def refuse(reason):
        sys.stdout.write(f"refuse\0{reason}\0")
        return 0

    if any(ord(c) < 32 or ord(c) == 127 for c in dest):
        return refuse("the destination holds a control character")
    if dest == "~" or dest.startswith("~/"):
        absolute = os.path.join(home, dest[2:]) if dest != "~" else home
    elif dest.startswith("~"):
        return refuse(f"{shown}: only ~ and ~/ are expanded (no ~user)")
    else:
        absolute = os.path.join(project, dest)
    # The nearest existing ancestor, realpath'd, plus the rest; nothing in the rest may exist
    # in any form (a dangling symlink included).
    parts = [part for part in absolute.split("/") if part and part != "."]
    if os.path.lexists(absolute):
        recorded = {
            entry["path"]: entry["commit"]
            for entry in recorded_rig_specs(old_manifest())
            if "path" in entry
        }
        project_real = os.path.realpath(project)
        resolved = os.path.realpath(absolute)
        rel = os.path.relpath(resolved, project_real).replace(os.sep, "/")
        if rel in recorded:
            return refuse(f"{rel} already copied at {recorded[rel]}; re-run without --rig")
        return refuse(f"{shown} already exists; --rig never writes over an existing path")
    base = "/"
    index = 0
    while index < len(parts) and os.path.isdir(os.path.join(base, parts[index])):
        base = os.path.join(base, parts[index])
        index += 1
    rest = parts[index:]
    if ".." in rest:
        return refuse(f"{shown}: '..' after a directory that does not exist")
    probe = base
    for part in rest:
        probe = os.path.join(probe, part)
        if os.path.lexists(probe):
            return refuse(f"{shown}: {json.dumps(probe)} exists and is not a directory")
    anchor = os.path.realpath(base)
    resolved = os.path.join(anchor, *rest) if rest else anchor
    for directory in forbidden:
        if resolved == directory or resolved.startswith(directory.rstrip("/") + "/"):
            return refuse(f"{shown} lies inside {json.dumps(directory)}")
    ancestor = anchor
    while True:
        if os.path.lexists(os.path.join(ancestor, "rig.yaml")):
            return refuse(f"{shown} lies under {json.dumps(ancestor)}, which holds a rig.yaml")
        if ancestor == "/":
            break
        ancestor = os.path.dirname(ancestor)
    project_real = os.path.realpath(project)
    rel = ""
    if resolved.startswith(project_real.rstrip("/") + "/"):
        rel = os.path.relpath(resolved, project_real).replace(os.sep, "/")
    sys.stdout.write(f"ok\0{resolved}\0{rel}\0")
    return 0


COMMANDS = {
    "sync": cmd_sync,
    "mcp": cmd_mcp,
    "localcfg": cmd_localcfg,
    "yamlget": cmd_yamlget,
    "jsonget": cmd_jsonget,
    "manifest": cmd_manifest,
    "sourcefile": cmd_sourcefile,
    "rigdest": cmd_rigdest,
}
sys.exit(COMMANDS[sys.argv[1]](sys.argv[2:]) or 0)
PY
}

# ---------------------------------------------------------------------------
# Arguments and upstream (R12)
# ---------------------------------------------------------------------------
parse_args() {
  while [ $# -gt 0 ]; do
    case "$1" in
      -y|--yes)                AUTO_YES=true ;;
      --ides=*)                PRESELECTED_IDES="${1#--ides=}" ;;
      --preserve-custom)       PRESERVE_CUSTOM=true ;;
      --mode=*)                MODE="${1#--mode=}" ;;
      --files=*)               FILES_TARGET="${1#--files=}" ;;
      --install-deps)          INSTALL_DEPS=true ;;
      --code-graph=*)          CODE_GRAPH="${1#--code-graph=}" ;;
      --install-code-graph)    INSTALL_CODE_GRAPH=true ;;
      --firewall=*)            FIREWALL="${1#--firewall=}" ;;
      --no-mcp-entry)          MCP_ENTRY_FLAG="skip" ;;
      --mcp-entry)             MCP_ENTRY_FLAG="write" ;;
      --migrate)               MIGRATE=true ;;
      --statusline)            STATUSLINE=true ;;
      --dry-run)               DRY_RUN=true ;;
      --rig)                   RIG_REQUESTED=true; RIG_DEST="openrig/hacf" ;;
      --rig=*)                 RIG_REQUESTED=true; RIG_DEST="${1#--rig=}"
                               [ -n "$RIG_DEST" ] || usage_error "--rig= needs a directory (or use --rig). Nothing was written." ;;
      --repo-url=*)            FLAG_REPO_URL="${1#--repo-url=}" ;;
      --repo-url)              [ $# -ge 2 ] || usage_error "--repo-url needs a value"; FLAG_REPO_URL="$2"; shift ;;
      --branch=*)              FLAG_BRANCH="${1#--branch=}" ;;
      --branch)                [ $# -ge 2 ] || usage_error "--branch needs a value"; FLAG_BRANCH="$2"; shift ;;
      --source=*)              SOURCE_GIVEN=true; FLAG_SOURCE="${1#--source=}" ;;
      --source)                [ $# -ge 2 ] || usage_error "--source needs a value"; SOURCE_GIVEN=true; FLAG_SOURCE="$2"; shift ;;
      --ref=*)                 REF_GIVEN=true; FLAG_REF="${1#--ref=}" ;;
      --ref)                   [ $# -ge 2 ] || usage_error "--ref needs a value"; REF_GIVEN=true; FLAG_REF="$2"; shift ;;
      -h|--help)              usage; exit 0 ;;
      *)                       usage_error "unknown option: $1" ;;
    esac
    shift
  done
  case "$CODE_GRAPH" in ""|none|codebase-memory-mcp) ;; *) usage_error "--code-graph must be none or codebase-memory-mcp, not '$CODE_GRAPH'" ;; esac
  case "$FIREWALL" in auto|off) ;; *) usage_error "--firewall must be auto or off, not '$FIREWALL'" ;; esac
  case "$MODE" in ""|full|skills|system|ide|repair|dry-run) ;; *) usage_error "--mode must be full, skills, system, ide, repair or dry-run, not '$MODE'" ;; esac
  if [ "$MODE" = "dry-run" ]; then DRY_RUN=true; fi
  if $RIG_REQUESTED && [ -n "$FILES_TARGET" ]; then
    usage_error "--rig cannot be combined with --files=. Nothing was written."
  fi
  if $RIG_REQUESTED && [[ " skills system ide " == *" $MODE "* ]]; then
    usage_error "--rig cannot be combined with --mode=$MODE. Nothing was written."
  fi
  if $SOURCE_GIVEN && [ -z "$FLAG_SOURCE" ]; then usage_error "--source needs a non-empty path. Nothing was written."; fi
  # --ref without --source needs a local-source record; resolve_upstream checks that (D1).
  if $REF_GIVEN && ! $SOURCE_GIVEN && { [ -n "$FLAG_REPO_URL" ] || [ -n "$FLAG_BRANCH" ]; }; then
    usage_error "--ref needs --source. Nothing was written."
  fi
  if $SOURCE_GIVEN && { [ -n "$FLAG_REPO_URL" ] || [ -n "$FLAG_BRANCH" ]; }; then
    usage_error "--source cannot be combined with --repo-url or --branch. Nothing was written."
  fi
  if [ -n "$PRESELECTED_IDES" ] && [ "$PRESELECTED_IDES" != "all" ]; then
    local id known
    known="$(all_ide_ids)"
    for id in ${PRESELECTED_IDES//,/ }; do
      contains_id "$id" $known || usage_error "unknown IDE id '$id' (known:$known)"
    done
  fi
}

resolve_upstream() {
  PIN_COMMIT=""; PIN_PATH=""; PIN_SOURCE_ARG=""
  if $SOURCE_GIVEN; then
    # --source: one commit of a local checkout. The HACF_TEST env overrides are not read here.
    $REF_GIVEN || same_recorded_source
    resolve_local_source
    return 0
  fi
  if [ -z "$FLAG_REPO_URL" ] && [ -z "$FLAG_BRANCH" ] \
      && ! { [ "${HACF_TEST:-}" = "1" ] && [ -n "${HACF_REPO_URL:-}" ]; } \
      && recorded_source; then
    # D1: a plain re-run reinstalls installed_commit; --ref moves within the recorded checkout.
    if ! $REF_GIVEN; then PIN_COMMIT="$REC_INSTALLED"; PIN_PATH="$REC_PATH"; fi
    resolve_local_source
    return 0
  fi
  if $REF_GIVEN; then usage_error "--ref needs --source. Nothing was written."; fi
  REPO_URL="$DEFAULT_REPO_URL"
  BRANCH="$DEFAULT_BRANCH"
  if [ "${HACF_TEST:-}" = "1" ]; then
    # Test-only overrides for E2E runs against a local bare clone (R12).
    REPO_URL="${HACF_REPO_URL:-$REPO_URL}"
    BRANCH="${HACF_BRANCH:-$BRANCH}"
  fi
  [ -n "$FLAG_REPO_URL" ] && REPO_URL="$FLAG_REPO_URL"
  [ -n "$FLAG_BRANCH" ] && BRANCH="$FLAG_BRANCH"
  case "$REPO_URL" in
    https://*) ;;
    *)
      if [ "${HACF_TEST:-}" != "1" ]; then
        die "refusing upstream URL '$REPO_URL': only https:// URLs are accepted. Nothing was written."
      fi
      ;;
  esac
  case "$REPO_URL" in -*) die "refusing upstream URL '$REPO_URL'. Nothing was written." ;; esac
  if [[ "$BRANCH" == -* ]] || { command -v git >/dev/null 2>&1 && ! git check-ref-format --branch "$BRANCH" >/dev/null 2>&1; }; then
    die "refusing branch name '$BRANCH'. Nothing was written."
  fi
}

# q <value>: the value shell-quoted for display (printf %q), so a path never injects output.
q() { printf '%q' "$1"; }

# realdir <dir> [<relative dir>]: the physical absolute path of a directory (or of the second
# argument taken relative to the first). Fails when it does not exist.
realdir() {
  if [ $# -ge 2 ]; then
    (CDPATH='' cd -- "$1" && CDPATH='' cd -- "$2" && pwd -P)
  else
    (CDPATH='' cd -- "$1" && pwd -P)
  fi
}

# check_local_path <path>: steps 1-5 below. On success SOURCE_REAL is the checkout's top level
# (or the bare repo) and LS_BARE, LS_COMMON, LS_TOP describe it; otherwise LS_REASON says why.
check_local_path() {
  local path="$1" out rc=0
  LS_REASON=""; LS_BARE=""; LS_COMMON=""; LS_TOP=""
  command -v git >/dev/null 2>&1 || { LS_REASON="git is required"; return 1; }
  # 1-2. Control characters (0x01-0x1f, 0x7f) are refused before the value is used anywhere.
  if [ "$(printf '%s' "$path" | LC_ALL=C tr -d '\001-\037\177')" != "$path" ]; then
    LS_REASON="the path holds a control character"; return 1
  fi
  # 3. A directory, realpath'd: only an absolute path ever reaches git (never host:dir or a URL).
  [ -d "$path" ] || { LS_REASON="not a directory"; return 1; }
  SOURCE_REAL=$(realdir "$path") || { LS_REASON="not a directory"; return 1; }
  # 4. A git repository (bare accepted); dubious ownership gets its own refusal.
  out=$(git -C "$SOURCE_REAL" rev-parse --is-bare-repository --git-common-dir 2>&1 </dev/null) || rc=$?
  if [ "$rc" -ne 0 ]; then
    case "$out" in
      *"dubious ownership"*) LS_REASON="owned by another user (git safe.directory)" ;;
      *) LS_REASON="not a git repository" ;;
    esac
    return 1
  fi
  out=$(git -C "$SOURCE_REAL" rev-parse --is-bare-repository --git-common-dir 2>/dev/null </dev/null) \
    || { LS_REASON="not a git repository"; return 1; }
  LS_BARE=$(printf '%s\n' "$out" | sed -n 1p)
  LS_COMMON=$(printf '%s\n' "$out" | sed -n 2p)
  LS_COMMON=$(realdir "$SOURCE_REAL" "$LS_COMMON") || { LS_REASON="not a git repository"; return 1; }
  if [ "$LS_BARE" != "true" ]; then
    LS_TOP=$(git -C "$SOURCE_REAL" rev-parse --show-toplevel 2>/dev/null </dev/null) \
      || { LS_REASON="not a git repository"; return 1; }
    LS_TOP=$(realdir "$LS_TOP") || { LS_REASON="not a git repository"; return 1; }
    # git upload-pack opens the path it is given, not a parent: fetch from the top level.
    SOURCE_REAL="$LS_TOP"
  fi
  # 5. A partial clone would fetch missing objects from its own remote.
  if [ -n "$(git -C "$SOURCE_REAL" config --get extensions.partialClone 2>/dev/null </dev/null || true)" ]; then
    LS_REASON="partial clone (it would fetch from its remote)"; return 1
  fi
  return 0
}

# read_install_record: the manifest's source and installed_commit, and the per-machine file.
# Sets REC_KIND, REC_SRC_COMMIT, REC_INSTALLED, REC_FILE_ERR (missing | invalid | ""),
# REC_SCHEMA, REC_FILE_KIND, REC_FILE_COMMIT and REC_PATH. A value holding NUL reads as "".
read_install_record() {
  local fields=() field
  while IFS= read -r -d '' field; do fields+=("$field"); done < <(
    python3 -c '
import json, sys

def load(path):
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return {}, "missing"
    except (OSError, ValueError):
        return {}, "invalid"
    return (data, "") if isinstance(data, dict) else ({}, "invalid")

def text(value):
    return value if isinstance(value, str) and "\0" not in value else ""

manifest, _ = load(sys.argv[1])
source = manifest.get("source") if isinstance(manifest.get("source"), dict) else {}
record, error = load(sys.argv[2])
values = [source.get("kind"), source.get("commit"), manifest.get("installed_commit"), error,
          record.get("schema"), record.get("kind"), record.get("commit"), record.get("path")]
sys.stdout.write("".join(text(v) + "\0" for v in values))
' "$MANIFEST_REL" "$SOURCE_FILE_REL" </dev/null 2>/dev/null || true)
  REC_KIND="${fields[0]:-}"; REC_SRC_COMMIT="${fields[1]:-}"; REC_INSTALLED="${fields[2]:-}"
  REC_FILE_ERR="${fields[3]:-}"; REC_SCHEMA="${fields[4]:-}"; REC_FILE_KIND="${fields[5]:-}"
  REC_FILE_COMMIT="${fields[6]:-}"; REC_PATH="${fields[7]:-}"
}

# refuse_recorded <reason>: the one refusal for an unusable recorded source (never a fallback).
refuse_recorded() {
  local where="path not recorded"
  [ -n "$REC_PATH" ] && where="$(q "$REC_PATH")"
  die "this project was installed from a local HACF checkout ($where) at ${REC_INSTALLED:-(no installed_commit)}; $1. Pass --source=<checkout> or --repo-url=<https url>. Nothing was written."
}

# Slice 32 Task 5, D1 (OPR.99.0.6.1): an upgrade of a --source install reads the recorded checkout.
# Without --ref it is pinned to installed_commit (resolve_local_source refuses a moved HEAD); with
# --ref it resolves that ref in the checkout. Returns 1 (the remote path, unchanged) unless the manifest records source.kind == local; then
# the per-machine file must be valid and its path usable, or the run refuses.
recorded_source() {
  [ -f "$MANIFEST_REL" ] || return 1
  read_install_record
  [ "$REC_KIND" = "local" ] || return 1
  case "$REC_FILE_ERR" in
    missing) refuse_recorded "$SOURCE_FILE_REL is missing" ;;
    invalid) refuse_recorded "$SOURCE_FILE_REL is not a valid JSON object" ;;
  esac
  [ "$REC_SCHEMA" = "$SOURCE_SCHEMA" ] || refuse_recorded "$SOURCE_FILE_REL has schema $(q "$REC_SCHEMA"), not $SOURCE_SCHEMA"
  [ "$REC_FILE_KIND" = "local" ] || refuse_recorded "$SOURCE_FILE_REL has kind $(q "$REC_FILE_KIND"), not local"
  if [ -z "$REC_INSTALLED" ] || [ "$REC_FILE_COMMIT" != "$REC_INSTALLED" ]; then
    refuse_recorded "$SOURCE_FILE_REL records commit $(q "$REC_FILE_COMMIT"), not installed_commit ${REC_INSTALLED:-(none)}"
  fi
  [ -n "$REC_PATH" ] || refuse_recorded "$SOURCE_FILE_REL records no path"
  check_local_path "$REC_PATH" || refuse_recorded "the source $LS_REASON"
  FLAG_SOURCE="$REC_PATH"
  echo "    source: recorded by the previous install (pass --repo-url to switch to a remote)"
  return 0
}

# same_recorded_source (D1): --source without --ref naming the checkout this project records is
# pinned like a plain re-run. Any other --source is an explicit new source (HEAD, as before).
same_recorded_source() {
  [ -f "$MANIFEST_REL" ] || return 0
  read_install_record
  [ "$REC_KIND" = "local" ] && [ -z "$REC_FILE_ERR" ] && [ "$REC_SCHEMA" = "$SOURCE_SCHEMA" ] \
    && [ "$REC_FILE_KIND" = "local" ] && [ -n "$REC_PATH" ] && [ -n "$REC_INSTALLED" ] || return 0
  local given
  check_local_path "$FLAG_SOURCE" || return 0   # resolve_local_source refuses it with its reason
  given="$SOURCE_REAL"
  check_local_path "$REC_PATH" || return 0
  [ "$given" = "$SOURCE_REAL" ] || return 0
  PIN_COMMIT="$REC_INSTALLED"; PIN_PATH="$REC_PATH"; PIN_SOURCE_ARG=" --source=$(q "$FLAG_SOURCE")"
}

# R56 (slice 31): resolve --source/--ref to one commit of a local HACF checkout before any
# write. Only reads the source: nothing here writes to it or starts a network process.
resolve_local_source() {
  local refuse="refusing --source $(q "$FLAG_SOURCE"):"
  # 1-5 (check_local_path).
  if ! check_local_path "$FLAG_SOURCE"; then
    case "$LS_REASON" in
      "git is required") die "git is required for --source. Nothing was written." ;;
      "the path holds a control character") die "refusing --source: $LS_REASON. Nothing was written." ;;
      *) die "$refuse $LS_REASON. Nothing was written." ;;
    esac
  fi
  local out bare="$LS_BARE" common="$LS_COMMON" top="$LS_TOP"
  # 6. The target may not be the source's work tree, lie inside it or share its common dir.
  local target target_common=""
  target=$(pwd -P)
  if out=$(git rev-parse --git-common-dir 2>/dev/null </dev/null); then
    target_common=$(realdir "$target" "$out" 2>/dev/null) || target_common=""
  fi
  if [ -n "$top" ] && { [ "$target" = "$top" ] || [[ "$target" == "$top"/* ]]; }; then
    die "$refuse this project ($(q "$target")) is the source checkout or lies inside it. Nothing was written."
  fi
  if [ -n "$target_common" ] && [ "$target_common" = "$common" ]; then
    die "$refuse this project shares its git directory ($(q "$common")) with the source (a worktree). Nothing was written."
  fi
  # 7. The ref: a full sha of a commit. No check-ref-format, so a sha, HEAD~1 and v0.6.0^{} work.
  if $REF_GIVEN; then SOURCE_REF="$FLAG_REF"; else SOURCE_REF="HEAD"; fi
  case "$SOURCE_REF" in
    "") die "refusing an empty --ref. Nothing was written." ;;
    -*) die "refusing --ref $(q "$SOURCE_REF"): it starts with '-'. Nothing was written." ;;
  esac
  SRC_COMMIT=$(git -C "$SOURCE_REAL" rev-parse --verify --quiet "$SOURCE_REF^{commit}" 2>/dev/null </dev/null || true)
  [ -n "$SRC_COMMIT" ] || die "refusing --ref $(q "$SOURCE_REF"): unknown ref in $(q "$SOURCE_REAL"). Nothing was written."
  # 7b. D1: a re-run of the recorded checkout without --ref installs installed_commit or nothing.
  if [ -n "$PIN_COMMIT" ] && [ "$SRC_COMMIT" != "$PIN_COMMIT" ]; then
    if ! [[ "$PIN_COMMIT" =~ ^[0-9a-f]{40}([0-9a-f]{24})?$ ]] \
        || ! git -C "$SOURCE_REAL" cat-file -e "$PIN_COMMIT^{commit}" 2>/dev/null </dev/null; then
      refuse_recorded "its installed_commit is not a commit in that checkout; to install one of its commits, pass --ref=<commit>"
    fi
    local again="bash $(q "$PIN_PATH")/HACF-install.sh$PIN_SOURCE_ARG"
    die "refusing a plain re-run: the recorded HACF checkout $(q "$PIN_PATH") is at $SRC_COMMIT, but this project is installed at $PIN_COMMIT.
  to reinstall the installed commit:  $again --ref=$PIN_COMMIT
  to move to the checkout's HEAD:     $again --ref=$SRC_COMMIT
Nothing was written."
  fi
  # 8. Version guard: the commit's own installer must be this version.
  local blob version=""
  blob=$(git -C "$SOURCE_REAL" show "$SRC_COMMIT:HACF-install.sh" 2>/dev/null </dev/null || true)
  version=$(read_hacf_version "$blob")
  if [ "$version" != "$HACF_VERSION" ]; then
    local tmp_installer="${TMPDIR:-/tmp}/HACF-install-${SRC_COMMIT:0:12}.sh"
    die "commit $SRC_COMMIT carries HACF ${version:-(no HACF_VERSION)}; this installer is $HACF_VERSION; run it from that commit: git -C $(q "$SOURCE_REAL") show $SRC_COMMIT:HACF-install.sh > $(q "$tmp_installer"); bash $(q "$tmp_installer") --source=$(q "$SOURCE_REAL") --ref=$SRC_COMMIT
Nothing was written."
  fi
  # 9. Dirty warning (non-bare only); --no-optional-locks keeps status from writing the index.
  if [ "$bare" != "true" ]; then
    local head entry paths=() skip=false n=0
    head=$(git -C "$SOURCE_REAL" rev-parse --verify --quiet "HEAD^{commit}" 2>/dev/null </dev/null || true)
    while IFS= read -r -d '' entry; do
      if $skip; then skip=false; continue; fi
      paths+=("${entry:3}")
      case "${entry:0:1}" in R|C) skip=true ;; esac   # a rename's second field is its origin
    done < <(git --no-optional-locks -C "$SOURCE_REAL" -c core.fsmonitor=false \
               status --porcelain=v1 -z </dev/null 2>/dev/null || true)
    if [ ${#paths[@]} -gt 0 ]; then
      echo "⚠️  uncommitted changes in $(q "$SOURCE_REAL") are NOT installed (they are relative to its HEAD):"
      for entry in "${paths[@]}"; do
        n=$((n + 1))
        [ "$n" -le 20 ] || break
        echo "      $(q "$entry")"
      done
      [ ${#paths[@]} -le 20 ] || echo "      ... and $(( ${#paths[@]} - 20 )) more"
    fi
    if [ "$SRC_COMMIT" != "$head" ]; then
      echo "    (installing $SRC_COMMIT, which is not HEAD)"
    fi
  fi
  # 10. The commit is known before any write: every prompt and the report show it.
  SOURCE_KIND="local"
  UPSTREAM_SHA=""
  ANNOUNCED_SHA="$SRC_COMMIT"
  echo "    source: local $(q "$SOURCE_REAL") @ $SRC_COMMIT (ref $(q "$SOURCE_REF"))"
}

# The commit a consent prompt shows before the clone exists: ls-remote, verified after the clone.
announce_upstream_sha() {
  [ "$SOURCE_KIND" = "local" ] && return 0   # resolved before preflight; no ls-remote
  [ -n "$UPSTREAM_SHA$ANNOUNCED_SHA" ] && return 0
  ANNOUNCED_SHA=$(git ls-remote -- "$REPO_URL" "refs/heads/$BRANCH" 2>/dev/null </dev/null | cut -f1 | head -n 1) || ANNOUNCED_SHA=""
}

# ---------------------------------------------------------------------------
# Preflight (before any write)
# ---------------------------------------------------------------------------
python_guidance() {
  cat >&2 <<'GUIDE'
    HACF needs Python >= 3.10 with PyYAML >= 5.4. Fix it with one of these, then re-run:
      - any OS (uv):            uv python install 3.12, then put that python3 first on PATH
      - macOS (Homebrew):       brew install python@3.12
      - Ubuntu/Debian:          sudo apt install python3-yaml
                                (older releases: sudo add-apt-repository ppa:deadsnakes/ppa
                                 && sudo apt install python3.12)
      - PyYAML with pip:        python3 -m pip install --user 'pyyaml>=5.4'
    Nothing was written.
GUIDE
}

# Prints "ok", "python-old <version>", "yaml-missing" or "yaml-old <version>" (the rule of
# .agents/scripts/_runtime.py: leading numeric parts compared as integers).
python_status() {
  python3 - </dev/null <<'PY' 2>/dev/null || echo "python-broken"
import re
import sys

def parts(text):
    match = re.match(r"\d+(?:\.\d+)*", str(text).strip())
    return tuple(int(p) for p in match.group(0).split(".")) if match else ()

if tuple(sys.version_info[:2]) < (3, 10):
    print("python-old %d.%d.%d" % tuple(sys.version_info[:3]))
    sys.exit(0)
try:
    import yaml
except ImportError:
    print("yaml-missing")
    sys.exit(0)
version = str(getattr(yaml, "__version__", ""))
print("ok" if parts(version) >= (5, 4) else "yaml-old " + (version or "unknown"))
PY
}

externally_managed() {
  python3 -c 'import os, sysconfig; print(os.path.exists(os.path.join(sysconfig.get_path("stdlib"), "EXTERNALLY-MANAGED")))' </dev/null 2>/dev/null | grep -qx True
}

preflight() {
  echo "🔎  Preflight (nothing is written before it passes):"
  command -v git >/dev/null 2>&1 || die "git is required but not installed. Nothing was written."
  echo "    ✓  git"
  echo "    ✓  bash $_hacf_bash_major"
  if ! command -v python3 >/dev/null 2>&1; then
    echo "❌  python3 was not found." >&2
    python_guidance
    exit 1
  fi
  local status
  status=$(python_status)
  case "$status" in
    ok) ;;
    yaml-missing)
      echo "    ⚠️   PyYAML is not installed for $(command -v python3)."
      if $DRY_RUN; then
        echo "    [DRY-RUN] would ask to run: python3 -m pip install --user pyyaml"
        python_guidance
        exit 1
      fi
      if ! $INSTALL_DEPS && ! $AUTO_YES && tty_available; then announce_upstream_sha; fi
      if prompt_consent "$INSTALL_DEPS" "Install PyYAML with: python3 -m pip install --user pyyaml"; then
        if externally_managed; then
          cat >&2 <<'PEP668'
❌  This Python is externally managed (PEP 668), so pip would refuse a --user install.
    Use one of these instead, then re-run the installer:
      - uv:   uv venv && . .venv/bin/activate && uv pip install 'pyyaml>=5.4'
      - venv: python3 -m venv .venv && . .venv/bin/activate && pip install 'pyyaml>=5.4'
      - apt:  sudo apt install python3-yaml
    Nothing was written.
PEP668
          exit 1
        fi
        python3 -m pip install --user pyyaml </dev/null || { python_guidance; exit 1; }
        status=$(python_status)
        [ "$status" = "ok" ] || { echo "❌  PyYAML is still not usable ($status)." >&2; python_guidance; exit 1; }
      else
        echo "❌  PyYAML is missing and its install was not consented to (pass --install-deps)." >&2
        python_guidance
        exit 1
      fi
      ;;
    yaml-old\ *)
      echo "❌  PyYAML ${status#yaml-old } is older than 5.4." >&2
      python_guidance
      exit 1
      ;;
    python-old\ *)
      echo "❌  Python ${status#python-old } is older than 3.10." >&2
      python_guidance
      exit 1
      ;;
    *)
      echo "❌  python3 could not run the version check." >&2
      python_guidance
      exit 1
      ;;
  esac
  echo "    ✓  python3 >= 3.10 with PyYAML >= 5.4"
  echo ""
}

require_git_root() {
  local top
  if ! top=$(git rev-parse --show-toplevel 2>/dev/null </dev/null); then
    if $INTERACTIVE && ! $DRY_RUN && ask_yn "No git repository here. Initialize one (git init)?" n; then
      git init -q </dev/null
      echo "    ✅  git repository initialized."
      return 0
    fi
    die "no git repository here: run \`git init\` (or cd to your repository's root) and re-run. Nothing was written."
  fi
  if [ "$(cd "$top" && pwd -P)" != "$(pwd -P)" ]; then
    die "run the installer from the repository root ($top). Nothing was written."
  fi
}

# ---------------------------------------------------------------------------
# Mode, IDE and backend selection
# ---------------------------------------------------------------------------
detect_mode() {
  # Upgrade only when HACF itself is here; OpenRig-projected .agents/skills alone is fresh.
  if [ -f "$MANIFEST_REL" ] || [ -f ".agents/scripts/hypergraph_updater.py" ]; then
    UPGRADE_MODE=true
    echo "🔄  Existing HACF installation detected — running in UPGRADE mode."
  else
    echo "🆕  No HACF installation found — running a fresh install."
  fi
  echo ""
  if ! $UPGRADE_MODE && [ -z "$FILES_TARGET" ]; then
    if [ -n "$MODE" ] && [ "$MODE" != "dry-run" ]; then
      echo "    (--mode=$MODE applies to upgrades; a fresh install runs the full install)"
    fi
    MODE="install"
  fi
  if $UPGRADE_MODE && [ -z "$MODE" ] && [ -z "$FILES_TARGET" ]; then
    if ! $INTERACTIVE; then
      MODE="full"
      echo "🤖  Update mode: full (auto/non-interactive)"
    else
      echo "📋  Select update mode:"
      echo "    1) Full update     — system + skills  (recommended)"
      echo "    2) Skills only     — .agents/skills/ + IDE skill bridges"
      echo "    3) System only     — scripts, schemas, hooks, rules (not skills)"
      echo "    4) IDE files only  — IDE dirs, instruction-file blocks, settings"
      echo "    5) Repair / verify — install only missing pieces"
      echo "    6) Dry-run preview — show what would change, touch nothing"
      case "$(tty_ask "    Selection [1]: ")" in
        ""|1) MODE="full" ;;
        2) MODE="skills" ;;
        3) MODE="system" ;;
        4) MODE="ide" ;;
        5) MODE="repair" ;;
        6) MODE="full"; DRY_RUN=true ;;
        *) echo "    ⚠️  Invalid selection, defaulting to full."; MODE="full" ;;
      esac
      echo "    ✅  Mode: $MODE"
    fi
    echo ""
  fi
  [ "$MODE" = "dry-run" ] && MODE="full"
  if $DRY_RUN; then
    echo "🔍  DRY-RUN — every write, merge and download is printed; nothing is written."
    echo ""
  fi
}

# read_recorded_ides: RECORDED_IDE_IDS from the old manifest's `ides` (upgrade only), with
# each id unknown to IDE_DEFS dropped and warned about.
read_recorded_ides() {
  RECORDED_IDE_IDS=""
  $UPGRADE_MODE && [ -f "$MANIFEST_REL" ] || return 0
  local raw id known
  raw=$(python3 -c '
import json, sys
try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        data = json.load(handle)
except (OSError, ValueError):
    data = {}
ides = data.get("ides") if isinstance(data, dict) else None
for ide in ides if isinstance(ides, list) else []:
    if isinstance(ide, str) and ide.isprintable() and ide.split() == [ide]:
        print(ide)
' "$MANIFEST_REL" </dev/null 2>/dev/null || true)
  known="$(all_ide_ids)"
  while IFS= read -r id; do
    [ -n "$id" ] || continue
    if contains_id "$id" $known; then
      contains_id "$id" $RECORDED_IDE_IDS || RECORDED_IDE_IDS="$RECORDED_IDE_IDS $id"
    else
      echo "    ⚠️  ignoring unknown IDE id '$(q "$id")' recorded in $MANIFEST_REL"
    fi
  done <<<"$raw"
}

select_ides() {
  case "$MODE" in skills|system) return 0 ;; esac
  [ -n "$FILES_TARGET" ] && return 0
  read_recorded_ides
  if [ -n "$PRESELECTED_IDES" ]; then
    if [ "$PRESELECTED_IDES" = "all" ]; then
      SELECTED_IDE_IDS="$(all_ide_ids)"
    else
      SELECTED_IDE_IDS=" ${PRESELECTED_IDES//,/ }"
    fi
    echo "🤖  IDE selection:$SELECTED_IDE_IDS (from --ides)"
  elif ! $INTERACTIVE && [ -n "$RECORDED_IDE_IDS" ]; then
    SELECTED_IDE_IDS="$RECORDED_IDE_IDS"
    IDES_FROM_RECORD=true
    echo "🤖  IDE selection:$SELECTED_IDE_IDS (recorded by the previous install; use --ides= to change)"
  elif ! $INTERACTIVE; then
    SELECTED_IDE_IDS="$(all_ide_ids)"
    echo "🤖  IDE selection: all (non-interactive default; use --ides= to choose):$SELECTED_IDE_IDS"
  else
    echo "🖥️   Select the Agentic Coding IDE(s) to install support for."
    echo "    Enter the numbers separated by spaces, or type 'a' for all."
    local i=1 def raw num prompt="    Selection [a = all]: " numbers=""
    for def in "${IDE_DEFS[@]}"; do
      printf "    %d) %-38s  %s %s\n" "$i" "$(ide_display "$def")" "$(ide_dirs "$def")" "$(ide_files "$def")"
      contains_id "$(ide_id "$def")" $RECORDED_IDE_IDS && numbers="$numbers $i"
      i=$((i + 1))
    done
    if [ -n "$RECORDED_IDE_IDS" ]; then
      prompt="    Selection [Enter = recorded:$numbers ($(echo $RECORDED_IDE_IDS | tr ' ' ',')); a = all]: "
    fi
    raw=$(tty_ask "$prompt")
    if [[ -z "$raw" && -n "$RECORDED_IDE_IDS" ]]; then
      SELECTED_IDE_IDS="$RECORDED_IDE_IDS"
      IDES_FROM_RECORD=true
    elif [[ -z "$raw" || "$raw" == "a" || "$raw" == "A" || "$raw" == "all" ]]; then
      SELECTED_IDE_IDS="$(all_ide_ids)"
    else
      for num in $raw; do
        if [[ "$num" =~ ^[0-9]+$ ]] && [ "$num" -ge 1 ] && [ "$num" -le "${#IDE_DEFS[@]}" ]; then
          SELECTED_IDE_IDS="$SELECTED_IDE_IDS $(ide_id "${IDE_DEFS[$((num - 1))]}")"
        else
          echo "    ⚠️  Ignoring invalid selection: $num"
        fi
      done
    fi
    [[ -n "${SELECTED_IDE_IDS// /}" ]] || die "no valid IDEs selected. Nothing was written."
    echo "    ✅  Selected:$SELECTED_IDE_IDS"
  fi
  echo ""
}

select_code_graph() {
  local existing=""
  if [ -f .agents/local/hacf.yml ]; then
    existing=$(hx yamlget .agents/local/hacf.yml code_graph backend </dev/null || true)
  fi
  if [ -n "$CODE_GRAPH" ]; then
    CODE_GRAPH_BACKEND="$CODE_GRAPH"
  elif [ -n "$existing" ]; then
    CODE_GRAPH_BACKEND="$existing"
  elif $INTERACTIVE && [ -z "$FILES_TARGET" ] && [[ "$MODE" == "install" || "$MODE" == "full" ]]; then
    if ask_yn "Use the codebase-memory-mcp $CBM_VERSION code graph for blast-radius analysis?" n; then
      CODE_GRAPH="codebase-memory-mcp"
      CODE_GRAPH_BACKEND="$CODE_GRAPH"
    fi
  fi
  echo "🧭  Code-graph backend: $CODE_GRAPH_BACKEND"
  echo ""
}

# ---------------------------------------------------------------------------
# Clone (into the temp dir only)
# ---------------------------------------------------------------------------
clone_upstream() {
  SRC="$TMP_DIR/upstream"
  if [ "$SOURCE_KIND" = "local" ]; then
    fetch_local_source
    return 0
  fi
  echo "📦  Fetching framework: $REPO_URL (branch: $BRANCH)..."
  if ! git clone --quiet --depth=1 --branch "$BRANCH" -- "$REPO_URL" "$SRC" </dev/null; then
    die "could not clone $REPO_URL (branch $BRANCH). Nothing was written."
  fi
  UPSTREAM_SHA=$(git -C "$SRC" rev-parse HEAD </dev/null)
  if [ -n "$ANNOUNCED_SHA" ] && [ "$ANNOUNCED_SHA" != "$UPSTREAM_SHA" ]; then
    die "upstream moved from $ANNOUNCED_SHA (shown in the consent prompt) to $UPSTREAM_SHA; re-run. Nothing was written."
  fi
  # C4 (OPR.99.0.10.3): the manifest records the fetched installer's version, not this script's.
  INSTALL_VERSION=$(read_hacf_version "$(cat -- "$SRC/HACF-install.sh" 2>/dev/null || true)")
  [ -n "$INSTALL_VERSION" ] \
    || die "the fetched HACF-install.sh carries no readable HACF_VERSION. Nothing was written."
  echo "    ✅  upstream $REPO_URL @ $UPSTREAM_SHA (HACF $INSTALL_VERSION)"
  echo ""
}

# lgit <args>: git under an isolated config (no system or global file), autocrlf and fsmonitor
# off, so the temp checkout holds the commit's blobs byte for byte (R56, F2, F18).
lgit() {
  GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null \
    git -c core.autocrlf=false -c core.fsmonitor=false "$@" </dev/null
}

# Task 5: the commit object into the temp dir. init + fetch by sha, not clone: a clone carries
# only refs/heads, tags and HEAD (F2). The fetch only reads the source.
fetch_local_source() {
  echo "📦  Fetching framework: local $(q "$SOURCE_REAL") @ $SRC_COMMIT..."
  lgit init -q -- "$SRC" >/dev/null \
    || die "could not create the temp checkout. Nothing was written."
  lgit -C "$SRC" fetch --quiet --no-tags -- "$SOURCE_REAL" "$SRC_COMMIT" \
    || die "could not fetch $SRC_COMMIT from $(q "$SOURCE_REAL"). Nothing was written."
  lgit -C "$SRC" checkout --quiet --detach "$SRC_COMMIT" \
    || die "could not check out $SRC_COMMIT in the temp dir. Nothing was written."
  UPSTREAM_SHA=$(lgit -C "$SRC" rev-parse HEAD)
  if [ "$UPSTREAM_SHA" != "$SRC_COMMIT" ]; then
    die "the temp checkout is at $UPSTREAM_SHA, not $SRC_COMMIT. Nothing was written."
  fi
  echo "    ✅  upstream local $(q "$SOURCE_REAL") @ $UPSTREAM_SHA"
  echo ""
}

# Task 6 (F18): a symlinked copy root would copy whatever it points to; refused before any write.
check_payload_links() {
  local roots=() d def root
  for d in "${AGENT_SYSTEM_SUBDIRS[@]}"; do roots+=(".agents/$d"); done
  roots+=(".agents/skills" ".windsurf/workflows" ".agents/install-templates" "${ROOT_COPY_FILES[@]}")
  for def in "${IDE_DEFS[@]}"; do
    for d in $(ide_dirs "$def"); do roots+=("$d"); done
  done
  for root in "${roots[@]}"; do
    if [ -L "$SRC/$root" ]; then
      die "the upstream commit's $root is a symlink; refusing to copy it. Nothing was written."
    fi
  done
}

# ---------------------------------------------------------------------------
# The OpenRig team spec (--rig; mission R3-R5, R7)
# ---------------------------------------------------------------------------
RIG_SRC_REL="openrig/hacf"

# saved_v05x_uninstaller: true when ./HACF-uninstall.sh is a saved v0.5.x blob (FLOW 15).
saved_v05x_uninstaller() {
  [ -f HACF-uninstall.sh ] && in_list "$(py_sha HACF-uninstall.sh)" "${V05X_UNINSTALLER_SHAS[@]}"
}

# decide_rig_copy: RIG_COPY from --rig, or (R5) one local question, default N, asked only when
# interactive, without --rig, with no copy recorded and no saved v0.5.x uninstaller. Asked
# before check_rig_dest, so a yes is validated before any write. Never prompt_consent.
decide_rig_copy() {
  local copies_mode=true
  [ -z "$FILES_TARGET" ] && [[ " install full repair " == *" $MODE "* ]] || copies_mode=false
  if $RIG_REQUESTED; then
    $copies_mode || die "--rig copies only in a full, repair or fresh install, not in $MODE mode. Nothing was written."
    RIG_COPY=true
    return 0
  fi
  $INTERACTIVE && $copies_mode || return 0
  [ -z "$(hx jsonget "$MANIFEST_REL" rig_spec </dev/null || true)" ] || return 0
  saved_v05x_uninstaller && return 0
  if ask_yn "Copy HACF's OpenRig team spec to openrig/hacf/ for a per-project team?" n; then
    RIG_COPY=true
    RIG_DEST="openrig/hacf"
  fi
  echo ""
}

# check_rig_dest: every --rig refusal (R3), before the first project write. Exit 1 with
# "Nothing was written." Sets RIG_ABS, RIG_REL and RIG_SLUG.
check_rig_dest() {
  $RIG_COPY || return 0
  local refuse="refusing --rig:" payload="$SRC/$RIG_SRC_REL" project link names fields=() field
  project=$(pwd -P)
  [ -f "$payload/rig.yaml" ] && [ ! -L "$payload/rig.yaml" ] \
    || die "$refuse the upstream commit has no $RIG_SRC_REL/rig.yaml. Nothing was written."
  link=$(find "$payload" -type l -print -quit 2>/dev/null </dev/null || true)
  if [ -L "$payload" ] || [ -n "$link" ]; then
    die "$refuse the upstream commit's $RIG_SRC_REL holds a symlink. Nothing was written."
  fi
  names=$(grep -c '^name: hacf$' "$payload/rig.yaml" </dev/null || true)
  [ "$names" = "1" ] || die "$refuse the upstream $RIG_SRC_REL/rig.yaml has ${names:-0} 'name: hacf' lines, not one. Nothing was written."
  local git_dir="" common_dir="" out
  out=$(git rev-parse --git-dir 2>/dev/null </dev/null) && git_dir=$(realdir "$project" "$out" 2>/dev/null || true)
  out=$(git rev-parse --git-common-dir 2>/dev/null </dev/null) && common_dir=$(realdir "$project" "$out" 2>/dev/null || true)
  while IFS= read -r -d '' field; do fields+=("$field"); done < <(
    hx rigdest "$RIG_DEST" "$HOME" "$project" "$git_dir" "$common_dir" \
      "$([ "$SOURCE_KIND" = "local" ] && echo "$SOURCE_REAL")" "$TMP_DIR" </dev/null)
  [ "${fields[0]:-}" = "ok" ] || die "$refuse ${fields[1]:-could not check the destination}. Nothing was written."
  RIG_ABS="${fields[1]}"
  RIG_REL="${fields[2]:-}"
  if saved_v05x_uninstaller; then
    die "$refuse a saved v0.5.x uninstaller deletes every path in HACF main, openrig/hacf/ included; replace it first (re-run without --rig and accept the replacement), then re-run with --rig. Nothing was written."
  fi
  RIG_SLUG=$(basename -- "$project" | LC_ALL=C tr 'A-Z' 'a-z' | LC_ALL=C sed -E 's/[^a-z0-9]+/-/g; s/^-+//; s/-+$//')
  [ -n "$RIG_SLUG" ] || die "$refuse the project folder's name $(q "$(basename -- "$project")") gives an empty rig name slug. Nothing was written."
}

# rig_name_copy <payload> <tmp> <name>: give the temp copy the payload directory's mode and
# replace its rig.yaml's one `name: hacf` line with `name: <name>` (R7). Fails otherwise.
rig_name_copy() {
  python3 -c '
import os, re, sys
payload, tmp, name = sys.argv[1:]
os.chmod(tmp, os.stat(payload).st_mode & 0o7777)
path = os.path.join(tmp, "rig.yaml")
with open(path, encoding="utf-8", newline="") as handle:
    text = handle.read()
new, count = re.subn(r"(?m)^name: hacf$", "name: " + name, text)
if count != 1:
    sys.exit("rig.yaml has %d name: hacf lines, not one" % count)
with open(path, "w", encoding="utf-8", newline="") as handle:
    handle.write(new)
' "$@" </dev/null
}

# rig_rename <staged> <target>: one rename(2) through python3, so it has GNU mv's no-target-directory
# semantics on every POSIX host (macOS's mv lacks that flag): it never moves INTO a directory that appeared late, it
# fails instead (ENOTEMPTY / ENOTDIR). Only an empty directory is replaced.
rig_rename() {
  python3 -c 'import os,sys; os.rename(sys.argv[1], sys.argv[2])' "$1" "$2" </dev/null
}

# copy_rig_spec: the payload's openrig/hacf/ tree only (never hx sync, never the working tree),
# into a temp sibling of DEST, its one `name: hacf` line renamed, then one rename. On any
# failure the temp is removed and DEST does not exist. The copy is the user's from then on.
copy_rig_spec() {
  $RIG_COPY || return 0
  local payload="$SRC/$RIG_SRC_REL" parent tmp n name="$RIG_SLUG-hacf" shown project next
  project=$(pwd -P)
  parent=$(dirname -- "$RIG_ABS")
  n=$(find "$payload" -type f </dev/null | wc -l | tr -d ' ')
  shown="$RIG_ABS"; [ -n "$RIG_REL" ] && shown="$RIG_REL"
  echo "🧩  OpenRig team spec (--rig):"
  if $DRY_RUN; then
    echo "    [DRY-RUN] would copy $RIG_SRC_REL/ ($n files) to $(q "$shown") as rig $name"
    echo ""
    return 0
  fi
  mkdir -p -- "$parent" || die "could not create $(q "$parent") for the rig spec copy."
  tmp=$(mktemp -d "$parent/.hacf-rig.XXXXXX") || die "could not create a temp dir in $(q "$parent") for the rig spec copy."
  if ! { cp -R -p -- "$payload/." "$tmp/" \
         && rig_name_copy "$payload" "$tmp" "$name" \
         && [ ! -e "$RIG_ABS" ] && [ ! -L "$RIG_ABS" ]; }; then
    rm -rf -- "$tmp"
    die "could not copy the rig spec to $(q "$shown"); nothing was left there."
  fi
  if ! rig_rename "$tmp" "$RIG_ABS"; then
    rm -rf -- "$tmp"
    die "--rig: $(q "$shown") appeared during install; nothing written there"
  fi
  echo "    ✅  $RIG_SRC_REL/ ($n files) -> $(q "$shown"), rig name $name"
  if [ -n "$RIG_REL" ]; then
    next="rig up --plan $(q "$RIG_REL/rig.yaml") --name $name --cwd $(q "$project")"
    RIG_JSON=$(python3 -c 'import json,sys; print(json.dumps([{"path": sys.argv[1], "commit": sys.argv[2]}]))' \
      "$RIG_REL" "$UPSTREAM_SHA" </dev/null)
    report "Rig spec: copied to $(q "$RIG_REL") as rig $name. It is yours: an upgrade never refreshes it and uninstall leaves it."
    report "Rig spec: next, $next"
  else
    next="rig up --plan $(q "$RIG_ABS/rig.yaml") --name $name --cwd $(q "$project")"
    RIG_JSON=$(python3 -c 'import json,sys; print(json.dumps([{"outside_project": True, "commit": sys.argv[1]}]))' \
      "$UPSTREAM_SHA" </dev/null)
    report "Rig spec: copied outside this project to $(q "$RIG_ABS") as rig $name. It is yours: an upgrade never refreshes it and uninstall leaves it."
    report "Rig spec: next, $next (--cwd is required: a copy outside the project resolves its relative cwd elsewhere)"
  fi
  echo ""
}

# ---------------------------------------------------------------------------
# Installation steps
# ---------------------------------------------------------------------------
sync_paths() {
  # sync_paths <owner> <rel>...: per-file copy from the clone (FLOW 4-5, 7).
  local owner="$1" sync_mode="upgrade"; shift
  [ "$MODE" = "repair" ] && sync_mode="repair"
  $UPGRADE_MODE || [ "$sync_mode" = "repair" ] || sync_mode="fresh"
  hx sync "$SRC" "$sync_mode" "$($DRY_RUN && echo 1 || echo 0)" \
    ".agents/.backup/$BACKUP_TS" "$TMP_DIR/files.jsonl" "$owner" "$@" </dev/null
}

install_system_dirs() {
  echo "⚙️   System files (.agents/ — excluding skills), manifest-aware:"
  local had_config=() f
  for f in "$SRC"/.agents/config/*; do
    [ -f "$f" ] || continue
    [ -f ".agents/config/$(basename "$f")" ] && had_config+=("$(basename "$f")")
  done
  local subdirs=() d
  for d in "${AGENT_SYSTEM_SUBDIRS[@]}"; do subdirs+=(".agents/$d"); done
  # R64: every root copy file, HACF-uninstall.sh included, so a project has its own uninstaller
  # (recorded in the manifest, refreshed by an upgrade). A saved v0.5.x copy is FLOW 15's.
  local roots=() r
  for r in "${ROOT_COPY_FILES[@]}"; do
    if [ "$r" = "HACF-uninstall.sh" ] && [ -f "$r" ] \
        && in_list "$(py_sha "$r")" "${V05X_UNINSTALLER_SHAS[@]}"; then
      echo "    ⏭️   HACF-uninstall.sh: a saved v0.5.x uninstaller; FLOW 15 decides"
      continue
    fi
    roots+=("$r")
  done
  sync_paths hacf "${subdirs[@]}" "${roots[@]}"
  if $UPGRADE_MODE && ! $DRY_RUN; then
    local added=()
    for f in "$SRC"/.agents/config/*; do
      [ -f "$f" ] || continue
      in_list "$(basename "$f")" "${had_config[@]+"${had_config[@]}"}" || added+=(".agents/config/$(basename "$f")")
    done
    if [ ${#added[@]} -gt 0 ]; then
      report "Installed missing config file(s): ${added[*]} — the upgrade is complete (R24.10)."
    fi
  fi
  if ! $DRY_RUN; then
    chmod +x .agents/scripts/*.py 2>/dev/null || true
    local hook
    for hook in "${GIT_HOOKS[@]}"; do chmod +x ".agents/scripts/$hook" 2>/dev/null || true; done
  fi
  echo ""
}

install_skills() {
  echo "🧠  Skills (.agents/skills/, per file; skill dirs upstream does not ship are kept):"
  sync_paths hacf ".agents/skills"
  echo ""
}

install_skill_bridges() {
  echo "🔗  IDE skill bridges:"
  local found=false bridge
  for bridge in "${SKILL_BRIDGE_DIRS[@]}"; do
    [ -d "$SRC/$bridge" ] || continue
    if [ -d "$bridge" ]; then
      found=true
      sync_paths hacf "$bridge"
    else
      echo "    ⏭️   $bridge/ not installed in this project — skipping."
    fi
  done
  $found || echo "    ℹ️   No skill bridge directories found in this project."
  echo ""
}

install_ide_dirs() {
  local def id dirs=()
  for def in "${IDE_DEFS[@]}"; do
    id=$(ide_id "$def")
    contains_id "$id" $SELECTED_IDE_IDS || continue
    [ -n "$(ide_dirs "$def")" ] && dirs+=($(ide_dirs "$def"))
  done
  [ ${#dirs[@]} -gt 0 ] || return 0
  echo "🖥️   IDE directories:"
  sync_paths hacf "${dirs[@]}"
  echo ""
}

run_scaffold() {
  # FLOW 6: hyper_scaffold check then apply; a foreign collision skips it (legacy mode).
  echo "🏗️   Project scaffold (hyper_scaffold.py):"
  local script=".agents/scripts/hyper_scaffold.py" rc=0 out
  $DRY_RUN && script="$SRC/.agents/scripts/hyper_scaffold.py"
  out=$(python3 "$script" check </dev/null 2>&1) || rc=$?
  if [ "$rc" -eq 5 ]; then
    echo "$out" | sed 's/^/    /'
    echo "    ⚠️   A root item belongs to someone else: the scaffold was skipped (legacy mode)."
    report "Scaffold skipped: foreign root item(s) (see hyper_scaffold.py check); staying in legacy mode."
    echo ""
    return 0
  elif [ "$rc" -ne 0 ]; then
    echo "$out" | sed 's/^/    /'
    die "hyper_scaffold.py check failed (exit $rc)."
  fi
  if $DRY_RUN; then
    python3 "$script" apply --dry-run </dev/null 2>&1 | sed 's/^/    [DRY-RUN] /' || true
  else
    rc=0
    out=$(python3 "$script" apply --yes </dev/null 2>&1) || rc=$?
    echo "$out" | sed 's/^/    /'
    if [ "$rc" -eq 5 ]; then
      report "Scaffold skipped: foreign root item(s); staying in legacy mode."
    elif [ "$rc" -ne 0 ]; then
      die "hyper_scaffold.py apply failed (exit $rc)."
    fi
  fi
  echo ""
}

install_instruction_files() {
  # FLOW 8: AGENTS.md, CLAUDE.md and GEMINI.md through the hyper_blocks block engine (R9, R26).
  local def id file files=() rc out blocks="$SRC/.agents/install-templates"
  for def in "${IDE_DEFS[@]}"; do
    id=$(ide_id "$def")
    contains_id "$id" $SELECTED_IDE_IDS || continue
    for file in $(ide_files "$def"); do files+=("$file"); done
  done
  # K7 (R13): every IDE's instructions point at AGENTS.md, so any non-empty IDE selection
  # also installs it, once (universal already lists it). It goes through the same decision.
  if [ -n "${SELECTED_IDE_IDS// /}" ] && ! contains_id AGENTS.md "${files[@]+"${files[@]}"}"; then
    files+=("AGENTS.md")
  fi
  [ ${#files[@]} -gt 0 ] || return 0
  echo "📄  Instruction files (only the HACF managed block changes):"
  for file in "${files[@]}"; do
    if $PRESERVE_CUSTOM; then
      echo "    ⏭️   $file skipped (--preserve-custom)."
      continue
    fi
    if [ ! -f "$blocks/$file" ]; then
      echo "    ⚠️   upstream has no .agents/install-templates/$file — $file skipped."
      continue
    fi
    if $DRY_RUN; then
      echo "    [DRY-RUN] would apply the HACF block decision to $file"
      continue
    fi
    local yes=() manifest_sha=""
    $INTERACTIVE || yes=(--yes)
    manifest_sha=$(hx jsonget "$MANIFEST_REL" blocks "$file" hacf </dev/null || true)
    rc=0
    if [ -n "$manifest_sha" ] && grep -q '^<!-- BEGIN HACF MANAGED BLOCK: hacf -->' "$file" 2>/dev/null; then
      out=$(python3 .agents/scripts/hyper_blocks.py upsert "$file" --id hacf \
              --body-file "$blocks/$file" --manifest-sha "$manifest_sha" "${yes[@]+"${yes[@]}"}" </dev/null 2>&1) || rc=$?
    else
      out=$(python3 .agents/scripts/hyper_blocks.py instruction "$file" \
              --block-file "$blocks/$file" "${yes[@]+"${yes[@]}"}" </dev/null 2>&1) || rc=$?
    fi
    echo "$out" | sed 's/^/    /'
    if [ "$rc" -ne 0 ]; then
      report "$file: left unchanged (hyper_blocks exit $rc: duplicate, nested or unbalanced markers, or a concurrent change)."
    elif grep -q '^sidecar:' <<<"$out"; then
      report "$file: customized, so it was left unchanged; merge $(basename "$file" .md).hacf-v0.6.0.md into it, then delete that file."
    elif grep -q '^left:' <<<"$out"; then
      report "$file: your edits to the HACF block were kept; compare it with .agents/install-templates/$file."
    fi
  done
  echo ""
}

# KI-6: sha256 of each .claude/settings.json this run wrote (computed from the bytes just
# written, never read from the manifest, a backup or an earlier run). They go to later calls as
# --written-sha so the firewall skips only the backup of bytes this run itself wrote.
WRITTEN_SHAS=()
SHA_ARGS=()
set_sha_args() {
  local sha
  SHA_ARGS=()
  for sha in ${WRITTEN_SHAS[@]+"${WRITTEN_SHAS[@]}"}; do SHA_ARGS+=(--written-sha "$sha"); done
}
record_settings_write() {
  # $1 = the firewall's output; record the file's sha only when this call printed "wrote".
  if grep -q '^wrote: \.claude/settings\.json$' <<<"$1"; then
    WRITTEN_SHAS+=("$(py_sha .claude/settings.json)")
  fi
  return 0
}

merge_settings() {
  # FLOW 9: merge the fragment (allows, SessionStart hook, gate hook); never replace the file
  # and never touch .claude/settings.local.json.
  contains_id claude $SELECTED_IDE_IDS || return 0
  echo "🔧  Claude Code settings (.claude/settings.json, merged):"
  if $DRY_RUN; then
    echo "    [DRY-RUN] would run: python3 .agents/scripts/hyper_firewall.py merge-settings $FRAGMENT_REL"
    if $STATUSLINE; then
      echo "    [DRY-RUN] would merge statusLine: $STATUSLINE_COMMAND"
    fi
    echo ""
    return 0
  fi
  local rc=0 out
  set_sha_args
  out=$(python3 .agents/scripts/hyper_firewall.py merge-settings "$FRAGMENT_REL" ${SHA_ARGS[@]+"${SHA_ARGS[@]}"} </dev/null 2>&1) || rc=$?
  if [ "$rc" -eq 0 ]; then record_settings_write "$out"; fi
  grep -v '^owned: ' <<<"$out" | sed 's/^/    /' || true
  grep '^owned: ' <<<"$out" | sed 's/^owned: //' >>"$TMP_DIR/owned.jsonl" || true
  [ "$rc" -eq 0 ] || report ".claude/settings.json: merge refused (exit $rc); nothing written to it."
  # Q39: only with --statusline (the flag is the consent); without it no statusLine is added.
  if $STATUSLINE && prompt_consent true "Add a statusLine that runs \`$STATUSLINE_COMMAND\` (downloads from npm at session start)"; then
    local frag="$TMP_DIR/statusline.fragment.json"
    printf '{\n  "statusLine": {\n    "type": "command",\n    "command": "%s"\n  }\n}\n' "$STATUSLINE_COMMAND" >"$frag"
    rc=0
    set_sha_args
    out=$(python3 .agents/scripts/hyper_firewall.py merge-settings "$frag" ${SHA_ARGS[@]+"${SHA_ARGS[@]}"} </dev/null 2>&1) || rc=$?
    if [ "$rc" -eq 0 ]; then record_settings_write "$out"; fi
    grep -v '^owned: ' <<<"$out" | sed 's/^/    /' || true
    [ "$rc" -eq 0 ] && report "statusLine added: $STATUSLINE_COMMAND (an existing statusLine is never replaced)."
  fi
  echo ""
}

# mcp_entry_method (MR 1, OPR.99.0.10.4): this run's consented method, else code_graph.install_method
# from .agents/local/hacf.yml, else "" (today's default, the uvx form).
mcp_entry_method() {
  if [ -n "$CODE_GRAPH_METHOD" ]; then echo "$CODE_GRAPH_METHOD"; return 0; fi
  python3 -c '
import sys
import yaml
try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    method = (data.get("code_graph") or {}).get("install_method")
except Exception:
    method = None
print(method if isinstance(method, str) else "")
' .agents/local/hacf.yml </dev/null 2>/dev/null || true
}

# mcp_entry_record (MR 4): "skipped" when this run skips the entry (--no-mcp-entry, or a plain
# re-run of a project whose manifest records mcp_entry: skipped); "" otherwise.
mcp_entry_record() {
  case "$MCP_ENTRY_FLAG" in
    skip) echo "skipped"; return 0 ;;
    write) echo ""; return 0 ;;
  esac
  [ -f "$MANIFEST_REL" ] || return 0
  python3 -c '
import json, sys
try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        data = json.load(handle)
except (OSError, ValueError):
    data = {}
print("skipped" if isinstance(data, dict) and data.get("mcp_entry") == "skipped" else "")
' "$MANIFEST_REL" </dev/null 2>/dev/null || true
}

mcp_entry_json() {
  local method
  method=$(mcp_entry_method)
  if [ "$method" = "uv-tool" ] || [ "$method" = "path" ]; then
    echo '{"type": "stdio", "command": "codebase-memory-mcp", "args": []}'
  else
    echo "{\"type\": \"stdio\", \"command\": \"uvx\", \"args\": [\"codebase-memory-mcp@$CBM_VERSION\"]}"
  fi
}

install_code_graph() {
  # FLOW 10: hacf.yml selections, the consented install, then index.
  [ "$CODE_GRAPH_BACKEND" = "codebase-memory-mcp" ] || {
    if [ "$CODE_GRAPH" = "none" ]; then
      echo "🧭  Code graph: none (by flag)"
      hx localcfg .agents/local/hacf.yml "$($DRY_RUN && echo 1 || echo 0)" "backend=none!" </dev/null || true
      echo ""
    fi
    return 0
  }
  echo "🧭  Code graph: codebase-memory-mcp $CBM_VERSION"
  local verified label="" dry
  dry=$($DRY_RUN && echo 1 || echo 0)
  verified=$(python3 - "$SRC/.agents/config/code_graph.defaults.yml" </dev/null <<'PY' 2>/dev/null || echo unknown
import sys
import yaml
data = yaml.safe_load(open(sys.argv[1], encoding="utf-8")) or {}
backend = (data.get("backends") or {}).get("codebase-memory-mcp") or {}
print("true" if backend.get("verified") is True else "false")
PY
)
  [ "$verified" = "true" ] || label=" [unverified: the S04 contract test has not passed for it]"
  local cfg_args=()
  [ "$CODE_GRAPH" = "codebase-memory-mcp" ] && cfg_args+=("backend=codebase-memory-mcp!") || cfg_args+=("backend=codebase-memory-mcp")
  local how="" argv=()
  if command -v uv >/dev/null 2>&1; then
    how="uv-tool"; argv=(uv tool install "codebase-memory-mcp==$CBM_VERSION")
  elif command -v uvx >/dev/null 2>&1; then
    how="uvx"; argv=()
  elif command -v curl >/dev/null 2>&1; then
    how="path"; argv=(curl -fsSL "$CBM_VENDOR_URL")
  fi
  local action="install codebase-memory-mcp $CBM_VERSION$label"
  case "$how" in
    uv-tool) action="$action with: ${argv[*]}" ;;
    uvx)     action="$action through uvx (uvx codebase-memory-mcp@$CBM_VERSION fetches it on first use)" ;;
    path)    action="$action with the tag-pinned vendor script $CBM_VENDOR_URL --skip-config (CBM_DOWNLOAD_URL=$CBM_DOWNLOAD_URL)" ;;
    *)       action="$action (no uv, uvx or curl found)" ;;
  esac
  if [ -z "$how" ]; then
    echo "    ⚠️   No uv, uvx or curl on PATH: install codebase-memory-mcp $CBM_VERSION yourself."
    report "Code graph: codebase-memory-mcp selected but not installed (no uv, uvx or curl)."
  elif $DRY_RUN; then
    echo "    [DRY-RUN] would ask: $action"
  elif prompt_consent "$INSTALL_CODE_GRAPH" "$action"; then
    local rc=0
    case "$how" in
      uv-tool) "${argv[@]}" </dev/null || rc=$? ;;
      uvx)     rc=0 ;;
      path)
        local script="$TMP_DIR/cbm-install.sh"
        curl -fsSL "$CBM_VENDOR_URL" -o "$script" </dev/null || rc=$?
        [ "$rc" -eq 0 ] && { CBM_DOWNLOAD_URL="$CBM_DOWNLOAD_URL" bash "$script" --skip-config </dev/null || rc=$?; }
        ;;
    esac
    if [ "$rc" -eq 0 ]; then
      CODE_GRAPH_METHOD="$how"
      cfg_args+=("install_method=$how!")
      report "Code graph: codebase-memory-mcp $CBM_VERSION installed ($how)."
    else
      report "Code graph: the install failed (exit $rc); the backend stays unavailable until it is installed."
    fi
  else
    DECLINED+=("codebase-memory-mcp install (pass --install-code-graph)")
  fi
  hx localcfg .agents/local/hacf.yml "$dry" "${cfg_args[@]}" </dev/null || \
    report ".agents/local/hacf.yml: code_graph was not written (see above)."
  if [ -n "$CODE_GRAPH_METHOD" ] && ! $DRY_RUN; then
    if python3 .agents/scripts/hyper_codegraph.py index </dev/null; then
      echo "    ✅  indexed; project recorded in .agents/state/codegraph/project.json"
    else
      report "Code graph: hyper_codegraph.py index failed; run it again later."
    fi
  fi
  echo ""
}

register_mcp() {
  # FLOW 11: merge the stdio entry for the selected claude, cursor and roo targets only.
  [ "$CODE_GRAPH_BACKEND" = "codebase-memory-mcp" ] || return 0
  echo "🔌  MCP registration (selected IDEs only; other servers are kept):"
  local entry dry target id file any=false status_file="$TMP_DIR/mcp-status.tsv" line
  local f_file f_status f_argv
  entry=$(mcp_entry_json)
  dry=$($DRY_RUN && echo 1 || echo 0)
  if [ "$(mcp_entry_record)" = "skipped" ]; then
    # MR 4: --no-mcp-entry (or its record) writes no entry and leaves an existing one alone.
    echo "    ⏭️   codebase-memory-mcp entry skipped (--no-mcp-entry; --mcp-entry writes it)"
    report "code graph MCP entry: skipped: $(python3 -c 'import json,sys; e=json.loads(sys.argv[1]); print(" ".join([e["command"]] + e["args"]))' "$entry" </dev/null)"
    echo ""
    return 0
  fi
  : >"$status_file"
  for target in "claude:.mcp.json" "cursor:.cursor/mcp.json" "roo:.roo/mcp.json"; do
    id="${target%%:*}"; file="${target#*:}"
    contains_id "$id" $SELECTED_IDE_IDS || continue
    any=true
    hx mcp "$file" codebase-memory-mcp "$entry" "$TMP_DIR/mcp.jsonl" "$dry" "$status_file" </dev/null || \
      report "$file: invalid JSON; the MCP entry was not merged and the file was not changed."
  done
  # MR 3: one summary line per MCP file (the file named when it is not .mcp.json).
  while IFS=$'\t' read -r f_file f_status f_argv; do
    line="code graph MCP entry: $f_status: $f_argv"
    [ "$f_file" = ".mcp.json" ] || line="$line ($f_file)"
    report "$line"
  done <"$status_file"
  for id in windsurf cline antigravity; do
    contains_id "$id" $SELECTED_IDE_IDS || continue
    echo "    ℹ️   $id: add this MCP server by hand (config format UNVERIFIED): command \"$(python3 -c 'import json,sys; e=json.loads(sys.argv[1]); print(" ".join([e["command"]] + e["args"]))' "$entry" </dev/null)\", transport stdio."
    report "MCP for $id: not written; add the stdio server by hand (printed above)."
  done
  if $any; then
    report "MCP: the tracked .mcp.json, .cursor/mcp.json and .roo/mcp.json entries make every teammate who accepts the project server run \`uvx codebase-memory-mcp@$CBM_VERSION\` (exact pin)."
  fi
  echo ""
}

render_firewall() {
  # FLOW 12.
  if [ "$FIREWALL" = "off" ]; then
    report "Firewall: skipped (--firewall=off); render it later with python3 .agents/scripts/hyper_firewall.py render."
    return 0
  fi
  local ids
  ids=$(echo $SELECTED_IDE_IDS | tr ' ' ',')
  [ -n "$ids" ] || return 0
  echo "🛡️   Agent firewall (.agentignore rendered for: $ids):"
  if $DRY_RUN; then
    echo "    [DRY-RUN] would run: python3 .agents/scripts/hyper_firewall.py render --ides $ids"
    echo ""
    return 0
  fi
  local rc=0 out
  set_sha_args
  out=$(python3 .agents/scripts/hyper_firewall.py render --ides "$ids" ${SHA_ARGS[@]+"${SHA_ARGS[@]}"} </dev/null 2>&1) || rc=$?
  grep -v '^owned: ' <<<"$out" | sed 's/^/    /' || true
  grep '^owned: ' <<<"$out" | sed 's/^owned: //' >>"$TMP_DIR/owned.jsonl" || true
  [ "$rc" -eq 0 ] || report "Firewall: render refused (exit $rc); nothing written by it."
  echo ""
}

install_hooks() {
  # FLOW 13: pre-commit (staged-only), commit-msg, pre-push; existing hooks are backed up.
  local dir hook src dst sha
  dir="$(git rev-parse --git-common-dir </dev/null)/hooks"
  echo "🪝  Git hooks ($dir):"
  if [ -n "$(git config --get core.hooksPath 2>/dev/null </dev/null || true)" ]; then
    echo "    ⚠️   core.hooksPath is set, so git does not run hooks from $dir."
    report "Git hooks: installed in $dir, but core.hooksPath is set; wire them into your hooks path."
  fi
  for hook in "${GIT_HOOKS[@]}"; do
    src=".agents/scripts/$hook"; dst="$dir/$hook"
    if [ ! -f "$src" ]; then echo "    ⚠️   $src not found — skipping."; continue; fi
    if $DRY_RUN; then echo "    [DRY-RUN] would install $dst"; continue; fi
    if [ -f "$dst" ] && [ "$MODE" = "repair" ]; then echo "    ✓  $hook already present."; continue; fi
    if [ -f "$dst" ] && [ "$(py_sha "$dst")" != "$(py_sha "$src")" ]; then
      mkdir -p ".agents/.backup/$BACKUP_TS/git-hooks"
      cp -p "$dst" ".agents/.backup/$BACKUP_TS/git-hooks/$hook"
      echo "    💾  backed up the existing $hook hook -> .agents/.backup/$BACKUP_TS/git-hooks/$hook"
    fi
    mkdir -p "$dir"
    cp "$src" "$dst"
    chmod +x "$dst"
    sha=$(py_sha "$dst")
    printf '{"name": "%s", "path": "%s", "sha256": "%s"}\n' "$hook" "$dst" "$sha" >>"$TMP_DIR/hooks.jsonl"
    echo "    ✅  $hook"
  done
  echo ""
}

# save_installer (C2): copy the installing script's bytes (under `curl | bash`, the fetched
# commit's installer) to SAVED_INSTALLER_REL, mode 0755, temp file plus rename; sets its sha256.
SAVED_INSTALLER_SHA=""
save_installer() {
  local from="$SRC/HACF-install.sh" dir tmp
  if [ -n "$SELF_SCRIPT" ] && [ -f "$SELF_SCRIPT" ] && [ -r "$SELF_SCRIPT" ]; then
    from="$SELF_SCRIPT"
  fi
  dir=$(dirname -- "$SAVED_INSTALLER_REL")
  mkdir -p -- "$dir"
  tmp=$(mktemp "$dir/.HACF-install.sh.XXXXXX")
  cat -- "$from" >"$tmp" && chmod 0755 "$tmp" && mv -f -- "$tmp" "$SAVED_INSTALLER_REL" \
    || { rm -f -- "$tmp"; die "could not save the installer copy $SAVED_INSTALLER_REL"; }
  SAVED_INSTALLER_SHA=$(py_sha "$SAVED_INSTALLER_REL")
  echo "    ✅  $SAVED_INSTALLER_REL (re-run: bash $SAVED_INSTALLER_REL)"
}

# check_saved_copy (C2): run from SAVED_INSTALLER_REL (realpath compare), this script's sha256
# must equal the manifest's installer_sha256 beside it, before anything else happens.
check_saved_copy() {
  [ -n "$SELF_SCRIPT" ] && [ -f "$SELF_SCRIPT" ] || return 0
  local verdict
  verdict=$(python3 -c '
import hashlib, json, os, sys
self = os.path.realpath(sys.argv[1])
suffix = os.sep + sys.argv[2]
if not self.endswith(suffix):
    print("not-saved")
    sys.exit(0)
root = self[: -len(suffix)]
try:
    with open(os.path.join(root, sys.argv[3]), encoding="utf-8") as handle:
        want = json.load(handle).get("installer_sha256")
except (OSError, ValueError, AttributeError):
    want = None
with open(self, "rb") as handle:
    have = hashlib.sha256(handle.read()).hexdigest()
print("ok" if isinstance(want, str) and want == have else "mismatch")
' "$SELF_SCRIPT" "$SAVED_INSTALLER_REL" "$MANIFEST_REL" </dev/null 2>/dev/null || echo mismatch)
  case "$verdict" in
    not-saved|ok) return 0 ;;
    *) die "saved installer copy does not match the manifest; re-run the installer from the HACF source" ;;
  esac
}

write_manifest() {
  # FLOW 14 (R25.5): installed_commit is the clone's HEAD.
  echo "🧾  Manifest:"
  if $DRY_RUN; then
    echo "    [DRY-RUN] would write $MANIFEST_REL (installed_commit $UPSTREAM_SHA, source $(upstream_label))"
    echo ""
    return 0
  fi
  # source (F19, F3): never the absolute local path, which goes only to the per-machine file.
  local source_json
  if [ "$SOURCE_KIND" = "local" ]; then
    source_json=$(python3 -c 'import json,sys; print(json.dumps({"kind": "local", "commit": sys.argv[1]}))' \
      "$UPSTREAM_SHA" </dev/null)
  else
    source_json=$(python3 -c 'import json,sys; print(json.dumps({"kind": "remote", "url": sys.argv[1], "branch": sys.argv[2], "commit": sys.argv[3]}))' \
      "$REPO_URL" "$BRANCH" "$UPSTREAM_SHA" </dev/null)
  fi
  save_installer
  hx manifest "$INSTALL_VERSION" "$UPSTREAM_SHA" "$TMP_DIR/files.jsonl" "$TMP_DIR/owned.jsonl" \
    "$TMP_DIR/mcp.jsonl" "$TMP_DIR/hooks.jsonl" "$SRC/.agents/install-templates" "$source_json" \
    "$SELECTED_IDE_IDS" "$RIG_JSON" "$SAVED_INSTALLER_SHA" "$(mcp_entry_record)" </dev/null
  # Task 8: the per-machine source file, ignored through .agents/state/ (managed .gitignore
  # block and the scaffold's .agents/state/.gitignore). A remote install removes a stale one.
  if [ "$SOURCE_KIND" = "local" ]; then
    # C3 (OPR.99.0.10.3): the ref is the resolved full sha, never "HEAD".
    hx sourcefile "$SOURCE_FILE_REL" "$SOURCE_REAL" "$SRC_COMMIT" "$UPSTREAM_SHA" </dev/null
  elif [ -e "$SOURCE_FILE_REL" ] || [ -L "$SOURCE_FILE_REL" ]; then
    rm -f -- "$SOURCE_FILE_REL"
    echo "    ✅  removed $SOURCE_FILE_REL (this install is from a remote)"
  fi
  echo ""
}

check_saved_uninstaller() {
  # FLOW 15 (R10): a saved v0.5.x HACF-uninstall.sh deletes every path that exists in HACF main.
  [ -f HACF-uninstall.sh ] || return 0
  local sha new_sha
  sha=$(py_sha HACF-uninstall.sh)
  in_list "$sha" "${V05X_UNINSTALLER_SHAS[@]}" || return 0
  echo "⚠️   ./HACF-uninstall.sh is a saved v0.5.x uninstaller. Running it deletes every project file"
  echo "    whose path exists in HACF main — including .gitattributes (Git LFS rules),"
  echo "    lore_hypergraph.yml, docs/*, SPEC.md, project.yaml and workspace.yaml."
  if [ ! -f "$SRC/HACF-uninstall.sh" ] || in_list "$(py_sha "$SRC/HACF-uninstall.sh")" "${V05X_UNINSTALLER_SHAS[@]}"; then
    report "./HACF-uninstall.sh is a saved v0.5.x uninstaller and this upstream has no v0.6.0 replacement: delete it, never run it."
    echo ""
    return 0
  fi
  new_sha=$(py_sha "$SRC/HACF-uninstall.sh")
  if $DRY_RUN; then
    echo "    [DRY-RUN] would offer to replace it with the v0.6.0 uninstaller ($new_sha)"
  elif prompt_consent false "Replace ./HACF-uninstall.sh with the v0.6.0 uninstaller (the old one is backed up first)"; then
    mkdir -p ".agents/.backup/$BACKUP_TS"
    cp -p HACF-uninstall.sh ".agents/.backup/$BACKUP_TS/HACF-uninstall.sh"
    cp "$SRC/HACF-uninstall.sh" HACF-uninstall.sh
    report "./HACF-uninstall.sh replaced with the v0.6.0 uninstaller; the old one is in .agents/.backup/$BACKUP_TS/."
  else
    DECLINED+=("replacing the saved v0.5.x ./HACF-uninstall.sh")
    report "./HACF-uninstall.sh is a saved v0.5.x uninstaller: delete it, never run it."
  fi
  echo ""
}

run_migration() {
  # --migrate: plan only, unless an interactive user hands off to the migration's own apply.
  if [ ! -f .agents/scripts/hyper_migrate.py ]; then
    if $MIGRATE; then
      report "--migrate: .agents/scripts/hyper_migrate.py is not in this release yet; nothing migrated."
    fi
    return 0
  fi
  if ! $MIGRATE; then
    $UPGRADE_MODE && report "Migration: review it with python3 .agents/scripts/hyper_migrate.py plan"
    return 0
  fi
  echo "🧳  Migration plan:"
  if $DRY_RUN; then
    echo "    [DRY-RUN] would run: python3 .agents/scripts/hyper_migrate.py plan"
    echo ""
    return 0
  fi
  python3 .agents/scripts/hyper_migrate.py plan </dev/null || report "Migration: the plan command failed."
  if $INTERACTIVE && ask_yn "Run the migration now? (it asks for its own confirmation)" n; then
    python3 .agents/scripts/hyper_migrate.py apply || report "Migration: apply did not complete."
  else
    report "Migration: not applied. Run python3 .agents/scripts/hyper_migrate.py apply when ready."
  fi
  echo ""
}

install_files() {
  local targets="${FILES_TARGET//,/ }" name spec src dst do_chmod available hooks_dir
  hooks_dir="$(git rev-parse --git-common-dir </dev/null)/hooks"
  available="$(printf '%s ' "${!FILE_TARGETS[@]}")"
  echo "📄  Targeted file install (skips existing files):"
  for name in $targets; do
    spec="${FILE_TARGETS[$name]:-}"
    if [[ -z "$spec" ]]; then
      echo "    ⚠️  Unknown target: '$name' — skipping. Available: $available"
      continue
    fi
    IFS='|' read -r src dst do_chmod <<<"$spec"
    [[ "$dst" == hooks/* ]] && dst="$hooks_dir/${dst#hooks/}"
    if [[ -f "$dst" ]]; then echo "    ✓  $dst already present — skipping."; continue; fi
    if $DRY_RUN; then echo "    [DRY-RUN] would install $dst"; continue; fi
    mkdir -p "$(dirname "$dst")"
    cp "$SRC/$src" "$dst"
    [[ "$do_chmod" == "yes" ]] && chmod +x "$dst"
    echo "    ✅  $dst installed."
  done
  echo ""
}

print_report() {
  echo "📝  Install report (upstream $(upstream_label)):"
  if [ "$SOURCE_KIND" = "local" ]; then
    echo "    uninstall: bash HACF-uninstall.sh --source=$(q "$SOURCE_REAL") --ref=$UPSTREAM_SHA"
  fi
  echo "    IDEs:${SELECTED_IDE_IDS:- (unchanged)}$($IDES_FROM_RECORD && echo " (recorded)")"
  echo "    Code-graph backend: $CODE_GRAPH_BACKEND"
  if contains_id claude $SELECTED_IDE_IDS && [ -f "$SRC/$FRAGMENT_REL" ]; then
    echo "    Allow rules shipped in .claude/settings.json: $(python3 -c 'import json,sys; print(", ".join(json.load(open(sys.argv[1]))["permissions"]["allow"]))' "$SRC/$FRAGMENT_REL" </dev/null)"
  fi
  local line
  for line in "${DECLINED[@]+"${DECLINED[@]}"}"; do echo "    declined: $line"; done
  for line in "${REPORT[@]+"${REPORT[@]}"}"; do echo "    - $line"; done
  echo ""
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
  check_saved_copy
  parse_args "$@"
  resolve_upstream
  if ! $AUTO_YES && tty_available; then INTERACTIVE=true; fi

  echo ""
  echo "╔══════════════════════════════════════════════════════════╗"
  echo "║      Hypergraph Agentic Coding Framework Installer       ║"
  echo "╚══════════════════════════════════════════════════════════╝"
  echo ""
  if [ "$SOURCE_KIND" = "local" ]; then
    echo "    upstream: $(upstream_label)"
  else
    echo "    upstream: $REPO_URL (branch $BRANCH)"
  fi
  echo ""

  preflight                    # FLOW 1
  require_git_root
  detect_mode                   # FLOW 2
  select_ides
  select_code_graph

  TMP_DIR="$(mktemp -d)"
  trap cleanup EXIT
  clone_upstream                # FLOW 3
  check_payload_links           # F18: before any write, both kinds
  decide_rig_copy               # R5: --rig, or one local question
  check_rig_dest                # R3: every --rig refusal, before any project write
  BACKUP_TS="$(date -u +%Y%m%dT%H%M%SZ)"
  : >"$TMP_DIR/files.jsonl"; : >"$TMP_DIR/owned.jsonl"; : >"$TMP_DIR/mcp.jsonl"; : >"$TMP_DIR/hooks.jsonl"

  if [[ -n "$FILES_TARGET" ]]; then
    install_files
  else
    case "$MODE" in
      install|full|repair)
        install_system_dirs       # FLOW 4
        install_skills            # FLOW 5
        run_scaffold              # FLOW 6
        install_ide_dirs          # FLOW 7
        install_instruction_files # FLOW 8
        merge_settings            # FLOW 9
        install_code_graph        # FLOW 10
        register_mcp              # FLOW 11
        render_firewall           # FLOW 12
        install_hooks             # FLOW 13
        copy_rig_spec             # R3: --rig only
        write_manifest            # FLOW 14
        check_saved_uninstaller   # FLOW 15
        run_migration
        ;;
      skills)
        install_skills
        install_skill_bridges
        write_manifest
        ;;
      system)
        install_system_dirs
        run_scaffold
        install_hooks
        write_manifest
        ;;
      ide)
        install_ide_dirs
        install_instruction_files
        merge_settings
        register_mcp
        render_firewall
        write_manifest
        ;;
    esac
  fi

  print_report                  # FLOW 16
  if $DRY_RUN; then
    echo "╔══════════════════════════════════════════════════════════╗"
    echo "║  ✅  Dry-run complete — no files were modified.          ║"
    echo "╚══════════════════════════════════════════════════════════╝"
  elif [[ "$MODE" == "install" ]]; then
    echo "╔══════════════════════════════════════════════════════════╗"
    echo "║  ✅  Installation complete!                              ║"
    echo "║                                                          ║"
    echo "║  Next steps:                                             ║"
    echo "║    /hyper-discover, then /hyper-baseline to write SPEC.md║"
    echo "║    See AGENTS.md for full usage instructions             ║"
    echo "╚══════════════════════════════════════════════════════════╝"
  else
    echo "╔══════════════════════════════════════════════════════════╗"
    printf "║  ✅  %-52s║\n" "${FILES_TARGET:+File install}${FILES_TARGET:-Upgrade ($MODE)} complete!"
    echo "║                                                          ║"
    echo "║  Next: /hyper-discover, then /hyper-baseline to write    ║"
    echo "║  SPEC.md (if you have not yet).                          ║"
    echo "╚══════════════════════════════════════════════════════════╝"
  fi
  echo ""
}

# Everything above is definitions; under `curl | bash` bash has read the whole script before
# main runs, so no child process can consume the rest of it from stdin.
main "$@"
