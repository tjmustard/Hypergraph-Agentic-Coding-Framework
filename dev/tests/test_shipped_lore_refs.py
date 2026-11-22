"""No shipped file cites HACF's own ADR/STORY ids or dev/design paths (slice 33, OPR.0.6.0.33).

TJ (mission SPEC R59, F6): shipped files "must not cite bare HACF ADR/STORY ids or dev/design
paths". A user's own lore ids would collide with HACF's, and dev/design is never shipped. This test
walks the shipped set (git-tracked plus untracked non-ignored files) and fails on any match.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

PATTERN = re.compile(r"\b(ADR|STORY)-[0-9]{4}\b|dev/design")

# The shipped roots: the directories and files HACF-install.sh copies into a project.
SHIPPED_ROOTS = (
    ".agents/scripts",
    ".agents/schemas",
    ".agents/config",
    ".agents/rules",
    ".agents/install-templates",
    ".agents/skills",
    ".claude/commands",
    ".windsurf",
    ".cursor",
    ".clinerules",
    ".roo",
    ".agentignore",
    "HACF-uninstall.sh",
)

# (file, substring of the line). hyper-grill-docs illustrates a *user's* lore tree with two
# example ADR file names; they are a user's lore example, not HACF's own ids.
ALLOWED = {
    (".agents/skills/hyper-grill-docs/SKILL.md", "ADR-0001-event-sourced-orders.md"),
    (".agents/skills/hyper-grill-docs/SKILL.md", "ADR-0002-postgres-for-write-model.md"),
}


def allowed(rel: str, line: str) -> bool:
    """Return True when the (file, line) pair is on the ALLOWED list."""
    return any(rel == path and part in line for path, part in ALLOWED)


def scan(root: Path, rels: list[str]) -> list[str]:
    """Return "path:line: text" for every PATTERN hit in rels under root, beyond ALLOWED."""
    hits = []
    for rel in rels:
        path = root / rel
        if not path.is_file() or path.is_symlink():
            continue
        text = path.read_bytes().decode("utf-8", "replace")
        for number, line in enumerate(text.splitlines(), 1):
            if PATTERN.search(line) and not allowed(rel, line):
                hits.append(f"{rel}:{number}: {line.strip()}")
    return hits


def shipped_files() -> list[str]:
    """Return the tracked and untracked non-ignored files under the shipped roots."""
    out = subprocess.run(
        [
            "git",
            "ls-files",
            "-z",
            "--cached",
            "--others",
            "--exclude-standard",
            "--",
            *SHIPPED_ROOTS,
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        check=True,
        timeout=60,
    ).stdout
    return sorted({rel for rel in out.decode("utf-8").split("\0") if rel})


def test_shipped_set_is_not_empty() -> None:
    """The walk sees the shipped set, so an empty result cannot pass the scan by accident."""
    files = shipped_files()
    assert ".agents/install-templates/AGENTS.md" in files
    assert "HACF-uninstall.sh" in files
    assert any(rel.startswith(".agents/scripts/") for rel in files)


def test_no_hacf_lore_refs_in_shipped_files() -> None:
    """No shipped file matches the pattern, except the two allowlisted grill-docs lines."""
    assert scan(REPO_ROOT, shipped_files()) == []


def test_allowlist_is_still_needed() -> None:
    """Each ALLOWED pair still matches a line, so a stale exception does not linger."""
    for rel, part in ALLOWED:
        lines = (REPO_ROOT / rel).read_text(encoding="utf-8").splitlines()
        assert any(part in line and PATTERN.search(line) for line in lines), (rel, part)
