"""No document promises a version for an item v0.6.6 or v0.6.7 deferred (OPR.0.6.6.1, OPR.0.6.7.1).

The README and the CHANGELOG's `[Unreleased]`, `[0.6.7]` and `[0.6.6]` sections may name a deferred
item but never pair one with a `v0.6.` or `v0.7.` version as a target.
A "line" here is one bullet (a bullet and its wrapped continuation lines). Released sections
(`[0.6.5]` and older) are history and are not scanned. Stdlib only; reads files, writes nothing.
"""

from __future__ import annotations

import re

from conftest import REPO_ROOT

README = REPO_ROOT / "README.md"
CHANGELOG = REPO_ROOT / "CHANGELOG.md"

DEFERRED = (
    re.compile(r"\bKI-[1245]\b"),
    re.compile(r"\bK11\b"),
    re.compile(r"symbol_impact --include-tests"),
    re.compile(r"`\.mcp\.json` `env` block"),
    re.compile(r"shared uv caches", re.IGNORECASE),
    re.compile(r"community harness", re.IGNORECASE),
    re.compile(r"harness: agy"),
    re.compile(r"no `PROGRESS\.md`"),  # the progress report's "tracked" rule (OPR.99.0.15.7)
)
TARGET = re.compile(r"\bv0\.[67]\.\d+")


def _bullets(text: str) -> list[str]:
    """Split text into bullets: a line starting with `- ` opens one, a blank line closes it."""
    out: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        if line.startswith("- ") or not line.strip():
            if current:
                out.append(" ".join(current))
            current = [line.strip()] if line.strip() else []
        elif current:
            current.append(line.strip())
        else:
            current = [line.strip()]
    if current:
        out.append(" ".join(current))
    return out


def _changelog_sections() -> str:
    """The `[Unreleased]`, `[0.6.7]` and `[0.6.6]` sections of the CHANGELOG."""
    text = CHANGELOG.read_text(encoding="utf-8")
    start = text.index("## [Unreleased]")
    end = text.index("## [0.6.5]")
    section = text[start:end]
    assert "## [0.6.7]" in section and "## [0.6.6]" in section
    return section


def _offenders(text: str) -> list[str]:
    """The bullets that pair a deferred item with a version target."""
    return [
        bullet[:120]
        for bullet in _bullets(text)
        if any(p.search(bullet) for p in DEFERRED) and TARGET.search(bullet)
    ]


def test_readme_pairs_no_deferred_item_with_a_version() -> None:
    """README.md promises no version for a deferred item."""
    assert _offenders(README.read_text(encoding="utf-8")) == []


def test_changelog_unreleased_to_0_6_6_pair_no_deferred_item_with_a_version() -> None:
    """The CHANGELOG sections since v0.6.6 promise no version for a deferred item."""
    assert _offenders(_changelog_sections()) == []


def test_the_scan_finds_a_promise_when_one_is_written() -> None:
    """Control: a bullet that promises a deferred item for a version is caught."""
    assert _offenders("- Scheduled for v0.6.7: K11 and the shared uv caches.\n")
    assert _offenders("- KI-1 is planned for v0.7.0.\n")
    assert not _offenders("- KI-1 is deferred, unplanned.\n")
