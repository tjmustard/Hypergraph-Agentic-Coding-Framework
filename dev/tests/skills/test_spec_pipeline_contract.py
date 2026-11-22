"""Contract tests for the /hyper-architect, /hyper-redteam and /hyper-resolve text.

The skills are prose, so these are grep checks; nothing here runs a command or writes a file.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from conftest import REPO_ROOT

ARCHITECT = REPO_ROOT / ".agents/skills/hyper-architect/SKILL.md"
REDTEAM = REPO_ROOT / ".agents/skills/hyper-redteam/SKILL.md"
RESOLVE = REPO_ROOT / ".agents/skills/hyper-resolve/SKILL.md"

GATED_VERB = re.compile(r"hyper_lore\.py (approve|reject|retire|reopen|review|approval-request)\b")
REQUIRED = {
    ARCHITECT: (
        "**One Question at a Time:**",
        "hyper_gates.py mode",
        "hyper_ledger.py impact --nodes",
        "hyper_codegraph.py search",
        "hyper_lore.py list --json",
        "hacf_status: draft",
    ),
    REDTEAM: ("hyper_lore.py list --json", "hyper_codegraph.py search", "read only"),
    RESOLVE: (
        "**The Pacing Loop:**",
        "**Forced Trade-offs:**",
        "**Strict Scope:**",
        "hyper_gates.py mode",
        "hyper_scope.py project init --check-only --json",
        "hyper_scope.py mission create --name <mission> --from-draft",
        "hyper_scope.py slice create --mission",
        "--intent '<the MiniPRD's one-sentence intent>'",
        "hyper_spec.py lint --mission",
        "hyper_lore.py propose --type decision",
        "at most 5 MiniPRDs or slices per response",
        "archive_specs.py --paths spec/active/Draft_PRD.md spec/active/RedTeam_Report.md "
        "--label <mission>",
        "WARNING: foreign root item",
        "spec/compiled/SuperPRD_<Feature>.md",
    ),
}


def text_of(path: Path) -> str:
    """Return a file's text."""
    return path.read_text(encoding="utf-8")


def section(text: str, heading: str) -> str:
    """Return the `## <heading>` section, up to the next `## ` heading."""
    found = [s for s in re.split(r"^(?=## )", text, flags=re.M) if s.startswith(f"## {heading}\n")]
    assert len(found) == 1, heading
    return found[0]


@pytest.mark.parametrize("path", list(REQUIRED), ids=["architect", "redteam", "resolve"])
def test_spec_pipeline_contract(path: Path) -> None:
    """No bare SuperPRD target, no .agent/schemas path, python3 only, the required commands."""
    text = text_of(path)
    assert "SuperPRD.md" not in text
    assert ".agent/schemas" not in text
    assert not re.search(r"\bpython\b(?!3)", text), "bare python"
    for phrase in REQUIRED[path]:
        assert phrase in text, phrase
    if path == RESOLVE:
        assert not GATED_VERB.search(text), "resolve runs a gated lore verb"


def test_resolve_replan_mode_text() -> None:
    """Replan mode names its three inputs, pins finished slices and logs each change (R14)."""
    replan = section(text_of(RESOLVE), "Replan mode")
    for phrase in (
        "trade-off log",
        "design annex",
        "failure record of the stopped slice",
        "only where it governs unfinished slices",
        "new R-row",
        "continuing the numbering",
        "Seed deviations row",
        "Recompile only unfinished MiniPRDs or slices",
        "Never change a finished slice",
        "never edit anything a slice ref pins",
    ):
        assert phrase in replan, phrase


@pytest.mark.parametrize("path", [ARCHITECT, REDTEAM], ids=["architect", "redteam"])
def test_architect_redteam_binding_text(path: Path) -> None:
    """Stale CANON and PROPOSED user-decision nodes bind; other PROPOSED nodes do not (R23)."""
    rule = [line for line in text_of(path).splitlines() if "(R23)" in line]
    assert len(rule) == 1
    for phrase in (
        "CANON nodes",
        "stale CANON nodes (binding until retired or superseded)",
        "PROPOSED nodes with `origin: user-decision` (binding until rejected)",
        "binding constraints",
    ):
        assert phrase in rule[0], phrase
