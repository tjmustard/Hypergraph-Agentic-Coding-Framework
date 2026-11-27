"""openrig/hacf/bin/seat-config.py validates and resolves the per-seat config (OPR.99.0.11.6).

Every run reads files in tmp_path (a copy of the shipped roles.yml, caps.yml and seat-config.yml,
plus an overrides.yml or preset a test writes); nothing is written inside the HACF checkout, and
the tool itself writes nothing anywhere.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
HACF = REPO_ROOT / "openrig" / "hacf"
TOOL = HACF / "bin" / "seat-config.py"
ROLES = list(yaml.safe_load((HACF / "roles.yml").read_text())["roles"])
CAPS = yaml.safe_load((HACF / "caps.yml").read_text())["seats"]
NOTE = REPO_ROOT / "dev" / "telemetry" / "h5-v066.md"
OPUS = "claude-opus-5-5"
# The routing block seat-config.yml ships (OPR.99.0.14.5, MR 3).
SHIPPED_ROUTING = {
    "question": {"destination": {"rule": "discover"}},
    "update": {"destination": {"rule": "discover"}},
    "tags": {
        "question": "hacf:question",
        "update": "hacf:update",
        "answer": "hacf:answer",
        "extra": [],
    },
    "format": {"options": "A/B/C", "recommended": "required"},
}
# What a seat that `held` ships (OPR.99.0.11.8, MR 5); every other seat ships Opus.
TIERED = {
    "execute": {"model": "claude-sonnet-5-5", "effort": "medium"},
    "lead": {"model": "claude-sonnet-5-5", "effort": "medium"},
    "scribe": {"model": "claude-haiku-5-5"},
}


def h5_verdict() -> dict[str, str]:
    """The note's fenced `h5_verdict` block: {execute|lead|scribe: held|not held|not judged}."""
    match = re.search(r"```yaml\s*\nh5_verdict:(.*?)\n```", NOTE.read_text(), re.S)
    assert match, "dev/telemetry/h5-v066.md has no fenced h5_verdict yaml block"
    verdict = yaml.safe_load("h5_verdict:" + match.group(1))["h5_verdict"]
    assert set(verdict) == set(TIERED) and set(verdict.values()) <= {
        "held",
        "not held",
        "not judged",
    }
    return verdict


def expected_seat(role: str) -> dict[str, str]:
    """The value seat-config.yml must ship for a role, from the note's verdict (not the file)."""
    if role in TIERED and h5_verdict()[role] == "held":
        return TIERED[role]
    return {"model": OPUS}


def tool(directory: Path, *args: str) -> subprocess.CompletedProcess:
    """Run seat-config.py on a directory."""
    return subprocess.run(
        [sys.executable, str(TOOL), *args, "--dir", str(directory)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


@pytest.fixture
def conf(tmp_path: Path) -> Path:
    """A directory holding the shipped roles.yml, caps.yml and seat-config.yml."""
    for name in ("roles.yml", "caps.yml", "seat-config.yml"):
        shutil.copy(HACF / name, tmp_path / name)
    return tmp_path


def resolved(directory: Path, *args: str) -> dict:
    """Return the resolved seats of a directory."""
    proc = tool(directory, "resolve", "--json", *args)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)["seats"]


def test_a_the_shipped_config_validates_and_resolves_all_roles(conf: Path) -> None:
    """(a) 7 roles, each model the verdict's value from default, the caps equal caps.yml's."""
    assert tool(conf, "validate").returncode == 0
    seats = resolved(conf)
    assert sorted(seats) == sorted(ROLES) and len(seats) == 7
    for role, row in seats.items():
        assert row["model"] == {"value": expected_seat(role)["model"], "source": "default"}
        for key in ("cap_tokens", "behaviour", "reset_scope"):
            assert row[key] == {"value": CAPS[role][key], "source": "default"}


def test_b_overrides_replace_one_key_and_everything_else_stays_default(conf: Path) -> None:
    """(b) A model override and a cap override show source override; nothing else changes."""
    (conf / "overrides.yml").write_text(
        "seats:\n  scribe:\n    model: claude-haiku-5-5\n  execute:\n    cap_tokens: 250000\n"
    )
    seats = resolved(conf)
    assert seats["scribe"]["model"] == {"value": "claude-haiku-5-5", "source": "override"}
    assert seats["execute"]["cap_tokens"] == {"value": 250000, "source": "override"}
    others = [
        (role, key, cell)
        for role, row in seats.items()
        for key, cell in row.items()
        if (role, key) not in (("scribe", "model"), ("execute", "cap_tokens"))
    ]
    assert others and all(cell["source"] == "default" for _, _, cell in others)


def test_c_default_then_preset_then_override(conf: Path) -> None:
    """(c) The same key: the preset beats the default, the override beats the preset."""
    (conf / "presets").mkdir()
    (conf / "presets" / "lean.yml").write_text(
        "schema: hacf.seat-preset/v2\ntested: not tested\n"
        "seats:\n  audit:\n    model: claude-sonnet-5-5\n"
        "  scribe:\n    model: claude-haiku-5-5\n"
    )
    text = (conf / "seat-config.yml").read_text().replace("preset: null", "preset: lean")
    (conf / "seat-config.yml").write_text(text)
    seats = resolved(conf)
    assert seats["audit"]["model"] == {"value": "claude-sonnet-5-5", "source": "preset:lean"}
    assert seats["lead"]["model"]["source"] == "default"
    (conf / "overrides.yml").write_text("seats:\n  audit:\n    model: claude-opus-5-5\n")
    assert resolved(conf)["audit"]["model"] == {"value": "claude-opus-5-5", "source": "override"}
    assert resolved(conf)["scribe"]["model"]["source"] == "preset:lean"


def refused(conf: Path, expected: str) -> None:
    """Assert validate and resolve both refuse with a line containing expected."""
    proc = tool(conf, "validate")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert expected in proc.stderr, proc.stderr
    assert tool(conf, "resolve", "--json").returncode == 1  # resolve validates first


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ("seats:\n  ghost:\n    model: x\n", "overrides.yml: seats.ghost: unknown role"),
        ("seats:\n  lead:\n    modle: x\n", "overrides.yml: seats.lead.modle: unknown key"),
        ("seats:\n  lead:\n    mcp: off\n", "overrides.yml: seats.lead.mcp: reserved key"),
        ("seats:\n  lead:\n    effort: loud\n", "overrides.yml: seats.lead.effort: effort must be"),
        (
            "seats:\n  lead:\n    cap_tokens: 0\n",
            "overrides.yml: seats.lead.cap_tokens: cap_tokens must",
        ),
        ("seats:\n  lead:\n    cap_tokens: true\n", "overrides.yml: seats.lead.cap_tokens"),
        (
            "seats:\n  lead:\n    behaviour: nap\n",
            "overrides.yml: seats.lead.behaviour: behaviour must",
        ),
        (
            "seats:\n  lead:\n    reset_scope: week\n",
            "overrides.yml: seats.lead.reset_scope: reset_scope must",
        ),
        (
            "schema: hacf.seat-overrides/v9\nseats: {}\n",
            "overrides.yml: schema: must be hacf.seat-overrides/v1 or hacf.seat-overrides/v2",
        ),
        ("seats: [lead]\n", "overrides.yml: seats: must be a mapping"),
        ("{not yaml", "overrides.yml: -: not valid YAML"),
    ],
)
def test_d_each_overrides_refusal_names_the_file_and_the_key(
    conf: Path, overrides: str, expected: str
) -> None:
    """(d) One refusal each, with `<file>: <key path>: <reason>`."""
    (conf / "overrides.yml").write_text(overrides)
    refused(conf, expected)


def test_d_a_missing_preset_is_refused(conf: Path) -> None:
    """(d) seat-config.yml naming a preset that is not in presets/."""
    text = (conf / "seat-config.yml").read_text().replace("preset: null", "preset: nope")
    (conf / "seat-config.yml").write_text(text)
    refused(conf, "seat-config.yml: preset: names presets/nope.yml, which does not exist")


def test_d_wrong_schema_in_each_of_the_files(conf: Path) -> None:
    """(d) seat-config.yml, caps.yml and a preset with an unknown schema."""
    original = {n: (conf / n).read_text() for n in ("seat-config.yml", "caps.yml")}
    (conf / "seat-config.yml").write_text(
        original["seat-config.yml"].replace("hacf.seat-config/v2", "hacf.seat-config/v9")
    )
    refused(conf, "seat-config.yml: schema: must be hacf.seat-config/v1")
    (conf / "seat-config.yml").write_text(original["seat-config.yml"])
    (conf / "caps.yml").write_text(original["caps.yml"].replace("hacf.caps/v1", "hacf.caps/v0"))
    refused(conf, "caps.yml: schema: must be hacf.caps/v1")
    (conf / "caps.yml").write_text(original["caps.yml"])
    (conf / "presets").mkdir()
    (conf / "presets" / "p.yml").write_text("schema: hacf.seat-preset/v9\nseats: {}\n")
    text = original["seat-config.yml"].replace("preset: null", "preset: p")
    (conf / "seat-config.yml").write_text(text)
    refused(conf, "presets/p.yml: schema: must be hacf.seat-preset/v1")


def test_d_the_defaults_file_takes_no_unknown_reserved_or_context_key(conf: Path) -> None:
    """(d) seat-config.yml itself: a typo, a reserved key and a context key are all refused."""
    base = (conf / "seat-config.yml").read_text()
    for bad, expected in (
        ("  lead:      {model: claude-opus-5-5}", "seat-config.yml: seats.lead"),
        ("  lead:      {modle: claude-opus-5-5}", "seats.lead.modle: unknown key"),
        ("  lead:      {model: claude-opus-5-5, mcp: off}", "seats.lead.mcp: reserved key"),
        (
            "  lead:      {model: claude-opus-5-5, cap_tokens: 5}",
            "seats.lead.cap_tokens: not a key",
        ),
    ):
        (conf / "seat-config.yml").write_text(
            base.replace("  lead:      {model: claude-opus-5-5}", bad)
        )
        if "modle" in bad or "mcp" in bad or "cap_tokens" in bad:
            refused(conf, expected)


def test_e_overrides_without_a_schema_line_is_valid(conf: Path) -> None:
    """(e) R89: an absent schema is valid (read as v2, D4); so is the one-line revert override."""
    (conf / "overrides.yml").write_text("seats:\n  scribe:\n    model: claude-opus-5-5\n")
    assert tool(conf, "validate").returncode == 0
    (conf / "overrides.yml").write_text(
        "schema: hacf.seat-overrides/v1\nseats:\n  scribe:\n    model: claude-opus-5-5\n"
    )
    assert tool(conf, "validate").returncode == 0


def test_role_and_section_filters_and_usage(conf: Path) -> None:
    """--role and --section narrow the output; a bad role or section is a usage error (2)."""
    seats = resolved(conf, "--role", "audit", "--section", "seats")
    assert list(seats) == ["audit"]
    assert list(seats["audit"]) == ["model", "tools", "instructions"]
    assert tool(conf, "resolve", "--json", "--role", "nobody").returncode == 2
    assert tool(conf, "resolve", "--json", "--section", "x").returncode == 2
    assert tool(conf, "bogus").returncode == 2


def test_the_tool_never_writes(conf: Path) -> None:
    """Validate and resolve change no byte under the directory and never create overrides.yml."""
    before = {p.name: p.read_bytes() for p in conf.iterdir()}
    tool(conf, "validate")
    tool(conf, "resolve", "--json")
    assert {p.name: p.read_bytes() for p in conf.iterdir()} == before
    assert not (conf / "overrides.yml").exists()


def test_the_shipped_defaults_are_exactly_the_documented_ones() -> None:
    """The shipped file holds no override and no preset; each role carries its verdict's value."""
    doc = yaml.safe_load((HACF / "seat-config.yml").read_text())
    assert doc["schema"] == "hacf.seat-config/v2" and doc["preset"] is None
    assert doc["sections"] == {
        "context": "caps.yml",
        "tools": "roles.yml",
        "skills": "agents/hacf/agent.yaml",
        "harness": "agents/hacf/agent.yaml",
    }
    assert doc["reserved"] == ["mcp"] and doc["routing"] == SHIPPED_ROUTING
    assert doc["seats"] == {role: expected_seat(role) for role in ROLES}
    assert not (HACF / "overrides.yml").exists()  # presets ship, none selected (test_presets)


def test_only_the_held_seats_ship_a_tiered_model() -> None:
    """(OPR.99.0.11.8 MR 6) the shipped seats off Opus are exactly the roles the note marks held."""
    doc = yaml.safe_load((HACF / "seat-config.yml").read_text())
    verdict = h5_verdict()
    off_opus = {role for role, value in doc["seats"].items() if value["model"] != OPUS}
    assert off_opus == {role for role, v in verdict.items() if v == "held"}
    for role in off_opus:
        assert doc["seats"][role] == TIERED[role]


# --- seat config v2 (OPR.99.0.14.2) -----------------------------------------------------------

AGENT = "agents/hacf/agent.yaml"
DENY = {
    role: entry["tools_deny"]
    for role, entry in yaml.safe_load((HACF / "roles.yml").read_text())["roles"].items()
}
PROFILES = yaml.safe_load((HACF / AGENT).read_text())["profiles"]
RESOURCE = "adversarial-agent-hardening"
FLOOR_TOOL = "AskUserQuestion"
ETA = "seats:\n" + "".join(
    f"  {role}:\n    tools: {{remove_deny: [Monitor, TaskStop]}}\n"
    for role in ("execute", "audit", "lead")
)
DISCOVER = {"value": {"rule": "discover"}, "source": "default"}


@pytest.fixture
def full(conf: Path) -> Path:
    """`conf` plus the shipped agent.yaml."""
    (conf / "agents" / "hacf").mkdir(parents=True)
    shutil.copy(HACF / AGENT, conf / AGENT)
    return conf


def over(directory: Path, text: str) -> None:
    """Write overrides.yml."""
    (directory / "overrides.yml").write_text(text)


def seat(role: str, body: str) -> str:
    """An overrides.yml setting one role's keys (a YAML flow mapping body)."""
    return f"seats:\n  {role}: {{{body}}}\n"


def use_preset(directory: Path, body: str, name: str = "p", schema: str = "v2") -> None:
    """Write presets/<name>.yml with `body` and select it; a tested line unless body has one."""
    (directory / "presets").mkdir(exist_ok=True)
    if schema == "v2" and "tested:" not in body:
        body = "tested: not tested\n" + body
    (directory / "presets" / f"{name}.yml").write_text(f"schema: hacf.seat-preset/{schema}\n{body}")
    text = (directory / "seat-config.yml").read_text()
    (directory / "seat-config.yml").write_text(text.replace("preset: null", f"preset: {name}"))


def ok(directory: Path, *args: str) -> str:
    """Validate; return stderr (warnings and notes)."""
    proc = tool(directory, "validate", *args)
    assert proc.returncode == 0, proc.stderr
    return proc.stderr


def make_v1(directory: Path) -> None:
    """Turn the copied seat-config.yml into a v1 file."""
    path = directory / "seat-config.yml"
    doc = yaml.safe_load(path.read_text())
    doc["schema"] = "hacf.seat-config/v1"
    doc.pop("routing", None)  # v1 has no routing key
    doc["reserved"] = ["harness", "instructions", "skills", "tools", "mcp"]
    path.write_text(yaml.safe_dump(doc, sort_keys=False))


# MR 1


def test_v1_files_are_read_with_a_note_each(conf: Path) -> None:
    """A v1 seat-config.yml and overrides validate; one stderr note per v1 file."""
    make_v1(conf)
    over(conf, "schema: hacf.seat-overrides/v1\nseats:\n  lead: {model: m}\n")
    err = ok(conf).splitlines()
    assert err == [
        f"note: {name}: schema v1: read as v1; the new keys need v2"
        for name in ("seat-config.yml", "overrides.yml")
    ]
    proc = tool(conf, "resolve", "--json")
    assert proc.returncode == 0 and "read as v1" in proc.stderr
    assert json.loads(proc.stdout)["seats"]["lead"]["model"]["value"] == "m"


def test_a_v1_preset_is_refused_for_want_of_a_tested_line(conf: Path) -> None:
    """MR 2: v1 has no tested key, so a v1 preset cannot state what was tested."""
    use_preset(conf, "seats: {}\n", schema="v1")
    refused(conf, "presets/p.yml: tested: a preset must state what was tested")
    assert "note: presets/p.yml: schema v1" in tool(conf, "validate").stderr


@pytest.mark.parametrize("key", ["tools", "skills", "instructions", "harness", "mcp"])
def test_a_v1_overrides_using_a_new_key_is_reserved_with_the_note(conf: Path, key: str) -> None:
    """v1 rules: the new keys stay reserved."""
    over(conf, "schema: hacf.seat-overrides/v1\n" + seat("lead", f"{key}: x"))
    proc = tool(conf, "validate")
    assert proc.returncode == 1
    assert f"overrides.yml: seats.lead.{key}: reserved key" in proc.stderr
    assert "note: overrides.yml: schema v1" in proc.stderr


def test_a_v1_file_takes_no_routing(conf: Path) -> None:
    """A v1 file has no routing key."""
    over(conf, "schema: hacf.seat-overrides/v1\nrouting: {}\n")
    refused(conf, "overrides.yml: routing: unknown key")


@pytest.mark.parametrize("text", ["seats:\n  lead: {mcp: off}\n", "mcp: off\n"])
def test_mcp_stays_reserved_in_v2(conf: Path, text: str) -> None:
    """The mcp key is refused anywhere."""
    over(conf, "schema: hacf.seat-overrides/v2\n" + text)
    refused(conf, "mcp: reserved key")


# MR 2


def test_the_defaults_file_takes_only_model_and_effort(conf: Path) -> None:
    """A v2 seat-config.yml seat takes no new seat key."""
    path = conf / "seat-config.yml"
    base = path.read_text()
    for key in ("tools", "skills", "instructions", "harness"):
        path.write_text(base.replace("{model: claude-opus-5-5}", f"{{model: m, {key}: x}}", 1))
        refused(conf, f"seats.lead.{key}: not a key of this file (it belongs in overrides.yml")


def test_routing_in_a_preset_is_refused(conf: Path) -> None:
    """Presets may not set routing."""
    use_preset(conf, "seats: {}\nrouting: {}\n")
    refused(conf, "presets/p.yml: routing: presets may not set routing")


@pytest.mark.parametrize("tested", ["''", "'  '", "3", "[a]"])
def test_a_preset_tested_line_is_a_non_empty_string(conf: Path, tested: str) -> None:
    """MR 2: required, and a non-empty string."""
    use_preset(conf, f"tested: {tested}\nseats: {{}}\n")
    refused(conf, "presets/p.yml: tested: a preset must state what was tested")


def test_a_preset_with_a_tested_line_is_valid(conf: Path) -> None:
    """A non-empty tested line passes."""
    use_preset(conf, "tested: not tested\nseats: {}\n")
    ok(conf)


# MR 3


def test_us002_the_eta_overrides_drop_monitor_and_taskstop(conf: Path) -> None:
    """US-002: execute, audit and lead lose the two denies; every other role is unchanged."""
    over(conf, ETA)
    seats = resolved(conf)
    for role, row in seats.items():
        if role in ("execute", "audit", "lead"):
            wanted = [t for t in DENY[role] if t not in ("Monitor", "TaskStop")]
            assert {"Monitor", "TaskStop"} <= set(DENY[role])
            assert row["tools"] == {"value": {"deny": wanted}, "source": "default+override"}
        else:
            assert row["tools"] == {"value": {"deny": DENY[role]}, "source": "default"}


def test_tools_compose_default_then_preset_then_override(conf: Path) -> None:
    """Each layer removes, then appends; the source names the layers that changed the list."""
    use_preset(conf, "seats:\n  execute: {tools: {remove_deny: [Monitor], add_deny: [Zed]}}\n")
    base = [t for t in DENY["execute"] if t != "Monitor"]
    row = resolved(conf)["execute"]["tools"]
    assert row == {"value": {"deny": [*base, "Zed"]}, "source": "default+preset:p"}
    over(conf, seat("execute", "tools: {remove_deny: [Zed, Absent], add_deny: [Monitor, Bash]}"))
    row = resolved(conf)["execute"]["tools"]
    assert row["value"]["deny"] == [*base, "Monitor", "Bash"]
    assert row["source"] == "default+preset:p+override"
    over(conf, seat("execute", "tools: {remove_deny: [Absent], add_deny: [Bash]}"))
    assert resolved(conf)["execute"]["tools"]["source"] == "default+preset:p+override"
    over(conf, seat("lead", "tools: {remove_deny: [Absent]}"))
    assert resolved(conf)["lead"]["tools"]["source"] == "default"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("{remove_deny: [AskUserQuestion]}", "AskUserQuestion is the floor: always denied"),
        ("{add_deny: [mcp__codebase-memory-mcp]}", "codebase-memory-mcp, the floor: never denied"),
        ("{add_deny: [mcp__codebase_memory_mcp]}", "codebase-memory-mcp, the floor: never denied"),
        ("{add_deny: [Codebase_Memory_MCP]}", "codebase-memory-mcp, the floor: never denied"),
        ("{add_deny: [mcp__x__tool]}", "tools.add_deny: 'mcp__x__tool' is not a bare MCP server"),
        ("{remove_deny: [mcp__x__tool]}", "tools.remove_deny: 'mcp__x__tool' is not a bare"),
        ("{add_deny: ['Bash(git push:*)']}", "tools.add_deny: "),
        ("{add_deny: ['']}", "tools.add_deny: "),
        ("{add_deny: [3]}", "tools.add_deny: "),
        ("{add_deny: Bash}", "tools.add_deny: must be a list"),
        ("{allow: [Bash]}", "tools.allow: unknown key"),
        ("[Bash]", "tools: must be a mapping"),
        ("null", "tools: must be a mapping"),
        ("{add_deny: [Bash], remove_deny: [Bash]}", "'Bash' is in both add_deny and remove_deny"),
    ],
)
def test_each_tools_refusal_names_the_key(conf: Path, value: str, expected: str) -> None:
    """The floor and the shape, in the override and in a preset."""
    over(conf, seat("execute", f"tools: {value}"))
    refused(conf, expected)
    assert "overrides.yml: seats.execute.tools" in tool(conf, "validate").stderr
    (conf / "overrides.yml").unlink()
    use_preset(conf, seat("execute", f"tools: {value}"))
    refused(conf, expected)
    assert "presets/p.yml: seats.execute.tools" in tool(conf, "validate").stderr


@pytest.mark.parametrize(
    "item",
    ["*", "?*", "mcp__*", "MCP__*", "mcp__codebase*", "mcp__c?debase-memory-mcp"],
)
def test_a_glob_deny_cannot_reach_the_floor(conf: Path, item: str) -> None:
    """P43: a glob that could match codebase-memory-mcp is refused, in an override and a preset."""
    expected = f"{item!r} denies codebase-memory-mcp, the floor: never denied"
    over(conf, seat("execute", f"tools: {{add_deny: ['{item}']}}"))
    refused(conf, "overrides.yml: seats.execute.tools.add_deny: " + expected)
    (conf / "overrides.yml").unlink()
    use_preset(conf, seat("execute", f"tools: {{add_deny: ['{item}']}}"))
    refused(conf, "presets/p.yml: seats.execute.tools.add_deny: " + expected)


def test_a_deny_that_cannot_reach_the_floor_still_validates(conf: Path) -> None:
    """P43: a bare server deny and a plain tool pass; a glob elsewhere meets the older check."""
    over(conf, seat("execute", "tools: {add_deny: [mcp__github, Monitor]}"))
    ok(conf)
    over(conf, seat("execute", "tools: {add_deny: ['mcp__github*']}"))
    refused(conf, "'mcp__github*' is not a bare MCP server deny")
    assert "codebase" not in tool(conf, "validate").stderr


@pytest.mark.parametrize(
    ("tool_name", "change", "expected"),
    [
        (FLOOR_TOOL, "remove", "roles.yml: roles.lead.tools_deny: lacks AskUserQuestion"),
        ("mcp__codebase-memory-mcp", "append", "roles.yml: roles.lead.tools_deny: "),
        ("mcp__*", "append", "roles.lead.tools_deny: 'mcp__*' denies codebase-memory-mcp"),
    ],
)
def test_a_roles_table_breaking_the_floor_is_refused(
    conf: Path, tool_name: str, change: str, expected: str
) -> None:
    """The base list is under the floor too, so no resolved deny can break it."""
    doc = yaml.safe_load((conf / "roles.yml").read_text())
    getattr(doc["roles"]["lead"]["tools_deny"], change)(tool_name)
    (conf / "roles.yml").write_text(yaml.safe_dump(doc, sort_keys=False))
    refused(conf, expected)


# MR 4


def test_skills_default_to_the_agent_profile_and_compose(full: Path) -> None:
    """Default from agent.yaml; an override removes and appends."""
    row = resolved(full)["scribe"]["skills"]
    assert row == {"value": PROFILES["scribe"]["uses"]["skills"], "source": "default"}
    over(full, seat("scribe", f"skills: {{remove: [{RESOURCE}]}}"))
    assert resolved(full)["scribe"]["skills"] == {"value": [], "source": "default+override"}
    use_preset(full, seat("scribe", f"skills: {{remove: [{RESOURCE}]}}"))
    over(full, seat("scribe", f"skills: {{add: [{RESOURCE}]}}"))
    row = resolved(full)["scribe"]["skills"]
    assert row == {"value": [RESOURCE], "source": "default+preset:p+override"}
    assert ok(full) == ""  # a resource id needs no warning


def loadout(tmp: Path, doc: object) -> str:
    """Write a loadout file beside the config dir; return its path."""
    path = tmp / "loadout.json"
    path.write_text(json.dumps(doc))
    return str(path)


def test_an_unknown_skill_warns_without_a_loadout(full: Path, tmp_path: Path) -> None:
    """Unverified without --loadout, unknown to one that lacks it, valid with one holding it."""
    over(full, seat("scribe", "skills: {add: [mystery]}"))
    err = ok(full)
    assert err == (
        "warning: overrides.yml: seats.scribe.skills.add.mystery: unverified: "
        "rig skill loadout unavailable\n"
    )
    assert resolved(full)["scribe"]["skills"]["value"][-1] == "mystery"
    lacking = loadout(tmp_path, {"scribe": ["other"], "lead": ["mystery"]})
    proc = tool(full, "validate", "--loadout", lacking)
    assert proc.returncode == 1
    assert "overrides.yml: seats.scribe.skills.add: unknown skill mystery" in proc.stderr
    assert ok(full, "--loadout", loadout(tmp_path, {"scribe": ["mystery"]})) == ""


CLAUDE_ROOTS = "<cwd>/.claude/skills, <spec-install>/skills, ~/.claude/skills, ~/.openrig/skills"
NOT_SCANNED = (
    "local-skill is only in <project>/.agents/skills, which OpenRig 0.7.23 does not scan for "
)


def project_with(tmp: Path, name: str = "local-skill", front: str | None = None) -> Path:
    """A project holding .agents/skills/<name>/SKILL.md whose frontmatter name is `front`."""
    project = tmp / "project"
    skill = project / ".agents" / "skills" / name
    skill.mkdir(parents=True)
    front = name if front is None else front
    (skill / "SKILL.md").write_text(f"---\nname: {front}\ndescription: d\n---\n# s\n")
    return project


def test_a_project_skill_passes_with_a_project_root(full: Path, tmp_path: Path) -> None:
    """P44: for a codex seat, <project>/.agents/skills/N/SKILL.md is in the reference set."""
    over(full, seat("scribe", "harness: codex, skills: {add: [local-skill]}"))
    project = tmp_path / "project"
    (project / ".agents" / "skills" / "local-skill").mkdir(parents=True)
    args = ("--project-root", str(project), "--loadout", loadout(tmp_path, {}))
    assert tool(full, "validate", *args).returncode == 1  # a dir without SKILL.md is no skill
    shutil.rmtree(project)
    project_with(tmp_path)
    assert ok(full, *args) == ""
    cell = resolved(full, *args)["scribe"]["skills"]
    assert cell == {"value": [RESOURCE, "local-skill"], "source": "default+override"}


def test_a_project_only_skill_is_refused_for_claude_code(full: Path, tmp_path: Path) -> None:
    """P44: with a loadout it is an error naming the scan roots."""
    over(full, seat("scribe", "skills: {add: [local-skill]}"))
    args = ("--project-root", str(project_with(tmp_path)), "--loadout", loadout(tmp_path, {}))
    proc = tool(full, "validate", *args)
    assert proc.returncode == 1, proc.stderr
    assert (
        f"overrides.yml: seats.scribe.skills.add: {NOT_SCANNED}claude-code "
        f"(scan roots: {CLAUDE_ROOTS})"
    ) in proc.stderr
    assert tool(full, "resolve", "--json", *args).returncode == 1


def test_a_project_only_skill_without_a_loadout_is_warned_and_held(
    full: Path, tmp_path: Path
) -> None:
    """P44: unverified, left out of value, listed under held."""
    over(full, seat("scribe", "skills: {add: [local-skill, mystery]}"))
    args = ("--project-root", str(project_with(tmp_path)))
    err = ok(full, *args)
    assert (
        "warning: overrides.yml: seats.scribe.skills.add.local-skill: unverified: rig skill "
        f"loadout unavailable; {NOT_SCANNED}claude-code (scan roots: {CLAUDE_ROOTS})\n"
    ) in err
    assert err.endswith("skills.add.mystery: unverified: rig skill loadout unavailable\n")
    cell = resolved(full, *args)["scribe"]["skills"]
    assert cell == {
        "value": [RESOURCE, "mystery"],
        "source": "default+override",
        "held": ["local-skill"],
    }
    assert "held" not in resolved(full, *args)["lead"]["skills"]


@pytest.mark.parametrize(
    ("harness", "roots"),
    [
        ("pi", "(scan roots: <spec-install>/skills)"),
        ("agy", "(scan roots: none; harness agy is deferred, not applied)"),
    ],
)
def test_a_project_only_skill_is_refused_for_pi_and_a_deferred_harness(
    full: Path, tmp_path: Path, harness: str, roots: str
) -> None:
    """P44: neither scans the project's .agents/skills for HACF."""
    over(full, seat("scribe", f"harness: {harness}, skills: {{add: [local-skill]}}"))
    args = ("--project-root", str(project_with(tmp_path)), "--loadout", loadout(tmp_path, {}))
    proc = tool(full, "validate", *args)
    assert proc.returncode == 1, proc.stderr
    assert f"{NOT_SCANNED}{harness} {roots}" in proc.stderr


def test_the_harness_that_decides_is_the_resolved_one(full: Path, tmp_path: Path) -> None:
    """P44: a preset's harness counts for an override's skill."""
    use_preset(full, seat("scribe", "harness: codex"))
    over(full, seat("scribe", "skills: {add: [local-skill]}"))
    args = ("--project-root", str(project_with(tmp_path)))
    assert ok(full, *args) == ""
    assert "local-skill" in resolved(full, *args)["scribe"]["skills"]["value"]


@pytest.mark.parametrize(
    ("front", "reason"),
    [
        ("other-name", "and its SKILL.md frontmatter name"),
        ("", "and OpenRig 0.7.23 would not take its SKILL.md: frontmatter missing required "
         "name field"),
    ],
)  # fmt: skip
def test_a_codex_skill_whose_name_differs_is_refused(
    full: Path, tmp_path: Path, front: str, reason: str
) -> None:
    """P44: OpenRig takes a found skill only when frontmatter name equals the directory name."""
    over(full, seat("scribe", "harness: codex, skills: {add: [local-skill]}"))
    root = str(project_with(tmp_path, front=front))
    why = f"local-skill is only in <project>/.agents/skills, {reason}"
    proc = tool(full, "validate", "--project-root", root, "--loadout", loadout(tmp_path, {}))
    assert proc.returncode == 1 and why in proc.stderr, proc.stderr
    assert why in ok(full, "--project-root", root)
    cell = resolved(full, "--project-root", root)["scribe"]["skills"]
    assert cell["held"] == ["local-skill"] and "local-skill" not in cell["value"]


# P46 (OPR.99.0.14.5, MR 8): a found skill must also be one OpenRig 0.7.23 takes
HEAD_OK = "---\nname: local-skill\ndescription: d\n---\n"
NOT_TAKEN = (
    "local-skill is only in <project>/.agents/skills, and OpenRig 0.7.23 would not take its "
    "SKILL.md: "
)
BAD_SKILLS = {
    "no-front-matter": ("# s\n", "no YAML frontmatter delimited by --- lines"),
    "yaml-error": ("---\nname: [\n---\n# s\n", "frontmatter YAML parse error"),
    "not-a-mapping": ("---\n- local-skill\n---\n# s\n", "frontmatter is not a YAML mapping"),
    "no-name": ("---\ndescription: d\n---\n# s\n", "frontmatter missing required name field"),
    "no-description": (
        "---\nname: local-skill\n---\n# s\n",
        "frontmatter missing required description field",
    ),
    "blank-description": (
        '---\nname: local-skill\ndescription: "  "\n---\n# s\n',
        "frontmatter missing required description field",
    ),
    "blank-body": (HEAD_OK + "  \n\n", "SKILL.md body is empty"),
}


@pytest.mark.parametrize(("content", "reason"), list(BAD_SKILLS.values()), ids=list(BAD_SKILLS))
def test_a_codex_skill_openrig_would_not_take_is_refused_or_held(
    full: Path, tmp_path: Path, content: str, reason: str
) -> None:
    """P46: with a loadout an error naming what is missing; without one, warned and held."""
    over(full, seat("scribe", "harness: codex, skills: {add: [local-skill]}"))
    project = project_with(tmp_path)
    (project / ".agents" / "skills" / "local-skill" / "SKILL.md").write_text(content)
    root = ("--project-root", str(project))
    proc = tool(full, "validate", *root, "--loadout", loadout(tmp_path, {}))
    assert proc.returncode == 1, proc.stderr
    assert f"overrides.yml: seats.scribe.skills.add: {NOT_TAKEN}{reason}\n" == proc.stderr
    assert ok(full, *root) == (
        "warning: overrides.yml: seats.scribe.skills.add.local-skill: unverified: rig skill "
        f"loadout unavailable; {NOT_TAKEN}{reason}\n"
    )
    cell = resolved(full, *root)["scribe"]["skills"]
    assert cell["held"] == ["local-skill"] and cell["value"] == [RESOURCE]


@pytest.mark.parametrize(
    "content",
    [
        HEAD_OK + "# s\n",
        (HEAD_OK + "# s\n").replace("\n", "\r\n"),
        "---  \nname: local-skill\ndescription: d\n---\nbody",
    ],
    ids=["lf", "crlf", "trailing-spaces"],
)
def test_a_full_codex_skill_resolves(full: Path, tmp_path: Path, content: str) -> None:
    """P46: name, description and body: valid, and rendered into the value."""
    over(full, seat("scribe", "harness: codex, skills: {add: [local-skill]}"))
    project = project_with(tmp_path)
    skill = project / ".agents" / "skills" / "local-skill" / "SKILL.md"
    skill.write_bytes(content.encode())
    args = ("--project-root", str(project), "--loadout", loadout(tmp_path, {}))
    assert ok(full, *args) == ""
    cell = resolved(full, *args)["scribe"]["skills"]
    assert cell == {"value": [RESOURCE, "local-skill"], "source": "default+override"}


def test_the_skill_check_cites_openrig() -> None:
    """P46: the ported rule names its source lines."""
    text = TOOL.read_text()
    for cite in ("skill-discovery.js:16", ":21-56", ":176"):
        assert cite in text, cite


def test_a_loadout_skill_still_resolves_on_claude_code(full: Path, tmp_path: Path) -> None:
    """P44: what OpenRig's loadout lists is found, wherever the project also keeps it."""
    over(full, seat("scribe", "skills: {add: [local-skill]}"))
    held = loadout(tmp_path, {"scribe": ["local-skill"]})
    args = ("--project-root", str(project_with(tmp_path)), "--loadout", held)
    assert ok(full, *args) == ""
    cell = resolved(full, *args)["scribe"]["skills"]
    assert cell == {"value": [RESOURCE, "local-skill"], "source": "default+override"}


def test_the_scan_roots_are_data_that_cite_openrig() -> None:
    """P44: one constant, with its source."""
    text = TOOL.read_text()
    assert "daemon/dist/domain/skill-discovery.js:191-211" in text
    assert text.count("OPENRIG_SKILL_ROOTS = {") == 1


@pytest.mark.parametrize("text", [None, "{", "[]", '{"scribe": "x"}', '{"scribe": [1]}'])
def test_a_missing_or_invalid_loadout_is_a_usage_error(
    full: Path, tmp_path: Path, text: str | None
) -> None:
    """Exit 2."""
    path = tmp_path / "loadout.json"
    if text is not None:
        path.write_text(text)
    for command in (("validate",), ("resolve", "--json")):
        proc = tool(full, *command, "--loadout", str(path))
        assert proc.returncode == 2 and "loadout" in proc.stderr, proc.stderr


def test_without_agent_yaml_skills_are_left_out_and_refused_when_set(conf: Path) -> None:
    """No agent.yaml: no skills or harness defaults; a layer setting skills is an error."""
    row = resolved(conf)["scribe"]
    assert "skills" not in row and "harness" not in row
    over(conf, seat("scribe", "skills: {add: [x]}"))
    refused(conf, "overrides.yml: seats.scribe.skills: skills: needs agents/hacf/agent.yaml in ")


@pytest.mark.parametrize(
    "value", ["[a]", "{add: a}", "{add: ['']}", "{add: [1]}", "{drop: [a]}", "null"]
)
def test_a_malformed_skills_value_is_refused(full: Path, value: str) -> None:
    """{add, remove} lists of names."""
    over(full, seat("scribe", f"skills: {value}"))
    refused(full, "overrides.yml: seats.scribe.skills")


def test_an_unreadable_agent_yaml_is_a_problem(full: Path) -> None:
    """A broken agent.yaml is refused, not skipped."""
    (full / AGENT).write_text("profiles: [\n")
    refused(full, "agents/hacf/agent.yaml: -: not valid YAML")


# MR 5


def local_file(directory: Path, name: str = "x.md", size: int = 10) -> Path:
    """A file of `size` bytes under agents/hacf/local/."""
    path = directory / "agents" / "hacf" / "local" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"a" * size)
    return path


def test_instructions_default_to_empty_and_the_highest_layer_replaces(conf: Path) -> None:
    """No merge: the override's list replaces the preset's."""
    assert resolved(conf)["lead"]["instructions"] == {"value": [], "source": "default"}
    local_file(conf)
    local_file(conf, "sub/y.md", 65536)
    use_preset(conf, seat("lead", "instructions: [local/x.md]"))
    row = resolved(conf)["lead"]["instructions"]
    assert row == {"value": ["local/x.md"], "source": "preset:p"}
    over(conf, seat("lead", "instructions: [local/sub/y.md]"))
    row = resolved(conf)["lead"]["instructions"]
    assert row == {"value": ["local/sub/y.md"], "source": "override"}


@pytest.mark.parametrize(
    "entry",
    [
        "local/missing.md",
        "local/link.md",
        "local/linked/x.md",
        "local/big.md",
        "local/sub",
        "../x.md",
        "local/../x.md",
        "/etc/hostname",
        "x.md",
        "local/",
    ],
)
def test_a_bad_instructions_entry_is_refused_by_name(conf: Path, entry: str) -> None:
    """Missing, a symlink, too large, a directory, or outside local/."""
    real = local_file(conf)
    local_file(conf, "big.md", 65537)
    local_file(conf, "sub/y.md")
    real.parent.joinpath("link.md").symlink_to(real)
    real.parent.joinpath("linked").symlink_to(real.parent, target_is_directory=True)
    (conf / "agents" / "hacf" / "x.md").write_text("outside\n")
    over(conf, seat("lead", f"instructions: ['{entry}']"))
    refused(conf, "overrides.yml: seats.lead.instructions: ")
    assert entry in tool(conf, "validate").stderr


def test_local_itself_may_not_be_a_symlink(conf: Path) -> None:
    """P43: every component from local down is checked, local included."""
    real = conf / "elsewhere"
    real.mkdir()
    (real / "x.md").write_bytes(b"a" * 10)
    (conf / "agents" / "hacf").mkdir(parents=True)
    (conf / "agents" / "hacf" / "local").symlink_to(real, target_is_directory=True)
    over(conf, seat("lead", "instructions: [local/x.md]"))
    refused(
        conf,
        "overrides.yml: seats.lead.instructions: local/x.md: is or passes through a symlink",
    )


@pytest.mark.parametrize("value", ["local/x.md", "[1]", "null"])
def test_instructions_is_a_list_of_strings(conf: Path, value: str) -> None:
    """Not a bare string."""
    over(conf, seat("lead", f"instructions: {value}"))
    refused(conf, "overrides.yml: seats.lead.instructions: ")


# MR 6


def test_harness_defaults_to_the_profile_runtime(full: Path) -> None:
    """Default from agent.yaml; the highest layer wins."""
    row = resolved(full)["lead"]["harness"]
    assert row == {"value": PROFILES["lead"]["preferences"]["runtime"], "source": "default"}
    use_preset(full, seat("lead", "harness: codex"))
    assert resolved(full)["lead"]["harness"] == {"value": "codex", "source": "preset:p"}
    over(full, seat("lead", "harness: pi"))
    assert resolved(full)["lead"]["harness"] == {"value": "pi", "source": "override"}


def test_a_community_harness_validates_and_is_deferred_by_name(conf: Path) -> None:
    """R161: agy resolves with its deferral text."""
    over(conf, seat("scribe", "harness: agy"))
    ok(conf)
    assert resolved(conf)["scribe"]["harness"] == {
        "value": "agy",
        "source": "override",
        "deferred": "harness agy for scribe: deferred in v0.6.7 (OpenRig 0.7.23 has no per-seat "
        "path for a community harness); not applied",
    }


@pytest.mark.parametrize("value", ["'Agy!'", "''", "-x", "3", "null", "a" * 64, '"a\\n"'])
def test_a_malformed_harness_is_refused(conf: Path, value: str) -> None:
    """^[a-z0-9][a-z0-9-]{0,62}$."""
    over(conf, seat("scribe", f"harness: {value}"))
    refused(conf, "overrides.yml: seats.scribe.harness: ")


# MR 7


def routing(directory: Path) -> dict:
    """The resolved routing section."""
    proc = tool(directory, "resolve", "--json", "--section", "routing")
    assert proc.returncode == 0, proc.stderr
    doc = json.loads(proc.stdout)
    assert list(doc) == ["routing"]
    return doc["routing"]


def unrouted(directory: Path) -> None:
    """Delete `routing` from the copied seat-config.yml: no routing anywhere."""
    path = directory / "seat-config.yml"
    doc = yaml.safe_load(path.read_text())
    doc.pop("routing", None)
    path.write_text(yaml.safe_dump(doc, sort_keys=False))


def test_us007_unset_routing_is_the_discovery_rule(conf: Path) -> None:
    """US-007: both destinations default to discover; tags and format are left out."""
    unrouted(conf)
    assert routing(conf) == {"question.destination": DISCOVER, "update.destination": DISCOVER}


def test_routing_precedence_is_per_key(conf: Path) -> None:
    """seat-config.yml < overrides.yml, key by key, with no deep merge."""
    unrouted(conf)
    with (conf / "seat-config.yml").open("a") as handle:
        handle.write(
            "routing:\n  question: {destination: {session: a@b}}\n"
            "  tags: {question: q, extra: [x]}\n"
            '  format: {options: "A/B/C", recommended: required}\n'
        )
    over(
        conf,
        "routing:\n  update: {destination: {session: advisor-lead@kernel}}\n"
        "  tags: {answer: tj-answer}\n",
    )
    got = routing(conf)
    assert list(got) == ["question.destination", "update.destination", "tags", "format"]
    assert got["question.destination"] == {"value": {"session": "a@b"}, "source": "default"}
    assert got["update.destination"] == {
        "value": {"session": "advisor-lead@kernel"}, "source": "override"}  # fmt: skip
    assert got["tags"] == {"value": {"answer": "tj-answer"}, "source": "override"}
    assert got["format"] == {
        "value": {"options": "A/B/C", "recommended": "required"}, "source": "default"}  # fmt: skip


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("[a]", "routing: must be a mapping"),
        ("{ask: {}}", "routing.ask: unknown key"),
        ("{question: x}", "routing.question: must be a mapping"),
        ("{question: {to: x}}", "routing.question.to: unknown key"),
        ("{question: {destination: {rule: ask}}}", "routing.question.destination: "),
        ("{question: {destination: {session: x}}}", "routing.question.destination: "),
        ("{question: {destination: {session: 'a@b c'}}}", "routing.question.destination: "),
        ("{update: {destination: {rule: discover, session: a@b}}}", "routing.update.destination: "),
        ("{update: {destination: discover}}", "routing.update.destination: "),
        ("{tags: [a]}", "routing.tags: must be a mapping"),
        ("{tags: {other: a}}", "routing.tags.other: unknown key"),
        ("{tags: {question: 'A b'}}", "routing.tags.question: "),
        ("{tags: {extra: a}}", "routing.tags.extra: must be a list"),
        ("{tags: {extra: [A]}}", "routing.tags.extra: "),
        ("{format: {options: A/B, recommended: required}}", "routing.format: must be exactly"),
        ("{format: {options: A/B/C}}", "routing.format: must be exactly"),
    ],
)
def test_each_routing_refusal_names_the_key(conf: Path, body: str, expected: str) -> None:
    """One refusal each."""
    over(conf, f"routing: {body}\n")
    refused(conf, f"overrides.yml: {expected}")


# the shipped routing block (OPR.99.0.14.5, MR 3 and MR 4)

TAGS = {"value": SHIPPED_ROUTING["tags"], "source": "default"}
FORMAT = {"value": SHIPPED_ROUTING["format"], "source": "default"}


def test_the_shipped_kit_resolves_to_discovery_with_tags_and_format(conf: Path) -> None:
    """R155, R173: four keys, all source default."""
    assert routing(conf) == {
        "question.destination": DISCOVER,
        "update.destination": DISCOVER,
        "tags": TAGS,
        "format": FORMAT,
    }


def test_an_override_changes_one_routing_key_only(conf: Path) -> None:
    """A fixed session for questions; the other three keys stay default."""
    over(conf, 'routing: {question: {destination: {session: "advisor-lead@demo-rig"}}}\n')
    assert routing(conf) == {
        "question.destination": {
            "value": {"session": "advisor-lead@demo-rig"},
            "source": "override",
        },
        "update.destination": DISCOVER,
        "tags": TAGS,
        "format": FORMAT,
    }


def test_the_shipped_routing_holds_no_address() -> None:
    """No `@` and no `session` key anywhere in the shipped block."""
    block = yaml.safe_load((HACF / "seat-config.yml").read_text())["routing"]
    dumped = json.dumps(block)
    assert "@" not in dumped and '"session"' not in dumped


def test_resolve_routing_never_writes(conf: Path) -> None:
    """With an overrides.yml present, every file keeps its sha256."""
    over(conf, "routing: {question: {destination: {session: a@b}}}\n")
    before = snapshot(conf)
    routing(conf)
    assert snapshot(conf) == before


# MR 8


def test_the_sections_print_their_own_keys(full: Path) -> None:
    """No section prints seats and routing; seats and context only seats; routing only routing."""

    def doc(*args: str) -> dict:
        proc = tool(full, "resolve", "--json", *args)
        assert proc.returncode == 0, proc.stderr
        return json.loads(proc.stdout)

    seat_keys = ["model", "tools", "skills", "instructions", "harness"]
    context = ["cap_tokens", "behaviour", "reset_scope"]
    both = doc()
    assert list(both) == ["seats", "routing"]
    assert list(both["seats"]["lead"]) == [*seat_keys, *context]
    seats_only = doc("--section", "seats")
    assert list(seats_only) == ["seats"] and list(seats_only["seats"]["lead"]) == seat_keys
    context_only = doc("--section", "context")
    assert list(context_only) == ["seats"] and list(context_only["seats"]["lead"]) == context
    assert list(doc("--section", "routing", "--role", "lead")) == ["routing"]
    assert tool(full, "resolve", "--json", "--section", "routing", "--role", "x").returncode == 2


# MR 10


def test_the_shipped_header_states_the_v2_rules() -> None:
    """Each key, the floor, the local dir and the deferred harness are in the comment."""
    text = (HACF / "seat-config.yml").read_text()
    header = " ".join(line.lstrip("# ") for line in text.splitlines() if line.startswith("#"))
    for needle in (
        "model", "effort", "tools", "skills", "instructions", "harness", "routing",
        "add_deny", "remove_deny", "AskUserQuestion", "codebase-memory-mcp",
        "agents/hacf/local/", "claude-code, codex, pi", "deferred in v0.6.7",
        "default < preset < override",
    ):  # fmt: skip
        assert needle in header, needle


# MR 11


def snapshot(directory: Path) -> dict[str, str]:
    """Every path under directory with its sha256 (or link target)."""
    out = {}
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            out[str(path.relative_to(directory))] = "link " + str(path.readlink())
        elif path.is_file():
            out[str(path.relative_to(directory))] = hashlib.sha256(path.read_bytes()).hexdigest()
        else:
            out[str(path.relative_to(directory))] = "dir"
    return out


def test_no_command_writes_overrides_or_local(full: Path) -> None:
    """US-004: validate, every resolve section and every seat-rules mode change nothing."""
    shutil.copy(HACF / "rig.yaml", full / "rig.yaml")
    local_file(full)
    over(
        full,
        seat(
            "execute",
            "tools: {remove_deny: [Monitor]}, instructions: [local/x.md], "
            "skills: {add: [mystery]}, harness: agy",
        ),
    )
    before = snapshot(full)
    commands = [("validate",), ("resolve", "--json")]
    commands += [("resolve", "--json", "--section", s) for s in ("seats", "context", "routing")]
    for command in commands:
        assert tool(full, *command).returncode == 0, command
    rules = HACF / "bin" / "seat-rules.py"
    for mode in (("--role", "execute"), ("--all",), ("--check",)):
        argv = [sys.executable, str(rules), "--roles", str(full / "roles.yml"), *mode]
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=60, check=False)
        assert proc.returncode == 0, proc.stdout + proc.stderr
    assert snapshot(full) == before
