"""S15b Test 10: render_claude renders each deny/ask rule once (R54)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import hyper_firewall
from conftest import REPO_ROOT

GOLDEN = Path(__file__).parent / "golden" / "claude_rules.json"
BEGIN = "# BEGIN HACF MANAGED BLOCK: agentignore"
END = "# END HACF MANAGED BLOCK: agentignore"
MANAGED_BLOCK = (
    f"{BEGIN}\n"
    "# HACF: historical drafts and unverified outputs agents must not read (hyper_firewall.py).\n"
    "spec/archive/\n"
    "tests/candidate_outputs/\n"
    f"{END}\n"
)


def _with_managed_block() -> str:
    """The repo's .agentignore with any managed region removed, then the block appended."""
    text = (REPO_ROOT / ".agentignore").read_text(encoding="utf-8")
    text = re.sub(rf"(?ms)^{re.escape(BEGIN)}$.*?^{re.escape(END)}$\n?", "", text)
    if not text.endswith("\n"):
        text += "\n"
    return text + MANAGED_BLOCK


def test_render_claude_dedupes_rules():
    """The managed block's repeated patterns render once, in golden order."""
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))["permissions"]
    text = _with_managed_block()
    # The input really repeats the two managed patterns above the block.
    for pattern in ("spec/archive/", "tests/candidate_outputs/"):
        assert text.splitlines().count(pattern) == 2, pattern
    permissions = hyper_firewall.render_claude(text)["permissions"]
    deny = permissions["deny"]
    assert len(deny) == len(set(deny))
    # Equal to the golden list, which is the order of first occurrences.
    assert deny == golden["deny"]
    assert permissions["ask"] == golden["ask"]


def test_render_claude_dedupes_a_pattern_listed_twice():
    """A pattern listed twice in one section renders once."""
    text = "# Read-only\nnotes/\nnotes/\n"
    deny = hyper_firewall.render_claude(text)["permissions"]["deny"]
    path = hyper_firewall.translate("notes/")
    assert deny.count(f"Read({path})") == 1
    single = hyper_firewall.render_claude("# Read-only\nnotes/\n")["permissions"]["deny"]
    assert deny == single
