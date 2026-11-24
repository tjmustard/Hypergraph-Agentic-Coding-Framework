"""Every hacf role's delivered start-up set carries the required rules (v064-context OPR.99.0.8.5).

The delivered set of a profile is what the rig spec hands that seat at launch: the rig's
`culture_file` (CULTURE.md), its shared start-up files in rig.yaml order (startup/context.md,
startup/protocol.md), the member's own start-up files (the lead's startup/lead-first-move.md) and
the profile's role file from agents/hacf/agent.yaml. Each REQUIRED pattern is the exact phrase the
source file uses; only runs of whitespace are collapsed, because the files wrap their lines. A
trim (slice OPR.99.0.8.6) that drops a phrase fails here with the role and the rule's name.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
RIG_DIR = REPO_ROOT / "openrig" / "hacf"

# (name, exact phrase, where it lives today)
REQUIRED = [
    (
        "footer-line-1",
        "Agentic-Assistant: OpenRig (Claude Code harness) · model",
        "CULTURE.md",
    ),
    ("footer-line-2", "Agentic-Framework: HACF v", "CULTURE.md"),
    (
        "no-co-author",
        "Never add a Claude or Anthropic co-author trailer",
        "CULTURE.md",
    ),
    (
        "push-only-with-the-person",
        "never push or publish without the person's own word for that specific work",
        "startup/protocol.md",
    ),
    (
        "no-secrets",
        "Never print, log or commit a secret (a token, a key, a password or an .env value).",
        "CULTURE.md",
    ),
    ("no-no-verify", "Never `--no-verify` a commit or `-n` a hook.", "startup/protocol.md"),
    ("no-ask-user-question", "Never use `AskUserQuestion`", "startup/protocol.md"),
    ("no-git-add-all", "never runs `git add -A` or `git add .`", "startup/protocol.md"),
    # P25 (lead ruling, v064-context .7): the lore rule is back in CULTURE.md
    (
        "lore-propose-only",
        "Seats propose lore and never approve or reject it; the person approves it with "
        "`/hyper-lore-approve`.",
        "CULTURE.md",
    ),
]


def squash(text: str) -> str:
    """Collapse each run of whitespace to one space."""
    return re.sub(r"\s+", " ", text)


def delivered_files(role: str) -> list[Path]:
    """The files the rig spec delivers to a seat of this profile, in delivery order."""
    rig = yaml.safe_load((RIG_DIR / "rig.yaml").read_text(encoding="utf-8"))
    files = [RIG_DIR / rig["culture_file"]]
    files += [RIG_DIR / f["path"] for f in rig["startup"]["files"]]
    member = next(m for pod in rig["pods"] for m in pod["members"] if m["profile"] == role)
    files += [RIG_DIR / f["path"] for f in (member.get("startup") or {}).get("files") or []]
    agent_dir = RIG_DIR / member["agent_ref"].split(":", 1)[1]
    agent = yaml.safe_load((agent_dir / "agent.yaml").read_text(encoding="utf-8"))
    files += [agent_dir / f["path"] for f in agent["profiles"][role]["startup"]["files"]]
    return files


def delivered_text(role: str) -> str:
    """The delivered set of a profile as one whitespace-collapsed text."""
    return squash("\n".join(p.read_text(encoding="utf-8") for p in delivered_files(role)))


def missing_rules(text: str) -> list[str]:
    """The names of the REQUIRED rules whose phrase is not in text."""
    return [name for name, phrase, _where in REQUIRED if squash(phrase) not in text]


ROLES = list(
    yaml.safe_load((RIG_DIR / "agents" / "hacf" / "agent.yaml").read_text(encoding="utf-8"))[
        "profiles"
    ]
)


def test_seven_profiles_and_nine_rules() -> None:
    """The table names 9 rules (P25 added the lore rule) and the rig has the 7 HACF profiles."""
    assert len(REQUIRED) == 9 and len({r[0] for r in REQUIRED}) == 9
    assert ROLES == ["lead", "architect", "red-team", "resolve", "execute", "audit", "scribe"]


def test_lead_set_has_lead_first_move() -> None:
    """Only the lead's delivered set holds lead-first-move.md."""
    assert RIG_DIR / "startup" / "lead-first-move.md" in delivered_files("lead")
    for role in ROLES[1:]:
        assert RIG_DIR / "startup" / "lead-first-move.md" not in delivered_files(role)
    for role in ROLES:
        files = delivered_files(role)
        assert files[:3] == [
            RIG_DIR / "CULTURE.md",
            RIG_DIR / "startup" / "context.md",
            RIG_DIR / "startup" / "protocol.md",
        ]
        assert files[-1].parent.name == "guidance" and all(f.is_file() for f in files)


def _params():
    for role in ROLES:
        for name, phrase, where in REQUIRED:
            yield pytest.param(role, name, phrase, where, id=f"{role}-{name}")


@pytest.mark.parametrize(("role", "name", "phrase", "where"), list(_params()))
def test_rule_in_delivered_set(role: str, name: str, phrase: str, where: str) -> None:
    """Each role's delivered start-up set carries each required rule, word for word."""
    assert squash(phrase) in delivered_text(role), (
        f"role {role}: required rule {name!r} missing (expected in {where})"
    )


def test_a_dropped_rule_is_named() -> None:
    """Removing one phrase from a delivered set reports exactly that rule."""
    text = delivered_text("execute")
    phrase = squash(dict((n, p) for n, p, _w in REQUIRED)["no-no-verify"])
    assert phrase in text
    assert "no-no-verify" in missing_rules(text.replace(phrase, ""))
    assert "no-no-verify" not in missing_rules(text)
