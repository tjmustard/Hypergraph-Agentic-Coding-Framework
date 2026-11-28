"""The kit docs of the host tool (OPR.99.0.16.4): OPS.md "Host operations" and lead.md's command.

Seats read these two files to learn the one heavy-job command, its per-install path (R193) and who
may run which verb (R200).
"""

from __future__ import annotations

import re

import pytest
from conftest import REPO_ROOT

KIT = REPO_ROOT / "openrig" / "hacf"
HEADING = "## Host operations"
SEAT_VERBS = ("job run", "job status", "status", "boundary", "idle-point status")
PERSON_VERBS = ("pause", "resume", "park", "restore", "idle-point request|release", "supervisor")


def section(rel: str, heading: str) -> str:
    """The lines under `heading` of a kit file, up to the next heading of the same level."""
    lines = (KIT / rel).read_text(encoding="utf-8").splitlines()
    assert lines.count(heading) == 1, f"{rel}: expected one {heading!r}"
    level = heading.split()[0] + " "
    body = []
    for line in lines[lines.index(heading) + 1 :]:
        if line.startswith(level):
            break
        body.append(line)
    return "\n".join(body)


def flat(text: str) -> str:
    """The text with every run of white space as one space, so wrapping does not matter."""
    return " ".join(text.split())


def test_host_operations_sits_between_the_restart_rule_and_the_team_view() -> None:
    """MR 3: the section follows the rig env (restart) rule and precedes the Herdr view."""
    lines = (KIT / "OPS.md").read_text(encoding="utf-8").splitlines()
    headings = [line for line in lines if line.startswith("## ")]
    at = headings.index(HEADING)
    assert headings[at - 1].startswith("## Rig env keys are stored when a rig is created")
    assert headings[at + 1] == "## The Herdr team view (herdr-view.py)"


def verbs_of(text: str, label: str) -> list[str]:
    """The backticked verbs of the one list item that starts with `label`, up to its first stop."""
    items = [flat(item) for item in re.split(r"^- ", text, flags=re.MULTILINE)]
    (item,) = [item for item in items if item.startswith(label)]
    return re.findall(r"`([^`]+)`", item.partition(". ")[0])


def test_host_operations_says_who_runs_which_verb() -> None:
    """MR 3, R200: the seats' verbs and the person's or operator's verbs, each list exact."""
    text = section("OPS.md", HEADING)
    assert verbs_of(text, "**Seats run only**") == list(SEAT_VERBS)
    assert verbs_of(text, "**The person, or the operator on the person's word,** runs") == [
        *PERSON_VERBS[:5],
        "supervise",
        PERSON_VERBS[5],
    ]
    assert "A seat of a mapped rig is refused" in flat(text)


@pytest.mark.parametrize(
    "words",
    [
        "job run --job <name> [--ram-mb N] [--gpu]",
        "--ram-mb",
        "--gpu",
        "tier",
        "backfill",
        "reserve_after_minutes",
        "exit 75",
        "never a verdict",
        "${XDG_CONFIG_HOME:-~/.config}/hacf/host.yml",
        "HACF never writes it",
        "host.example.yml",
        "status --json",
        "boundary <rig>",
        "not in this build",
    ],
)
def test_host_operations_covers_the_scheduler_and_the_config(words: str) -> None:
    """MR 3: the scheduler, the host config, and the two read verbs."""
    assert words in flat(section("OPS.md", HEADING))


def test_lead_routes_heavy_commands_through_job_run() -> None:
    """MR 4, R193: the command, the Rig spec path first and the project's copy as fallback."""
    text = flat(section("agents/hacf/guidance/lead.md", "## How you dispatch"))
    primary = 'python3 "<Rig spec>/bin/hacf-host.py" job run --job <name> [--ram-mb N] [--gpu] -- …'
    fallback = 'python3 "<project root>/openrig/hacf/bin/hacf-host.py"'
    assert primary in text and fallback in text
    assert text.index(primary) < text.index(fallback)
    assert "when the Rig spec has no `bin/hacf-host.py`" in text
    assert "pytest, check.sh, compat, model runs" in text
    assert 'Exit 75 means "busy, retry", never a verdict' in text


def test_host_operations_covers_the_idle_point() -> None:
    """OPR.99.0.16.7 MR 5: what it blocks, what it never does, and the operator's sequence."""
    text = section("OPS.md", HEADING)
    assert "### The idle point" in text.splitlines()
    words = flat(text)
    for wanted in (
        "New heavy jobs",
        "new slice handoffs",
        "Restores",
        "never installs, upgrades, stops or parks",
        "exit 75",
        "unmapped",
    ):
        assert wanted in words, wanted
    order = [
        "idle-point request --purpose openrig-upgrade:<V>",
        "idle-point wait",
        "upgrade the host",
        "idle-point release",
    ]
    steps = flat(text[text.index("### The idle point") :])
    positions = [steps.index(step) for step in order]
    assert positions == sorted(positions)


def test_lead_holds_new_handoffs_during_a_drain() -> None:
    """OPR.99.0.16.7 MR 3, R187: read the status, hold a new slice, let one in flight finish."""
    text = flat(section("agents/hacf/guidance/lead.md", "## How you dispatch"))
    assert 'python3 "<Rig spec>/bin/hacf-host.py" idle-point status --json' in text
    assert "Before you hand off a **new** slice" in text
    assert "While one is in force, don't hand off" in text
    assert "waiting on the drain" in text
    assert "A slice already in flight finishes, its oracle and check.sh included" in text
