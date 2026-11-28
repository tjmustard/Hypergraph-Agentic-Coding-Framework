"""No document promises a version for an item v0.6.6 to v0.6.8 deferred (OPR.0.6.6.1, .7.1, .8.1).

The README and the CHANGELOG's `[Unreleased]` and `[0.6.8]` to `[0.6.6]` sections may name a
deferred item but never pair one with a `v0.6.` or `v0.7.` version as a target.
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
    # v0.6.8's deferrals (OPR.0.6.8.1); each matches the deferral's own words, not the feature's.
    re.compile(r"old-client queue gap", re.IGNORECASE),  # R190 (OPR.99.0.16.3)
    re.compile(r"[Aa]uto-park is off by default|[Ll]ive auto-park"),  # R181, R195 (.16.11)
    re.compile(r"best-effort snapshots"),  # the OpenRig gaps U1-U4 (OPR.99.0.16.11)
    re.compile(r"dry-run trial"),  # the grace fix's second dry run (OPR.99.0.16.18)
    re.compile(r"0\.7\.24 has not been run live"),  # OPR.99.0.16.18
    re.compile(r"closing of its open queue rows|row closing"),  # P109 (OPR.99.0.16.18)
    re.compile(r"`lastActivityAt` again|grace is not re-read"),  # OPR.99.0.16.18
    re.compile(r"stopped rig (that has|with) no HACF record"),  # P94 (OPR.99.0.16.11)
    re.compile(r"[Mm]anual park (or|and) restore"),  # P86 (OPR.99.0.16.8)
    re.compile(r"park conditions"),  # P106 (OPR.99.0.16.11)
    re.compile(r"straight to the `job run` process"),  # P89 (OPR.99.0.16.9)
    re.compile(r"host scripts outside HACF"),  # R192 (OPR.99.0.16.11)
    re.compile(r"advisor experiment's run"),  # R224 (OPR.99.0.16.13)
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
    """The `[Unreleased]` and `[0.6.8]` to `[0.6.6]` sections of the CHANGELOG."""
    text = CHANGELOG.read_text(encoding="utf-8")
    start = text.index("## [Unreleased]")
    end = text.index("## [0.6.5]")
    section = text[start:end]
    assert all(f"## [0.6.{n}]" in section for n in (8, 7, 6))
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
