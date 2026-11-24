"""The product name guard (OPR.0.6.3.5): only allowlisted old-name hits ship.

No shipped file holds the old product name, the old repository path or the old method name (the
"Coding" release before 4.1), except the historical and deliberate hits allowlisted below with exact
counts. The shipped files are `git ls-files` (untracked new files included) minus the paths of
dev/release_export.yml's `versions: all` exclude rows. The old strings are built from fragments,
so this file never matches itself.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT

OLD_NAME = re.compile("Hypergraph[ -]" + "Coding[ -]Agent[ -]" + "Framework")
OLD_PATH = "/Hypergraph-" + "Coding-Agent-" + "Framework"
CODING_40 = "Coding " + "4.0"

# {(path, pattern): (count, reason)}; counts are exact.
ALLOWLIST = {
    ("CHANGELOG.md", "old-name"): (1, "historical: the initial-commit entry of an old release"),
    ("CHANGELOG.md", "old-path"): (1, "the 0.6.3 Known issues line that discloses the move (F18)"),
    ("CHANGELOG.md", "coding-4.0"): (1, "TJ's 0.6.3 bullet naming the old method"),
    ("PRDs/v0.5.0/SuperPRD_ Dynamic Workflow Architecture.md", "old-name"): (
        1,
        "historical: a v0.5.0 PRD",
    ),
    ("README.md", "coding-4.0"): (
        4,
        "TJ's section on the old method and 4.1, and his workflow line",
    ),
}


def _tool():
    if "release_export" not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            "release_export", REPO_ROOT / "dev" / "release_export.py"
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules["release_export"] = module
        spec.loader.exec_module(module)
    return sys.modules["release_export"]


def counts(text: str) -> dict[str, int]:
    """The old-name, old-path and old-method counts of one text (a path is not also a name)."""
    path = text.count(OLD_PATH)
    return {
        "old-name": len(OLD_NAME.findall(text)) - path,
        "old-path": path,
        "coding-4.0": text.count(CODING_40),
    }


def shipped_files(root: Path) -> list[str]:
    """Every tracked or new file of `root` outside the `versions: all` exclude rows."""
    rx = _tool()
    rows = [r for r in rx.load_profile(root / "dev" / "release_export.yml").exclude]
    rows = [r for r in rows if r.versions == "all"]
    out = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        capture_output=True,
        check=True,
    ).stdout.decode("utf-8", "surrogateescape")
    return sorted(
        rel
        for rel in set(filter(None, out.split("\0")))
        if not any(rx.excluded(rel, r) for r in rows)
    )


def violations(root: Path, files: list[str], allow: dict) -> list[str]:
    """Each unlisted hit or count that differs from its allowlist row, named by path and pattern."""
    found = {}
    for rel in files:
        path = root / rel
        if path.is_symlink() or not path.is_file():
            continue
        data = path.read_bytes()
        if b"\0" in data[:8000]:
            continue
        for pattern, n in counts(data.decode("utf-8", "replace")).items():
            if n:
                found[(rel, pattern)] = n
    bad = []
    for key in sorted(set(found) | set(allow)):
        want = allow.get(key, (0, ""))[0]
        if found.get(key, 0) != want:
            bad.append(f"{key[0]}: {key[1]} x{found.get(key, 0)} (allowlisted x{want})")
    return bad


def test_no_shipped_file_holds_the_old_name() -> None:
    """The shipped tree holds only the allowlisted old-name, old-path and old-method hits."""
    assert violations(REPO_ROOT, shipped_files(REPO_ROOT), ALLOWLIST) == []
    for count, reason in ALLOWLIST.values():
        assert count > 0 and reason


@pytest.mark.parametrize(
    ("text", "allow", "expected"),
    [
        ("see " + "Hypergraph Coding " + "Agent Framework" + "\n", {}, ["x.md: old-name x1"]),
        ("github.com/o" + OLD_PATH + "\n", {}, ["x.md: old-path x1"]),
        (
            CODING_40 + " and " + CODING_40,
            {("x.md", "coding-4.0"): (1, "r")},
            ["x.md: coding-4.0 x2"],
        ),
        (CODING_40, {("x.md", "coding-4.0"): (1, "r")}, []),
        ("clean\n", {("x.md", "old-name"): (1, "r")}, ["x.md: old-name x0"]),
    ],
)
def test_guard_self_test(tmp_path: Path, text: str, allow: dict, expected: list[str]) -> None:
    """A planted hit fails; an allowlisted one at the wrong count (or absent) fails, named."""
    (tmp_path / "x.md").write_text(text, encoding="utf-8")
    got = violations(tmp_path, ["x.md"], allow)
    assert [g.split(" (allowlisted")[0] for g in got] == expected
