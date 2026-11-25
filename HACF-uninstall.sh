#!/usr/bin/env bash
# Platform guard (R24): the first command, in bash-3.2 syntax, before any bash-4 construct.
if [ "${HACF_TEST:-}" = "1" ] && [ -n "${HACF_TEST_BASH_MAJOR:-}" ]; then
  _hacf_bash_major="$HACF_TEST_BASH_MAJOR"   # test-only override (BASH_VERSINFO is read-only)
else
  _hacf_bash_major="${BASH_VERSINFO[0]:-0}"
fi
case "$_hacf_bash_major" in ''|*[!0-9]*) _hacf_bash_major=0 ;; esac
if [ "$_hacf_bash_major" -lt 4 ]; then
  echo "❌  HACF-uninstall.sh needs bash 4 or newer; this is bash ${BASH_VERSION:-unknown}." >&2
  echo "    macOS ships bash 3.2: install a newer one with Homebrew (brew install bash)." >&2
  echo "    Nothing was changed." >&2
  exit 1
fi
set -euo pipefail
# The runs write no __pycache__ into the project (D1).
export PYTHONDONTWRITEBYTECODE=1

# Hypergraph Agentic Coding Framework — uninstaller (v0.6.0)
#
# Deletes only what HACF can prove it owns (R10, US-019):
# - with .agents/hacf-manifest.json (written by HACF-install.sh v0.6.0): the files it lists whose
#   sha256 still matches, the HACF managed blocks, the .claude/settings.json rules and hooks, the
#   MCP entries and the git hooks it records;
# - without one (a v0.5.x install): the files whose sha256 equals a file HACF shipped at v0.5.4 -
#   v0.5.9, from .agents/config/legacy_file_hashes.json in this uninstaller's own fresh clone,
#   never from the project (R24.10).
# A file with any other sha is kept and reported. NEVER_COPY paths are refused whatever the
# manifest or the hash list says: SPEC.md, project.yaml, workspace.yaml, lore/, missions/, spec/,
# tests/ and your root files are never deleted or edited. CLAUDE.local.md,
# .claude/settings.local.json and .gitattributes are never touched. Hashing uses Python hashlib.
# The project's own ./HACF-uninstall.sh (R64) is deleted last, after the manifest, and only when
# its sha256 still matches the manifest.
#
# Design: the installer section of HACF's v0.6.0 design notes (not shipped) (UNINSTALL; MiniPRD S12c).

usage() {
  cat <<'USAGE'
Usage: bash HACF-uninstall.sh [options]

  -y, --yes               proceed without asking; consents to nothing else
  --dry-run               list every deletion, block removal and settings edit; change nothing
  --delete-code-graph     consent to deleting this project's codebase-memory-mcp index
  --repo-url=URL          upstream repository the uninstaller clones (https:// only)
  --branch=NAME           upstream branch (default main)
  --source=PATH           take HACF's code from a commit of a local HACF git checkout instead
                          of cloning; cannot be combined with --repo-url or --branch
  --ref=REF               commit, branch or tag in --source (default: the manifest's
                          installed_commit, else HEAD)
  Without these, a project installed with --source uses the source recorded in
  .agents/state/install/source.json
  -h, --help              this text

Run it from the project's root. Without a terminal and without -y it changes nothing.
USAGE
}

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DEFAULT_REPO_URL="https://github.com/tjmustard/Hypergraph-Agentic-Coding-Framework.git"
DEFAULT_BRANCH="main"
MANIFEST_REL=".agents/hacf-manifest.json"
SOURCE_FILE_REL=".agents/state/install/source.json"   # per-machine record of a --source install
SOURCE_SCHEMA="hacf.install-source/v1"
LEGACY_REL=".agents/config/legacy_file_hashes.json"
CODEGRAPH_STATE_REL=".agents/state/codegraph/project.json"
SAVED_INSTALLER_REL=".agents/state/hacf/installer/HACF-install.sh"   # the installer's saved copy (C2)

# NEVER_COPY (target layout in HACF's design notes; the same list as HACF-install.sh): the
# uninstaller refuses to delete any of these. A trailing / means the directory and everything
# under it. Any other root file is refused too, unless DELETABLE_ROOT_FILES names it.
NEVER_COPY=(
  "SPEC.md" "project.yaml" "workspace.yaml" "lore_hypergraph.yml" ".gitattributes"
  "lore/" "missions/" "exhaust/" ".openrig/"
  "spec/" "tests/"
  "dev/" "PRDs/" "docs/"
  ".agents/local/" ".agents/state/" ".agents/.backup/" ".agents/logs/"
  ".agents/memory/"
  ".claude/settings*.json" ".mcp.json" ".cursor/mcp.json" ".roo/mcp.json" ".gitignore"
  "CHANGELOG.md" "README.md" "CITATION.cff" "pyproject.toml" "uv.lock" "requirements.txt"
  ".agent"
)
# Root files HACF copied whole: v0.6.0's ROOT_COPY_FILES, plus the v0.5.x instruction files
# (deleted only when their sha is a shipped v0.5.x template).
DELETABLE_ROOT_FILES=(".agentignore" "HACF-uninstall.sh" "AGENTS.md" "CLAUDE.md" "GEMINI.md")

# HACF managed blocks (hyper_blocks.py ids) the uninstaller removes, besides any the manifest
# records: "<file>|<block id>". The merge-drivers block of info/attributes is added at run time.
BLOCK_TARGETS=(
  "AGENTS.md|hacf" "CLAUDE.md|hacf" "GEMINI.md|hacf"
  ".gitignore|gitignore" ".agentignore|agentignore"
  ".cursorignore|agentignore" ".rooignore|agentignore"
  ".codeiumignore|agentignore" ".geminiignore|agentignore"
)

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------
AUTO_YES=false
DRY_RUN=false
DELETE_CODE_GRAPH=false
FLAG_REPO_URL=""
FLAG_BRANCH=""
FLAG_SOURCE=""
FLAG_REF=""
SOURCE_GIVEN=false
REF_GIVEN=false

SOURCE_KIND="remote"   # remote | local (--source or the recorded source)
SOURCE_RECORDED=false  # true when the local source came from the install's record
SOURCE_REAL=""         # the checkout, realpath'd; the only form passed to git
SOURCE_REF=""          # the ref as given
SRC_COMMIT=""          # the full sha SOURCE_REF names in SOURCE_REAL
LS_REASON=""           # why check_local_path refused a path
LS_BARE=""
REPO_URL=""
BRANCH=""
UPSTREAM_SHA=""
TMP_DIR=""
SRC=""
MODE=""            # manifest | legacy
BACKUP_TS=""
FAILURES=0
REPORT=()
DECLINED=()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
die() { echo "❌  $*" >&2; exit 1; }
usage_error() { echo "❌  $*" >&2; echo "" >&2; usage >&2; exit 2; }
report() { REPORT+=("$1"); }
fail() { FAILURES=$((FAILURES + 1)); report "$1"; }
upstream_label() {
  if [ "$SOURCE_KIND" = "local" ]; then
    echo "local $(printf '%q' "$SOURCE_REAL") @ ${UPSTREAM_SHA:-$SRC_COMMIT}"
  else
    echo "$REPO_URL @ ${UPSTREAM_SHA:-(commit not fetched yet)}"
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
# no under -y or without a terminal; otherwise ask on /dev/tty with default No (S12b semantics,
# R24.2). The prompt shows the effective upstream URL and commit (R12).
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

# ux <command> [args]: the uninstaller's one Python helper (hashlib, json, temp file +
# os.replace). It never starts a shell or evaluates code; paths come in as argv.
ux() {
  HACF_NEVER_COPY="$(printf '%s\n' "${NEVER_COPY[@]}")" \
  HACF_ROOT_FILES="$(printf '%s\n' "${DELETABLE_ROOT_FILES[@]}")" \
  python3 - "$@" <<'PY'
import fnmatch
import hashlib
import json
import os
import shutil
import sys
import tempfile

NEVER = [p for p in os.environ.get("HACF_NEVER_COPY", "").split("\n") if p]
ROOT_FILES = [p for p in os.environ.get("HACF_ROOT_FILES", "").split("\n") if p]
MANIFEST = ".agents/hacf-manifest.json"
SETTINGS = ".claude/settings.json"
MCP_FILES = (".mcp.json", ".cursor/mcp.json", ".roo/mcp.json")
METADATA_KEYS = ("schema", "releases")
LEGACY_PATH_LABEL = ".agents/config/legacy_file_hashes.json"
SELF_REL = "HACF-uninstall.sh"


def sha_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write(path, data):
    directory = os.path.dirname(path) or "."
    mode = os.stat(path).st_mode & 0o7777
    fd, tmp = tempfile.mkstemp(prefix=".hacf-", dir=directory)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _unique(pairs):
    seen = {}
    for key, value in pairs:
        if key in seen:
            raise ValueError(f"duplicate key {key!r}")
        seen[key] = value
    return seen


def load_object(path):
    """Return (dict, None), (None, None) for a missing file, or (None, error)."""
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle, object_pairs_hook=_unique)
    except FileNotFoundError:
        return None, None
    except (OSError, ValueError) as exc:
        return None, f"invalid JSON ({exc})"
    if not isinstance(data, dict):
        return None, "not a JSON object"
    return data, None


def manifest():
    data, _ = load_object(MANIFEST)
    return data or {}


def unsafe(rel):
    """True for a path that is not a plain relative path inside the project."""
    parts = rel.split("/")
    return (
        not rel
        or rel.startswith("/")
        or "\\" in rel
        or any(part in ("", ".", "..") for part in parts)
        or parts[0] == ".git"
    )


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
    return "/" not in rel and rel not in ROOT_FILES


def write_lines(path, rows, sep):
    with open(path, "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(row + sep)


def cmd_plan(args):
    """plan MODE LEGACY_LIST CANDIDATES REPORT SELF: the files to delete (NUL-separated) and why not.

    In manifest mode an unchanged HACF-uninstall.sh goes to SELF (`path<TAB>sha`), not to
    CANDIDATES: it is deleted last, after the manifest (R64).
    """
    mode, legacy_path, cand_path, report_path, self_path = args
    candidates, report, self_rows = [], [], []
    if mode == "manifest":
        files = manifest().get("files")
        if not isinstance(files, dict):
            print(f"    ❌  {MANIFEST}: no files map; no file will be deleted")
            files = {}
        owned = {}
        for rel, entry in files.items():
            sha = entry.get("sha256") if isinstance(entry, dict) else None
            owned[rel] = {sha} if isinstance(sha, str) else set()
        basis = "the manifest"
    else:
        data, error = load_object(legacy_path)
        if data is None:
            print(f"    ❌  the clone's {LEGACY_PATH_LABEL}: {error or 'missing'}; no file will be deleted")
            data = {}
        owned = {}
        for rel, value in data.items():
            if rel in METADATA_KEYS:
                continue
            values = value if isinstance(value, list) else [value]
            owned[rel] = {v for v in values if isinstance(v, str) and len(v) == 64}
        basis = "any HACF v0.5.4-v0.5.9 release"
    for rel in sorted(owned):
        exists = os.path.lexists(rel)
        if unsafe(rel) or never_copy(rel):
            if exists or mode == "manifest":
                report.append(f"refused\t{rel}")
            continue
        if not exists:
            continue
        if os.path.islink(rel) or not os.path.isfile(rel):
            report.append(f"notfile\t{rel}")
            continue
        sha = sha_file(rel)
        if sha not in owned[rel]:
            report.append(f"modified\t{rel}")
        elif mode == "manifest" and rel == SELF_REL:
            self_rows.append(f"{rel}\t{sha}")
        else:
            candidates.append(rel)
    write_lines(cand_path, candidates, "\0")
    write_lines(report_path, report, "\n")
    write_lines(self_path, self_rows, "\n")
    print(f"    {len(candidates) + len(self_rows)} file(s) match {basis} and will be deleted;"
          f" {sum(r.startswith('modified') for r in report)} changed file(s) are kept.")
    return 0



def cmd_blocks(args):
    """blocks SRC CANDIDATES TARGET...: print `path<TAB>id<TAB>manifest sha` per present block."""
    src, cand_path = args[0], args[1]
    sys.path.insert(0, os.path.join(src, ".agents", "scripts"))
    import hyper_blocks

    with open(cand_path, encoding="utf-8") as handle:
        candidates = set(p for p in handle.read().split("\0") if p)
    recorded = manifest().get("blocks") or {}
    targets = []
    for item in args[2:]:
        path, _, block_id = item.rpartition("|")
        targets.append((path, block_id))
    for path, ids in recorded.items() if isinstance(recorded, dict) else []:
        for block_id in ids if isinstance(ids, dict) else {}:
            targets.append((path, block_id))
    seen = set()
    for path, block_id in targets:
        if (path, block_id) in seen or path in candidates:
            continue
        seen.add((path, block_id))
        if os.path.basename(path) == "CLAUDE.local.md" or os.path.islink(path):
            continue
        if not os.path.isfile(path):
            continue
        sha = ((recorded.get(path) or {}) if isinstance(recorded, dict) else {}).get(block_id) or ""
        try:
            with open(path, "rb") as handle:
                doc = hyper_blocks.parse_doc(handle.read())
            blocks = hyper_blocks.scan(doc, hyper_blocks.style_of(hyper_blocks.Path(path)))
        except hyper_blocks.BlocksError:
            print(f"{path}\t{block_id}\t{sha}")  # the remove call reports the marker error
            continue
        if hyper_blocks.find_block(blocks, block_id) is not None:
            print(f"{path}\t{block_id}\t{sha}")
    return 0


def _prune(container, key):
    """Drop container[key] when it became empty."""
    if isinstance(container.get(key), (dict, list)) and not container[key]:
        del container[key]


def cmd_settings(args):
    """settings DRY BACKUP_DIR: remove the rules and hooks the manifest records (Task 17)."""
    dry, backup_dir = args[0] == "1", args[1]
    data = manifest()
    rules = data.get("settings_rules") or {}
    hooks = {k: v for k, v in (data.get("hooks") or {}).items() if k != "git"}
    if not any(rules.values()) and not any(hooks.values()):
        return 0
    settings, error = load_object(SETTINGS)
    if error:
        print(f"    ❌  {SETTINGS}: {error}; left unchanged")
        return 3
    if settings is None:
        return 0
    removed_rules = removed_hooks = 0
    permissions = settings.get("permissions")
    if isinstance(permissions, dict):
        for bucket, owned in rules.items():
            current = permissions.get(bucket)
            if not isinstance(current, list) or not isinstance(owned, list):
                continue
            kept = [rule for rule in current if rule not in owned]
            removed_rules += len(current) - len(kept)
            if len(kept) != len(current):
                permissions[bucket] = kept
                _prune(permissions, bucket)
        _prune(settings, "permissions")
    events = settings.get("hooks")
    if isinstance(events, dict):
        for event, owned in hooks.items():
            entries = events.get(event)
            if not isinstance(entries, list) or not isinstance(owned, list):
                continue
            new_entries = []
            for entry in entries:
                inner = entry.get("hooks") if isinstance(entry, dict) else None
                if not isinstance(inner, list):
                    new_entries.append(entry)
                    continue
                kept = [h for h in inner if not (isinstance(h, dict) and h.get("command") in owned)]
                removed_hooks += len(inner) - len(kept)
                if kept or not inner:
                    new_entries.append({**entry, "hooks": kept} if len(kept) != len(inner) else entry)
            if len(new_entries) != len(entries) or new_entries != entries:
                events[event] = new_entries
                _prune(events, event)
        _prune(settings, "hooks")
    if not removed_rules and not removed_hooks:
        print(f"    ✓  {SETTINGS}: no HACF rule or hook left to remove")
        return 0
    what = f"{removed_rules} rule(s) and {removed_hooks} hook(s) HACF added"
    if dry:
        print(f"    [DRY-RUN] would remove {what} from {SETTINGS}")
        return 0
    target = os.path.join(backup_dir, SETTINGS)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    shutil.copy2(SETTINGS, target)
    atomic_write(SETTINGS, (json.dumps(settings, indent=2, ensure_ascii=False) + "\n").encode())
    print(f"    ✅  {SETTINGS}: removed {what}; backup: {target}")
    if not settings:
        print(f"    ℹ️   {SETTINGS} is now an empty object; delete it if you do not need it")
    return 0


def cmd_mcp(args):
    """mcp DRY: remove the MCP entries the manifest records when unchanged (Task 18)."""
    dry = args[0] == "1"
    recorded = manifest().get("mcp_entries") or {}
    rc = 0
    for path, servers in sorted(recorded.items()) if isinstance(recorded, dict) else []:
        if path not in MCP_FILES or not isinstance(servers, dict) or not servers:
            continue
        data, error = load_object(path)
        if error:
            print(f"    ❌  {path}: {error}; left unchanged")
            rc = 3
            continue
        if data is None or not isinstance(data.get("mcpServers"), dict):
            continue
        current = data["mcpServers"]
        removed = []
        for server, entry in sorted(servers.items()):
            if server not in current:
                continue
            if current[server] == entry:
                removed.append(server)
            else:
                print(f"    ⚠️   {path}: {server} changed since install; kept")
        if not removed:
            continue
        if dry:
            print(f"    [DRY-RUN] would remove {', '.join(removed)} from {path}")
            continue
        for server in removed:
            del current[server]
        _prune(data, "mcpServers")
        atomic_write(path, (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode())
        print(f"    ✅  {path}: removed {', '.join(removed)} ({len(current)} other server(s) kept)")
    return rc


def cmd_githooks(args):
    """githooks DRY: remove the git hooks the manifest records when their sha matches (Task 19)."""
    dry = args[0] == "1"
    recorded = (manifest().get("hooks") or {}).get("git") or {}
    for name, entry in sorted(recorded.items()) if isinstance(recorded, dict) else []:
        path = entry.get("path") if isinstance(entry, dict) else None
        if not isinstance(path, str) or os.path.basename(path) != name:
            continue
        if os.path.basename(os.path.dirname(path)) != "hooks":
            print(f"    ⚠️   {path}: not in a hooks directory; left")
            continue
        if not os.path.isfile(path) or os.path.islink(path):
            continue
        if sha_file(path) != entry.get("sha256"):
            print(f"    ⚠️   {path}: changed since install; kept")
            continue
        if dry:
            print(f"    [DRY-RUN] would delete git hook {path}")
        else:
            os.unlink(path)
            print(f"    ✗  {path}")
    return 0


def cmd_codegraph(args):
    """codegraph: print the project name .agents/state/codegraph/project.json records."""
    data, _ = load_object(".agents/state/codegraph/project.json")
    name = (data or {}).get("project_name")
    if isinstance(name, str) and name.strip():
        print(name.strip())
    return 0


def cmd_prune(args):
    """prune DELETED: remove the directories that became empty, deepest first (Task 20)."""
    with open(args[0], encoding="utf-8") as handle:
        deleted = [p for p in handle.read().split("\0") if p]
    dirs = set()
    for rel in deleted:
        parent = os.path.dirname(rel)
        while parent:
            dirs.add(parent)
            parent = os.path.dirname(parent)
    for rel in sorted(dirs, key=lambda d: (-d.count("/"), d)):
        if os.path.isdir(rel) and not os.path.islink(rel) and not os.listdir(rel):
            os.rmdir(rel)
            print(f"    ✗  {rel}/ (empty)")
    return 0


def cmd_bytecode(args):
    """bytecode DRY CANDIDATES: delete the __pycache__/ next to each candidate .py (D1).

    A __pycache__ qualifies only when it is a real directory whose entries are all regular
    *.pyc files; any other one prints `kept<TAB><dir>/__pycache__` for the shell to report.
    """
    dry = args[0] == "1"
    with open(args[1], encoding="utf-8") as handle:
        paths = [p for p in handle.read().split("\0") if p]
    dirs = sorted({os.path.dirname(p) for p in paths if p.endswith(".py")})
    for directory in dirs:
        cache = os.path.join(directory, "__pycache__") if directory else "__pycache__"
        if not os.path.lexists(cache):
            continue
        entries = []
        ok = os.path.isdir(cache) and not os.path.islink(cache)
        if ok:
            entries = sorted(os.listdir(cache))
            for name in entries:
                full = os.path.join(cache, name)
                if not name.endswith(".pyc") or os.path.islink(full) or not os.path.isfile(full):
                    ok = False
                    break
        if not ok:
            print(f"kept\t{cache}")
        elif dry:
            print(f"    [DRY-RUN] would delete {cache}/ ({len(entries)} bytecode file(s))")
        else:
            for name in entries:
                os.unlink(os.path.join(cache, name))
            os.rmdir(cache)
            print(f"    ✗  {cache}/ (bytecode)")
    return 0


def cmd_drivers(args):
    """drivers SRC: `remove|kept<TAB><section>` per present HACF merge-driver git config section (D2).

    The expected values come from the clone's hyper_lore.DRIVER_CONFIG (R24.10), never the
    project's copy. A section is removable only when its keys and values equal them exactly.
    """
    sys.path.insert(0, os.path.join(args[0], ".agents", "scripts"))
    import subprocess

    import hyper_lore

    expected = {}
    for key, value in hyper_lore.DRIVER_CONFIG:
        expected.setdefault(key.rsplit(".", 1)[0], []).append((key, value))
    for section in ("merge.hacf-lore", "merge.hacf-generated"):
        proc = subprocess.run(
            ["git", "config", "--local", "--get-regexp", "^" + section.replace(".", "\\.") + "\\."],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            continue
        current = []
        for line in proc.stdout.splitlines():
            key, _, value = line.partition(" ")
            current.append((key, value))
        same = sorted(current) == sorted(expected.get(section, []))
        print(f"{'remove' if same else 'kept'}\t{section}")
    return 0


POINTERS = (".claude/commands/hyper-tutorial.md", ".windsurf/workflows/hyper-tutorial.md")


def cmd_leftovers(args):
    """leftovers SRC LEGACY_LIST CANDIDATES OUT: K9, `remove|keep<TAB>path<TAB>reason` rows.

    A legacy hyper-tutorial pointer is removable only when it is a regular file whose sha256 is
    listed for that exact path in the clone's legacy list (never the project's copy). A
    <STEM>.hacf-v0.6.0.md sidecar is removable only when its bytes equal
    sidecar_text(<instruction file>, B), B being the whole HACF block (both marker lines) still in
    that instruction file: the merge is finished. This runs in the plan phase, before any block
    is removed. Anything else is kept: it may hold a user's unfinished merge.
    """
    src, legacy_path, cand_path, out_path = args
    sys.path.insert(0, os.path.join(src, ".agents", "scripts"))
    import hyper_blocks

    with open(cand_path, encoding="utf-8") as handle:
        candidates = set(p for p in handle.read().split("\0") if p)
    data, _ = load_object(legacy_path)
    rows = []
    for rel in POINTERS:
        if rel in candidates or not os.path.lexists(rel):
            continue
        value = (data or {}).get(rel)
        values = value if isinstance(value, list) else [value]
        listed = {v for v in values if isinstance(v, str) and len(v) == 64}
        if os.path.islink(rel) or not os.path.isfile(rel):
            rows.append(f"keep\t{rel}\tnot a regular file; delete it by hand if unwanted")
        elif sha_file(rel) in listed:
            rows.append(f"remove\t{rel}\tHACF legacy file, sha matched")
        else:
            rows.append(f"keep\t{rel}\tcontent differs from HACF's; delete it by hand if unwanted")
    for name in hyper_blocks.INSTRUCTION_FILES:
        side = str(hyper_blocks.sidecar_path(hyper_blocks.Path(name)))
        if not os.path.lexists(side) or side in candidates:
            continue
        kept = "it may hold an unfinished merge; delete it by hand once merged"
        finished = False
        try:
            usable = all(
                os.path.isfile(path) and not os.path.islink(path) for path in (name, side)
            )
            if usable:
                with open(name, "rb") as handle:
                    doc = hyper_blocks.parse_doc(handle.read())
                block = hyper_blocks.find_block(
                    hyper_blocks.scan(doc, hyper_blocks.MARKDOWN), hyper_blocks.INSTRUCTION_BLOCK_ID
                )
                if block is not None:
                    whole = b"".join(doc.lines[block.begin : block.end + 1])
                    with open(side, "rb") as handle:
                        finished = handle.read() == hyper_blocks.sidecar_text(
                            hyper_blocks.Path(name), whole
                        )
        except (OSError, hyper_blocks.BlocksError):
            finished = False
        if finished:
            rows.append(f"remove\t{side}\tmerge finished: it equals the HACF block still in {name}")
        else:
            rows.append(f"keep\t{side}\t{kept}")
    write_lines(out_path, rows, "\n")
    return 0


def cmd_sha(args):
    """sha PATH: print the file's sha256 (R64's last check before remove_self deletes it)."""
    print(sha_file(args[0]))
    return 0


COMMANDS = {
    "plan": cmd_plan,
    "sha": cmd_sha,
    "leftovers": cmd_leftovers,
    "blocks": cmd_blocks,
    "settings": cmd_settings,
    "mcp": cmd_mcp,
    "githooks": cmd_githooks,
    "codegraph": cmd_codegraph,
    "prune": cmd_prune,
    "bytecode": cmd_bytecode,
    "drivers": cmd_drivers,
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
      -y|--yes)              AUTO_YES=true ;;
      --dry-run)             DRY_RUN=true ;;
      --delete-code-graph)   DELETE_CODE_GRAPH=true ;;
      --repo-url=*)          FLAG_REPO_URL="${1#--repo-url=}" ;;
      --repo-url)            [ $# -ge 2 ] || usage_error "--repo-url needs a value"; FLAG_REPO_URL="$2"; shift ;;
      --branch=*)            FLAG_BRANCH="${1#--branch=}" ;;
      --branch)              [ $# -ge 2 ] || usage_error "--branch needs a value"; FLAG_BRANCH="$2"; shift ;;
      --source=*)            SOURCE_GIVEN=true; FLAG_SOURCE="${1#--source=}" ;;
      --source)              [ $# -ge 2 ] || usage_error "--source needs a value"; SOURCE_GIVEN=true; FLAG_SOURCE="$2"; shift ;;
      --ref=*)               REF_GIVEN=true; FLAG_REF="${1#--ref=}" ;;
      --ref)                 [ $# -ge 2 ] || usage_error "--ref needs a value"; REF_GIVEN=true; FLAG_REF="$2"; shift ;;
      -h|--help)             usage; exit 0 ;;
      *)                     usage_error "unknown option: $1" ;;
    esac
    shift
  done
  if $SOURCE_GIVEN && [ -z "$FLAG_SOURCE" ]; then usage_error "--source needs a non-empty path. Nothing was changed."; fi
  if $REF_GIVEN && [ -z "$FLAG_REF" ]; then usage_error "--ref needs a non-empty value. Nothing was changed."; fi
  if $REF_GIVEN && ! $SOURCE_GIVEN; then usage_error "--ref needs --source. Nothing was changed."; fi
  if $SOURCE_GIVEN && { [ -n "$FLAG_REPO_URL" ] || [ -n "$FLAG_BRANCH" ]; }; then
    usage_error "--source cannot be combined with --repo-url or --branch. Nothing was changed."
  fi
}

resolve_upstream() {
  if $SOURCE_GIVEN; then
    # --source: one commit of a local checkout. The HACF_TEST env overrides are not read here.
    resolve_local_source
    return 0
  fi
  if [ -z "$FLAG_REPO_URL" ] && [ -z "$FLAG_BRANCH" ] \
      && ! { [ "${HACF_TEST:-}" = "1" ] && [ -n "${HACF_REPO_URL:-}" ]; }; then
    recorded_source
    [ "$SOURCE_KIND" = "local" ] && return 0
  fi
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
        die "refusing upstream URL '$REPO_URL': only https:// URLs are accepted. Nothing was changed."
      fi
      ;;
  esac
  case "$REPO_URL" in -*) die "refusing upstream URL '$REPO_URL'. Nothing was changed." ;; esac
  if [[ "$BRANCH" == -* ]] || ! git check-ref-format --branch "$BRANCH" >/dev/null 2>&1; then
    die "refusing branch name '$BRANCH'. Nothing was changed."
  fi
}

# ---------------------------------------------------------------------------
# Local source (slice 32; the installer's slice-31 checks without its version guard and target
# rules: the uninstaller only reads the source). Nothing here writes to the source or starts a
# network process.
# ---------------------------------------------------------------------------

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

is_sha() { [[ "$1" =~ ^[0-9a-f]{40}$ ]]; }

# check_local_path <path>: the installer's steps 1-5. On success SOURCE_REAL is the checkout's
# top level (or the bare repo) and LS_BARE is git's is-bare answer; otherwise LS_REASON says why.
check_local_path() {
  local path="$1" out rc=0 common top
  LS_REASON=""
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
  common=$(printf '%s\n' "$out" | sed -n 2p)
  realdir "$SOURCE_REAL" "$common" >/dev/null 2>&1 || { LS_REASON="not a git repository"; return 1; }
  if [ "$LS_BARE" != "true" ]; then
    top=$(git -C "$SOURCE_REAL" rev-parse --show-toplevel 2>/dev/null </dev/null) \
      || { LS_REASON="not a git repository"; return 1; }
    # git upload-pack opens the path it is given, not a parent: fetch from the top level.
    SOURCE_REAL=$(realdir "$top") || { LS_REASON="not a git repository"; return 1; }
  fi
  # 5. A partial clone would fetch missing objects from its own remote.
  if [ -n "$(git -C "$SOURCE_REAL" config --get extensions.partialClone 2>/dev/null </dev/null || true)" ]; then
    LS_REASON="partial clone (it would fetch from its remote)"; return 1
  fi
  return 0
}

# has_commit <sha>: the commit object is in SOURCE_REAL.
has_commit() { git -C "$SOURCE_REAL" cat-file -e "$1^{commit}" 2>/dev/null </dev/null; }

# warn_dirty: the installer's step 9 (non-bare only); --no-optional-locks keeps status from
# writing the index.
warn_dirty() {
  [ "$LS_BARE" != "true" ] || return 0
  local head entry paths=() skip=false n=0
  head=$(git -C "$SOURCE_REAL" rev-parse --verify --quiet "HEAD^{commit}" 2>/dev/null </dev/null || true)
  while IFS= read -r -d '' entry; do
    if $skip; then skip=false; continue; fi
    paths+=("${entry:3}")
    case "${entry:0:1}" in R|C) skip=true ;; esac   # a rename's second field is its origin
  done < <(git --no-optional-locks -C "$SOURCE_REAL" -c core.fsmonitor=false \
             status --porcelain=v1 -z </dev/null 2>/dev/null || true)
  if [ ${#paths[@]} -gt 0 ]; then
    echo "⚠️  uncommitted changes in $(q "$SOURCE_REAL") are NOT used (they are relative to its HEAD):"
    for entry in "${paths[@]}"; do
      n=$((n + 1))
      [ "$n" -le 20 ] || break
      echo "      $(q "$entry")"
    done
    [ ${#paths[@]} -le 20 ] || echo "      ... and $(( ${#paths[@]} - 20 )) more"
  fi
  if [ "$SRC_COMMIT" != "$head" ]; then
    echo "    (using $SRC_COMMIT, which is not HEAD)"
  fi
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

# The explicit --source/--ref, resolved to one commit before anything is changed.
resolve_local_source() {
  local refuse="refusing --source $(q "$FLAG_SOURCE"):"
  if ! check_local_path "$FLAG_SOURCE"; then
    case "$LS_REASON" in
      "the path holds a control character") die "refusing --source: $LS_REASON. Nothing was changed." ;;
      *) die "$refuse $LS_REASON. Nothing was changed." ;;
    esac
  fi
  # 7. The ref: --ref, else the manifest's installed_commit when it is a full sha, else HEAD.
  if $REF_GIVEN; then
    SOURCE_REF="$FLAG_REF"
  else
    read_install_record
    if is_sha "$REC_INSTALLED"; then SOURCE_REF="$REC_INSTALLED"; else SOURCE_REF="HEAD"; fi
  fi
  case "$SOURCE_REF" in
    "") die "refusing an empty --ref. Nothing was changed." ;;
    -*) die "refusing --ref $(q "$SOURCE_REF"): it starts with '-'. Nothing was changed." ;;
  esac
  if is_sha "$SOURCE_REF"; then
    has_commit "$SOURCE_REF" || die "$refuse commit $SOURCE_REF is not in $(q "$SOURCE_REAL"). Nothing was changed."
    SRC_COMMIT="$SOURCE_REF"
  else
    SRC_COMMIT=$(git -C "$SOURCE_REAL" rev-parse --verify --quiet "$SOURCE_REF^{commit}" 2>/dev/null </dev/null || true)
    [ -n "$SRC_COMMIT" ] || die "refusing --ref $(q "$SOURCE_REF"): unknown ref in $(q "$SOURCE_REAL"). Nothing was changed."
  fi
  warn_dirty   # 9
  SOURCE_KIND="local"
  UPSTREAM_SHA=""
}

# refuse_recorded <reason>: the one refusal for an unusable recorded source (never a fallback).
refuse_recorded() {
  local where="path not recorded"
  [ -n "$REC_PATH" ] && where="$(q "$REC_PATH")"
  die "this project was installed from a local HACF checkout ($where) at ${REC_INSTALLED:-(no installed_commit)}; $1. Pass --source=<checkout> [--ref=<sha>]. Nothing was changed."
}

# Task 3: a project installed with --source is uninstalled from that recorded source and commit.
# Anything but source.kind == local leaves the remote path exactly as before.
recorded_source() {
  read_install_record
  [ "$REC_KIND" = "local" ] || return 0
  is_sha "$REC_INSTALLED" || refuse_recorded "the manifest has no full installed_commit"
  # 1. The manifest's own record is current.
  if [ "$REC_SRC_COMMIT" != "$REC_INSTALLED" ]; then
    refuse_recorded "the recorded local source is stale (source.commit ${REC_SRC_COMMIT:-(none)} != installed_commit $REC_INSTALLED; a later update changed the install)"
  fi
  # 2. The per-machine file.
  case "$REC_FILE_ERR" in
    missing) refuse_recorded "$SOURCE_FILE_REL is missing" ;;
    invalid) refuse_recorded "$SOURCE_FILE_REL is not a valid JSON object" ;;
  esac
  [ "$REC_SCHEMA" = "$SOURCE_SCHEMA" ] || refuse_recorded "$SOURCE_FILE_REL has schema $(q "$REC_SCHEMA"), not $SOURCE_SCHEMA"
  [ "$REC_FILE_KIND" = "local" ] || refuse_recorded "$SOURCE_FILE_REL has kind $(q "$REC_FILE_KIND"), not local"
  if [ "$REC_FILE_COMMIT" != "$REC_INSTALLED" ]; then
    refuse_recorded "$SOURCE_FILE_REL records commit $(q "$REC_FILE_COMMIT"), not installed_commit $REC_INSTALLED"
  fi
  [ -n "$REC_PATH" ] || refuse_recorded "$SOURCE_FILE_REL records no path"
  # 3. The path, 4. the commit.
  check_local_path "$REC_PATH" || refuse_recorded "the source $LS_REASON"
  has_commit "$REC_INSTALLED" || refuse_recorded "commit $REC_INSTALLED is not in $(q "$SOURCE_REAL")"
  SOURCE_KIND="local"
  SOURCE_RECORDED=true
  SOURCE_REF="$REC_INSTALLED"
  SRC_COMMIT="$REC_INSTALLED"
  UPSTREAM_SHA=""
}

# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------
preflight() {
  command -v git >/dev/null 2>&1 || die "git is required. Nothing was changed."
  command -v python3 >/dev/null 2>&1 || die "python3 is required. Nothing was changed."
  if [ ! -f "$MANIFEST_REL" ] && [ ! -d ".agents" ]; then
    die "no $MANIFEST_REL and no .agents/ here: nothing to uninstall (run this from the project root)."
  fi
  local top
  if top=$(git rev-parse --show-toplevel 2>/dev/null </dev/null) \
      && [ "$(cd "$top" && pwd -P)" != "$(pwd -P)" ]; then
    die "run the uninstaller from the repository root ($top). Nothing was changed."
  fi
}

clone_upstream() {
  SRC="$TMP_DIR/upstream"
  if [ "$SOURCE_KIND" = "local" ]; then
    fetch_local_source
    return 0
  fi
  echo "📦  Fetching the framework: $REPO_URL (branch: $BRANCH)..."
  if ! git clone --quiet --depth=1 --branch "$BRANCH" -- "$REPO_URL" "$SRC" </dev/null; then
    die "could not clone $REPO_URL (branch $BRANCH). Nothing was changed."
  fi
  UPSTREAM_SHA=$(git -C "$SRC" rev-parse HEAD </dev/null)
  echo "    ✅  upstream $(upstream_label)"
  echo ""
}

# lgit <args>: git under an isolated config (no system or global file), autocrlf and fsmonitor
# off, so the temp checkout holds the commit's blobs byte for byte (the installer's slice 31).
lgit() {
  GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null \
    git -c core.autocrlf=false -c core.fsmonitor=false "$@" </dev/null
}

# The commit object into the temp dir: init + fetch by sha, not clone (a clone carries only
# refs/heads, tags and HEAD). The fetch only reads the source.
fetch_local_source() {
  echo "📦  Fetching the framework: local $(q "$SOURCE_REAL") @ $SRC_COMMIT..."
  lgit init -q -- "$SRC" >/dev/null \
    || die "could not create the temp checkout. Nothing was changed."
  lgit -C "$SRC" fetch --quiet --no-tags -- "$SOURCE_REAL" "$SRC_COMMIT" \
    || die "could not fetch $SRC_COMMIT from $(q "$SOURCE_REAL"). Nothing was changed."
  lgit -C "$SRC" checkout --quiet --detach "$SRC_COMMIT" \
    || die "could not check out $SRC_COMMIT in the temp dir. Nothing was changed."
  UPSTREAM_SHA=$(lgit -C "$SRC" rev-parse HEAD)
  if [ "$UPSTREAM_SHA" != "$SRC_COMMIT" ]; then
    die "the temp checkout is at $UPSTREAM_SHA, not $SRC_COMMIT. Nothing was changed."
  fi
  echo "    ✅  upstream $(upstream_label)"
  echo ""
}

plan_files() {
  # Tasks 10-13: the candidate list, from the manifest or the clone's legacy list (never the
  # project's copy, R24.10).
  if [ -f "$MANIFEST_REL" ]; then
    MODE="manifest"
    echo "🧾  Ownership: $MANIFEST_REL (only files whose sha256 still matches are deleted)"
  else
    MODE="legacy"
    echo "🧾  Ownership: no manifest (a v0.5.x install); using $LEGACY_REL from the clone"
    echo "    ($(upstream_label)); only files whose sha256 matches a v0.5.4-v0.5.9 release are deleted"
    [ -f "$SRC/$LEGACY_REL" ] || die "the clone ($(upstream_label)) has no $LEGACY_REL; nothing was changed."
  fi
  ux plan "$MODE" "$SRC/$LEGACY_REL" "$TMP_DIR/candidates" "$TMP_DIR/plan-report" "$TMP_DIR/self" \
    </dev/null
  ux blocks "$SRC" "$TMP_DIR/candidates" "${BLOCK_TARGETS[@]}" "$(attributes_path)|merge-drivers" \
    </dev/null >"$TMP_DIR/blocks"
  # D2: the lore merge-driver git config sections, checked against the clone's DRIVER_CONFIG.
  ux drivers "$SRC" </dev/null >"$TMP_DIR/drivers"
  # K9: legacy pointers and finished-merge sidecars, decided now, before any block is removed.
  ux leftovers "$SRC" "$SRC/$LEGACY_REL" "$TMP_DIR/candidates" "$TMP_DIR/leftovers" </dev/null
  local kind rel why
  while IFS=$'\t' read -r kind rel why; do
    [ "$kind" = "keep" ] && report "kept $rel ($why)"
  done <"$TMP_DIR/leftovers"
  while IFS=$'\t' read -r kind rel; do
    case "$kind" in
      modified) report "kept (changed since HACF installed it): $rel" ;;
      refused)  report "refused (NEVER_COPY or a user root file; never deleted): $rel" ;;
      notfile)  report "kept (not a regular file): $rel" ;;
    esac
  done <"$TMP_DIR/plan-report"
  echo ""
}

attributes_path() {
  local common
  if common=$(git rev-parse --git-common-dir 2>/dev/null </dev/null); then
    echo "$common/info/attributes"
  else
    echo ".git/info/attributes"
  fi
}

list_plan() {
  echo "📋  Plan (upstream $(upstream_label)):"
  local rel path id sha any=false
  while IFS= read -r -d '' rel; do
    echo "    ✗  $rel"
    any=true
  done <"$TMP_DIR/candidates"
  while IFS=$'\t' read -r rel sha; do
    echo "    ✗  $rel (deleted last)"
    any=true
  done <"$TMP_DIR/self"
  while IFS=$'\t' read -r path id sha; do
    echo "    ✂  HACF block '$id' in $path"
    any=true
  done <"$TMP_DIR/blocks"
  local kind section why
  while IFS=$'\t' read -r kind section; do
    [ "$kind" = "remove" ] || continue
    echo "    ✂  git config section $section"
    any=true
  done <"$TMP_DIR/drivers"
  while IFS=$'\t' read -r kind rel why; do
    if [ "$kind" = "remove" ]; then
      echo "    ✗  $rel ($why)"
      any=true
    else
      echo "    ⏸  $rel kept ($why)"
    fi
  done <"$TMP_DIR/leftovers"
  $any || echo "    (no file or block to remove)"
  echo ""
}

confirm() {
  if $AUTO_YES; then
    return 0
  fi
  if ! tty_available; then
    die "no terminal to ask on: re-run with -y to proceed, or --dry-run to see the plan. Nothing was changed."
  fi
  local answer
  answer=$(tty_ask "    Uninstall HACF as listed above? (upstream $(upstream_label)) [y/N] ")
  if [[ "$answer" != "y" && "$answer" != "Y" ]]; then
    echo "Aborted. Nothing was changed."
    exit 0
  fi
  echo ""
}

offer_code_graph_delete() {
  # Task 14 (R24.9): before any framework file is removed, so the project's own script still runs.
  local project
  project=$(ux codegraph </dev/null)
  [ -n "$project" ] || return 0
  echo "🧠  Code graph:"
  local msg="Delete the codebase-memory-mcp index of project '$project' (python3 .agents/scripts/hyper_codegraph.py delete)"
  if $DRY_RUN; then
    echo "    [DRY-RUN] would offer: $msg"
  elif prompt_consent "$DELETE_CODE_GRAPH" "$msg"; then
    if python3 .agents/scripts/hyper_codegraph.py delete </dev/null; then
      report "code graph: index of '$project' deleted."
    else
      report "code graph: hyper_codegraph.py delete failed; delete the index under ~/.cache/codebase-memory-mcp by hand."
    fi
  else
    DECLINED+=("deleting the code-graph index of '$project' (~/.cache/codebase-memory-mcp)")
  fi
  echo ""
}

remove_blocks() {
  # Tasks 15-16: through the clone's hyper_blocks.py, so the R26 grammar is not re-implemented.
  [ -s "$TMP_DIR/blocks" ] || return 0
  echo "✂️   HACF managed blocks (every byte outside the block is kept):"
  local path id sha rc out yes=() sha_args=()
  if $AUTO_YES || ! tty_available; then yes=(--yes); fi
  while IFS=$'\t' read -r path id sha; do
    if $DRY_RUN; then
      if [ "$id" = "merge-drivers" ]; then
        echo "    [DRY-RUN] would remove block '$id' from $path (the file is deleted if nothing else is left in it)"
      else
        echo "    [DRY-RUN] would remove block '$id' from $path"
      fi
      continue
    fi
    sha_args=()
    [ -n "$sha" ] && sha_args=(--manifest-sha "$sha")
    rc=0
    out=$(python3 "$SRC/.agents/scripts/hyper_blocks.py" remove "$path" --id "$id" \
            "${sha_args[@]+"${sha_args[@]}"}" "${yes[@]+"${yes[@]}"}" </dev/null 2>&1) || rc=$?
    echo "$out" | sed 's/^/    /'
    if [ "$rc" -ne 0 ]; then
      fail "$path: block '$id' left unchanged (hyper_blocks exit $rc: malformed markers or a concurrent change)."
    elif grep -q '^left:' <<<"$out"; then
      report "$path: your edited HACF block '$id' was kept."
    elif [ "$id" = "merge-drivers" ] && [ -f "$path" ] && [ ! -s "$path" ]; then
      # D2: this run's block removal left info/attributes empty, so it held only HACF's block.
      rm -f -- "$path"
      report "$path: deleted (it held only the HACF block)."
    elif [ -f "$path" ] && [ ! -s "$path" ]; then
      report "$path: now empty (it held only the HACF block); delete it if you do not need it."
    fi
  done <"$TMP_DIR/blocks"
  echo ""
}

remove_merge_drivers() {
  # D2: a section is removed only when it still equals the clone's DRIVER_CONFIG; a changed one
  # is kept and reported, like a changed file.
  [ -s "$TMP_DIR/drivers" ] || return 0
  echo "🔀  Lore merge drivers (repo-local git config):"
  local kind section
  while IFS=$'\t' read -r kind section; do
    case "$kind" in
      remove)
        if $DRY_RUN; then
          echo "    [DRY-RUN] would remove git config section $section"
        elif git config --local --remove-section "$section" </dev/null; then
          echo "    ✗  git config $section"
          report "git config: removed $section (HACF's lore merge driver)."
        else
          fail "$section: git config --remove-section failed."
        fi
        ;;
      kept) report "kept (changed since HACF installed it): git config $section" ;;
    esac
  done <"$TMP_DIR/drivers"
  echo ""
}

edit_settings_and_mcp() {
  # Tasks 17-19: only what the manifest records.
  [ "$MODE" = "manifest" ] || return 0
  local dry rc
  dry=$($DRY_RUN && echo 1 || echo 0)
  echo "🔧  Settings, MCP entries and git hooks HACF recorded:"
  rc=0
  ux settings "$dry" ".agents/.backup/$BACKUP_TS" </dev/null || rc=$?
  [ "$rc" -eq 0 ] || fail ".claude/settings.json: invalid JSON; nothing removed from it."
  rc=0
  ux mcp "$dry" </dev/null || rc=$?
  [ "$rc" -eq 0 ] || fail "MCP: a file with invalid JSON was left unchanged (see above)."
  ux githooks "$dry" </dev/null
  echo ""
}

delete_files() {
  # Task 20: the candidates, then the directories that became empty.
  echo "🗑️   Framework files:"
  local rel deleted=0
  : >"$TMP_DIR/deleted"
  while IFS= read -r -d '' rel; do
    if $DRY_RUN; then
      echo "    [DRY-RUN] would delete $rel"
      continue
    fi
    if rm -f -- "$rel"; then
      printf '%s\0' "$rel" >>"$TMP_DIR/deleted"
      deleted=$((deleted + 1))
    else
      fail "could not delete $rel."
    fi
  done <"$TMP_DIR/candidates"
  $DRY_RUN || echo "    Deleted $deleted file(s)."
  # D1: HACF's __pycache__/ next to the deleted (or, dry run, the candidate) .py files.
  local dry line list="$TMP_DIR/deleted"
  dry=$($DRY_RUN && echo 1 || echo 0)
  $DRY_RUN && list="$TMP_DIR/candidates"
  while IFS= read -r line; do
    case "$line" in
      kept$'\t'*) report "kept (not only HACF bytecode): ${line#kept$'\t'}/" ;;
      *) echo "$line" ;;
    esac
  done < <(ux bytecode "$dry" "$list" </dev/null)
  $DRY_RUN || ux prune "$TMP_DIR/deleted" </dev/null
  echo ""
}

remove_leftovers() {
  # K9: the rows the plan decided (ux leftovers); nothing is decided or re-decided here.
  [ -s "$TMP_DIR/leftovers" ] || return 0
  local kind rel why any=false
  : >"$TMP_DIR/deleted-leftovers"
  while IFS=$'\t' read -r kind rel why; do
    [ "$kind" = "remove" ] || continue
    if ! $any; then echo "🧹  HACF leftovers (proved to be HACF's):"; any=true; fi
    if $DRY_RUN; then
      echo "    [DRY-RUN] would remove $rel ($why)"
    elif rm -f -- "$rel"; then
      printf '%s\0' "$rel" >>"$TMP_DIR/deleted-leftovers"
      echo "    ✗  $rel"
      report "removed $rel ($why)"
    else
      fail "could not delete $rel."
    fi
  done <"$TMP_DIR/leftovers"
  $DRY_RUN || ux prune "$TMP_DIR/deleted-leftovers" </dev/null
  ! $any || echo ""
}

# report_rig_specs: one report line per manifest rig_spec entry (R4). A copy made by
# HACF-install.sh --rig is the user's: it is not in `files`, and it is never deleted.
report_rig_specs() {
  [ -f "$MANIFEST_REL" ] || return 0
  local kind rel sha there
  while IFS=$'\t' read -r kind rel sha there; do
    case "$kind" in
      inside)
        if [ "$there" = "1" ]; then
          report "rig spec copy at $rel (from $sha) left in place; it is yours"
        else
          report "rig spec copy recorded at $rel (from $sha) but no longer there"
        fi ;;
      outside) report "a rig spec was copied outside this project at $sha; it is yours" ;;
    esac
  done < <(python3 -c '
import json, os, sys
try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        data = json.load(handle)
except (OSError, ValueError):
    data = {}
entries = data.get("rig_spec") if isinstance(data, dict) else None
for entry in entries if isinstance(entries, list) else []:
    if not isinstance(entry, dict) or not isinstance(entry.get("commit"), str):
        continue
    sha = "".join(c for c in entry["commit"] if c.isalnum())
    rel = entry.get("path")
    if entry.get("outside_project") is True:
        print(f"outside\t-\t{sha}\t-")
    elif isinstance(rel, str) and rel.isprintable() and "\t" not in rel:
        print(f"inside\t{rel}\t{sha}\t{int(os.path.isdir(rel))}")
' "$MANIFEST_REL" </dev/null 2>/dev/null || true)
}

remove_saved_installer() {
  # C2 (OPR.99.0.10.3): the installer's saved copy and its empty directories, before the manifest.
  [ "$MODE" = "manifest" ] || return 0
  [ -e "$SAVED_INSTALLER_REL" ] || [ -L "$SAVED_INSTALLER_REL" ] || return 0
  if $DRY_RUN; then
    echo "    [DRY-RUN] would delete $SAVED_INSTALLER_REL"
  elif [ "$FAILURES" -eq 0 ]; then
    rm -f -- "$SAVED_INSTALLER_REL"
    rmdir .agents/state/hacf/installer 2>/dev/null && rmdir .agents/state/hacf 2>/dev/null || true
    echo "    ✗  $SAVED_INSTALLER_REL"
  else
    report "$SAVED_INSTALLER_REL kept: $FAILURES step(s) failed; fix them and re-run the uninstaller."
  fi
}

remove_source_record() {
  # KI-3 (OPR.99.0.11.3): the per-machine install record (SOURCE_FILE_REL). It is the one named
  # path carved out of the never-delete .agents/state/, and it goes only when nothing failed,
  # because a re-run after a failed uninstall reads it to find its source (recorded_source).
  [ "$MODE" = "manifest" ] || return 0
  [ -e "$SOURCE_FILE_REL" ] || [ -L "$SOURCE_FILE_REL" ] || return 0
  if [ ! -f "$SOURCE_FILE_REL" ] || [ -L "$SOURCE_FILE_REL" ]; then
    report "$SOURCE_FILE_REL kept: it is not a regular file."
  elif $DRY_RUN; then
    echo "    [DRY-RUN] would remove $SOURCE_FILE_REL"
  elif [ "$FAILURES" -eq 0 ]; then
    rm -f -- "$SOURCE_FILE_REL"
    echo "    removed $SOURCE_FILE_REL"
  else
    report "kept $SOURCE_FILE_REL (uninstall had failures; re-run to finish)"
  fi
}

remove_manifest() {
  [ "$MODE" = "manifest" ] || return 0
  if $DRY_RUN; then
    echo "    [DRY-RUN] would delete $MANIFEST_REL last"
  elif [ "$FAILURES" -eq 0 ]; then
    rm -f -- "$MANIFEST_REL"
    rmdir .agents 2>/dev/null || true
    echo "    ✗  $MANIFEST_REL"
  else
    report "$MANIFEST_REL kept: $FAILURES step(s) failed; fix them and re-run the uninstaller."
  fi
}

remove_self() {
  # R64: the project's own HACF-uninstall.sh, after the manifest. Safe when it is the running
  # script: bash keeps the open file, and only print_report and the banner run after this.
  [ "$MODE" = "manifest" ] && [ -s "$TMP_DIR/self" ] || return 0
  local rel sha
  IFS=$'\t' read -r rel sha <"$TMP_DIR/self"
  if $DRY_RUN; then
    echo "    [DRY-RUN] would delete $rel last"
  elif [ "$FAILURES" -gt 0 ]; then
    report "$rel kept: $FAILURES step(s) failed; fix them and re-run it."
  elif [ -f "$rel" ] && [ ! -L "$rel" ] && [ "$(ux sha "$rel" </dev/null)" = "$sha" ]; then
    rm -f -- "$rel"
    echo "    ✗  $rel"
  else
    report "$rel kept: it changed during the uninstall."
  fi
}

print_report() {
  local line kept=() dir
  for dir in .agents/local .agents/state .agents/memory .agents/.backup .agents/logs; do
    [ -e "$dir" ] && kept+=("$dir/")
  done
  [ ${#kept[@]} -eq 0 ] || report "kept by design (your selections, machine state, memory and backups): ${kept[*]}"
  echo ""
  echo "📝  Uninstall report (upstream $(upstream_label)):"
  for line in "${DECLINED[@]+"${DECLINED[@]}"}"; do echo "    declined: $line"; done
  for line in "${REPORT[@]+"${REPORT[@]}"}"; do echo "    - $line"; done
  [ ${#REPORT[@]} -gt 0 ] || [ ${#DECLINED[@]} -gt 0 ] || echo "    nothing kept, refused or declined."
  echo ""
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
  parse_args "$@"
  resolve_upstream

  echo ""
  echo "╔══════════════════════════════════════════════════════════╗"
  echo "║    Hypergraph Agentic Coding Framework Uninstaller       ║"
  echo "╚══════════════════════════════════════════════════════════╝"
  echo ""
  if $SOURCE_RECORDED; then
    echo "    upstream: local $(q "$SOURCE_REAL") @ $SRC_COMMIT (recorded by the install)"
  elif [ "$SOURCE_KIND" = "local" ]; then
    echo "    upstream: $(upstream_label) (--source, ref $(q "$SOURCE_REF"))"
  else
    echo "    upstream: $REPO_URL (branch $BRANCH)"
  fi
  echo "    Never deleted: SPEC.md, project.yaml, workspace.yaml, lore/, missions/, spec/, tests/,"
  echo "    your root files and any file whose sha256 HACF cannot match."
  echo ""

  preflight
  TMP_DIR="$(mktemp -d)"
  trap cleanup EXIT
  clone_upstream                 # Task 8
  BACKUP_TS="$(date -u +%Y%m%dT%H%M%SZ)"
  plan_files                     # Tasks 10-13
  list_plan                      # Task 21
  $DRY_RUN || confirm

  offer_code_graph_delete        # Task 14
  remove_blocks                  # Tasks 15-16
  remove_merge_drivers           # D2 (R67)
  edit_settings_and_mcp          # Tasks 17-19
  delete_files                   # Task 20
  remove_leftovers               # K9: pointers by sha, finished-merge sidecars
  report_rig_specs               # R4: a --rig copy is reported, never deleted
  remove_saved_installer
  remove_source_record           # KI-3: after the saved installer, only when nothing failed
  remove_manifest
  remove_self                    # R64: the project's own copy, last

  print_report                   # Task 21
  if $DRY_RUN; then
    echo "╔══════════════════════════════════════════════════════════╗"
    echo "║  ✅  Dry-run complete — nothing was changed.             ║"
    echo "╚══════════════════════════════════════════════════════════╝"
  else
    echo "╔══════════════════════════════════════════════════════════╗"
    echo "║  ✅  Uninstall complete. Re-run HACF-install.sh to       ║"
    echo "║      restore the framework.                              ║"
    echo "╚══════════════════════════════════════════════════════════╝"
  fi
  echo ""
}

# Everything above is definitions; under `curl | bash` bash has read the whole script before
# main runs, so no child process can consume the rest of it from stdin.
main "$@"
