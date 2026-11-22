#!/usr/bin/env python3
"""Generate .agents/config/legacy_file_hashes.json (S12c; process tooling, never shipped).

The v0.6.0 uninstaller (HACF-uninstall.sh) deletes a file from a v0.5.x project that has no
.agents/hacf-manifest.json only when the file's sha256 equals a file HACF shipped at a v0.5.4 -
v0.5.9 release (US-019 AC2). This script computes that list from the six release commits in this
repository's history. The uninstaller reads it from its own fresh clone, never from the project
(R24.10); hyper_blocks.py reads the entries for AGENTS.md, CLAUDE.md and GEMINI.md (S12a).

Usage:
    python3 dev/scripts/build_legacy_hashes.py            write the JSON file
    python3 dev/scripts/build_legacy_hashes.py --check    exit 1 with a diff summary when the
                                                          committed file differs, else 0
    [--repo DIR] [--output PATH]                          defaults: this checkout, the file above

Output shape (MiniPRD_21 clarifying question 3). hyper_blocks.known_template_shas() looks up the
TOP-LEVEL keys `<name>` and `.agents/install-templates/<name>` (value: a sha or a list of shas),
so the file keys every shipped file by its destination path at the top level, next to two
metadata keys that are never paths:
    {"schema": "hacf.legacy-hashes/v1",
     "releases": ["626a28e", ...],
     "<destination path>": ["<sha256>", ...], ...}
Keys and sha lists are sorted, the indent is two spaces and the file ends with a newline, so a
regeneration is byte-identical.

Which files count as shipped (clarifying question 4): the paths that release's own
HACF-install.sh copied into a project (COPY_SETS below, transcribed from each release's tables),
with a file copied from another source hashed under its destination path, minus every NEVER_COPY
path (dev/design/v0.6.0/schemas.md#target-layout), including the contents of spec/ and tests/.
Git hooks went into .git/hooks, which is not a project path, so they are not listed. Every git
call is an argv list; blobs are hashed with hashlib.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / ".agents" / "scripts"))
import _runtime  # noqa: E402

_runtime.require_runtime()  # R19: before anything else runs

SCHEMA = "hacf.legacy-hashes/v1"
OUTPUT_REL = ".agents/config/legacy_file_hashes.json"
METADATA_KEYS = ("schema", "releases")

# The release set, both ends included. `git rev-list 626a28e..984ca46` leaves 626a28e out, so the
# set is named here rather than derived from that range.
RELEASES = ("626a28e", "cd8ae6a", "b3b1634", "329e8f7", "0b19f16", "984ca46")

# Instruction files: destination name -> source path in the release (FILE_SOURCE_OVERRIDE, the
# same table in all six releases).
FILE_SOURCE_OVERRIDE = {
    "CLAUDE.md": ".agents/install-templates/CLAUDE.md",
    "AGENTS.md": ".agents/install-templates/AGENTS.md",
    "GEMINI.md": ".agents/install-templates/GEMINI.md",
}
# IDE_DEFS directories and files (the same table in all six releases):
# claude .claude + CLAUDE.md; antigravity GEMINI.md; windsurf .windsurf; cursor .cursor;
# cline .clinerules; roo .roo; universal AGENTS.md.
IDE_DIRS = (".claude", ".windsurf", ".cursor", ".clinerules", ".roo")
IDE_FILES = ("CLAUDE.md", "GEMINI.md", "AGENTS.md")
CORE_FILES = (".agentignore",)

# v0.5.4 (626a28e) and v0.5.5 (cd8ae6a): HACF-install.sh copies CORE_DIRS=(".agents" "tests")
# whole, CORE_FILES=(".agentignore"), the IDE dirs and the IDE files.
_COPY_V054 = {
    "dirs": (".agents", "tests", *IDE_DIRS),
    "files": (*CORE_FILES, *IDE_FILES),
}
# v0.5.6 (b3b1634), v0.5.7 (329e8f7), v0.5.8 (0b19f16) and v0.5.9 (984ca46): HACF-install.sh
# copies AGENT_SYSTEM_SUBDIRS=("scripts" "schemas" "config" "rules" "install-templates") under
# .agents/, .agents/skills, SKILL_BRIDGE_DIRS=(".claude/commands" ".windsurf/workflows"),
# CORE_DIRS=("tests"), CORE_FILES=(".agentignore"), the IDE dirs and the IDE files. The
# --files= targets (v0.5.7+) are all under .agents/ or .git/hooks.
_COPY_V056 = {
    "dirs": (
        ".agents/scripts",
        ".agents/schemas",
        ".agents/config",
        ".agents/rules",
        ".agents/install-templates",
        ".agents/skills",
        ".claude/commands",
        ".windsurf/workflows",
        "tests",
        *IDE_DIRS,
    ),
    "files": (*CORE_FILES, *IDE_FILES),
}
COPY_SETS = {
    "626a28e": _COPY_V054,
    "cd8ae6a": _COPY_V054,
    "b3b1634": _COPY_V056,
    "329e8f7": _COPY_V056,
    "0b19f16": _COPY_V056,
    "984ca46": _COPY_V056,
}

# NEVER_COPY, dev/design/v0.6.0/schemas.md#target-layout (the same list as HACF-install.sh). A
# trailing / means the directory and everything under it.
NEVER_COPY = (
    "SPEC.md",
    "project.yaml",
    "workspace.yaml",
    "lore_hypergraph.yml",
    ".gitattributes",
    "lore/",
    "missions/",
    "exhaust/",
    ".openrig/",
    "spec/",
    "tests/",
    "dev/",
    "PRDs/",
    "docs/",
    ".agents/local/",
    ".agents/state/",
    ".agents/.backup/",
    ".agents/logs/",
    ".agents/memory/",
    ".claude/settings*.json",
    ".mcp.json",
    ".cursor/mcp.json",
    ".roo/mcp.json",
    ".gitignore",
    "CHANGELOG.md",
    "README.md",
    "CITATION.cff",
    "pyproject.toml",
    "uv.lock",
    "requirements.txt",
    ".agent",
)
# Root files on an explicit v0.5.x copy list; any other root file is NEVER_COPY.
ROOT_COPY = frozenset((*CORE_FILES, *IDE_FILES))


class GenerateError(Exception):
    """A git call failed or a release is missing from this checkout."""


def never_copy(rel: str) -> bool:
    """Return True when rel is a NEVER_COPY path (or a root file on no copy list)."""
    for pattern in NEVER_COPY:
        if pattern.endswith("/"):
            if rel == pattern[:-1] or rel.startswith(pattern):
                return True
        elif any(c in pattern for c in "*?["):
            if fnmatch.fnmatchcase(rel, pattern):
                return True
        elif rel == pattern:
            return True
    return "/" not in rel and rel not in ROOT_COPY


def _git(repo: Path, args: list[str]) -> bytes:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, timeout=120, check=False
    )
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "replace").strip()
        raise GenerateError(f"git {' '.join(args)} failed: {detail}")
    return proc.stdout


def _tree(repo: Path, rev: str) -> dict[str, str]:
    """Return {path: blob id} for the regular files of rev (symlinks and submodules skipped)."""
    blobs: dict[str, str] = {}
    for entry in _git(repo, ["ls-tree", "-r", "-z", rev]).decode("utf-8").split("\0"):
        if not entry:
            continue
        meta, _, path = entry.partition("\t")
        mode, kind, oid = meta.split()
        if kind == "blob" and mode in ("100644", "100755"):
            blobs[path] = oid
    return blobs


def copy_set(rev: str, tree: dict[str, str]) -> dict[str, str]:
    """Return {destination path: source path} of what rev's HACF-install.sh copied."""
    spec = COPY_SETS[rev]
    copied: dict[str, str] = {}
    for top in spec["dirs"]:
        prefix = top + "/"
        for path in tree:
            if path.startswith(prefix):
                copied[path] = path
    for dest in spec["files"]:
        source = FILE_SOURCE_OVERRIDE.get(dest, dest)
        if source in tree:
            copied[dest] = source
    return {dest: src for dest, src in copied.items() if not never_copy(dest)}


def build(repo: Path) -> dict:
    """Return the legacy hash document for the release set."""
    files: dict[str, set[str]] = {}
    by_oid: dict[str, str] = {}
    for rev in RELEASES:
        try:
            _git(repo, ["rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}"])
        except GenerateError:
            raise GenerateError(
                f"release commit {rev} is not in {repo}; run this in a full (not shallow) clone"
            ) from None
        tree = _tree(repo, rev)
        for dest, source in copy_set(rev, tree).items():
            oid = tree[source]
            if oid not in by_oid:
                blob = _git(repo, ["show", f"{rev}:{source}"])
                by_oid[oid] = hashlib.sha256(blob).hexdigest()
            files.setdefault(dest, set()).add(by_oid[oid])
    document: dict = {"schema": SCHEMA, "releases": list(RELEASES)}
    for dest, shas in files.items():
        if dest in METADATA_KEYS:
            raise GenerateError(f"a shipped path collides with a metadata key: {dest}")
        document[dest] = sorted(shas)
    return document


def render(document: dict) -> bytes:
    """Return the canonical bytes: sorted keys, two-space indent, final newline."""
    return (json.dumps(document, indent=2, sort_keys=True) + "\n").encode("utf-8")


def diff_summary(old: dict, new: dict) -> list[str]:
    """Return one line per key that was added, removed or changed between old and new."""
    lines = []
    for key in sorted(set(old) | set(new)):
        if key not in old:
            lines.append(f"  added:   {key}")
        elif key not in new:
            lines.append(f"  removed: {key}")
        elif old[key] != new[key]:
            lines.append(f"  changed: {key}")
    return lines


def main(argv: list[str] | None = None) -> int:
    """Write or --check the legacy hash file; return the exit code."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="compare, write nothing")
    parser.add_argument("--repo", default=str(REPO_ROOT), help="the HACF checkout to read")
    parser.add_argument("--output", help=f"the JSON file (default <repo>/{OUTPUT_REL})")
    ns = parser.parse_args(argv)
    repo = Path(ns.repo).resolve()
    output = Path(ns.output) if ns.output else repo / OUTPUT_REL
    try:
        document = build(repo)
    except GenerateError as exc:
        print(f"build_legacy_hashes.py: {exc}", file=sys.stderr)
        return 2
    data = render(document)
    if ns.check:
        try:
            current = output.read_bytes()
        except FileNotFoundError:
            print(f"build_legacy_hashes.py --check: {output} does not exist", file=sys.stderr)
            return 1
        if current == data:
            print(f"ok: {output} matches the {len(RELEASES)} release commits")
            return 0
        try:
            old = json.loads(current.decode("utf-8"))
        except ValueError:
            old = {}
        print(f"build_legacy_hashes.py --check: {output} differs from a regeneration:")
        for line in diff_summary(old if isinstance(old, dict) else {}, document) or [
            "  (same entries; formatting differs)"
        ]:
            print(line)
        return 1
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(data)
    print(f"wrote {output}: {len(document) - len(METADATA_KEYS)} path(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
