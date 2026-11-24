"""The shipped openrig/hacf/rig.yaml carries exactly one rig-level key (v064-context OPR.99.0.8.7).

TJ (03:18Z) allowed per-member rig.yaml keys only where the compat check proves them on OpenRig
0.7.16 and 0.7.21. This slice adds one rig-level `env:` key that turns off the account's claude.ai
MCP connectors; no member carries a Claude key, and `claude_mcp: none` (which would also drop
codebase-memory-mcp) is never shipped.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
RIG = REPO_ROOT / "openrig" / "hacf" / "rig.yaml"
MEMBER_KEYS_FORBIDDEN = ("claude_mcp", "env", "append_system_prompt_file", "claude_permissions")


def spec() -> dict:
    """The shipped rig.yaml."""
    return yaml.safe_load(RIG.read_text(encoding="utf-8"))


def members() -> list[dict]:
    """Every member of every pod."""
    return [m for pod in spec()["pods"] for m in pod["members"]]


def test_one_rig_level_env_key() -> None:
    """Exactly one top-level env mapping, holding only ENABLE_CLAUDEAI_MCP_SERVERS: "false"."""
    data = spec()
    assert data["env"] == {"ENABLE_CLAUDEAI_MCP_SERVERS": "false"}
    assert isinstance(data["env"]["ENABLE_CLAUDEAI_MCP_SERVERS"], str)
    text = RIG.read_text(encoding="utf-8")
    assert sum(1 for line in text.splitlines() if line.startswith("env:")) == 1


def test_no_member_claude_keys() -> None:
    """No member carries claude_mcp, env, append_system_prompt_file or claude_permissions."""
    for member in members():
        assert not [k for k in MEMBER_KEYS_FORBIDDEN if k in member], member["id"]


def test_no_claude_mcp_anywhere() -> None:
    """claude_mcp is never shipped, at any level; neither is CLAUDE_CONFIG_DIR."""
    text = RIG.read_text(encoding="utf-8")
    assert "claude_mcp" not in spec()
    assert "claude_mcp:" not in text and "CLAUDE_CONFIG_DIR" not in text


def test_every_cwd_is_still_the_project_root() -> None:
    """Each member's cwd is still ../.. (the project root of a project copy)."""
    assert members() and all(m["cwd"] == "../.." for m in members())


def test_the_env_key_comment_names_the_decision() -> None:
    """The comment above env says v0.6.4, the compat condition and that project MCP servers stay.

    It cites no bare ADR id: shipped files must not (dev/tests/test_shipped_lore_refs.py).
    """
    text = RIG.read_text(encoding="utf-8")
    comment = " ".join(text.split("\nenv:", 1)[0].splitlines()[-3:])
    assert "v0.6.4" in comment and "0.7.16 and 0.7.21" in comment
    assert "codebase-memory-mcp" in comment
    assert "ADR-" not in comment
