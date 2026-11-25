"""openrig/hacf/bin/seat-config.py validates and resolves the per-seat config (OPR.99.0.11.6).

Every run reads files in tmp_path (a copy of the shipped roles.yml, caps.yml and seat-config.yml,
plus an overrides.yml or preset a test writes); nothing is written inside the HACF checkout, and
the tool itself writes nothing anywhere.
"""

from __future__ import annotations

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
        "schema: hacf.seat-preset/v1\nseats:\n  audit:\n    model: claude-sonnet-5-5\n"
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
        ("seats:\n  lead:\n    skills: [a]\n", "overrides.yml: seats.lead.skills: reserved key"),
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
            "schema: hacf.seat-overrides/v2\nseats: {}\n",
            "overrides.yml: schema: must be hacf.seat-overrides/v1",
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
    """(d) seat-config.yml, caps.yml and a preset with a schema other than v1."""
    original = {n: (conf / n).read_text() for n in ("seat-config.yml", "caps.yml")}
    (conf / "seat-config.yml").write_text(
        original["seat-config.yml"].replace("hacf.seat-config/v1", "hacf.seat-config/v9")
    )
    refused(conf, "seat-config.yml: schema: must be hacf.seat-config/v1")
    (conf / "seat-config.yml").write_text(original["seat-config.yml"])
    (conf / "caps.yml").write_text(original["caps.yml"].replace("hacf.caps/v1", "hacf.caps/v0"))
    refused(conf, "caps.yml: schema: must be hacf.caps/v1")
    (conf / "caps.yml").write_text(original["caps.yml"])
    (conf / "presets").mkdir()
    (conf / "presets" / "p.yml").write_text("schema: hacf.seat-preset/v2\nseats: {}\n")
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
    """(e) R89: an absent schema means v1; the one-line revert override is valid."""
    (conf / "overrides.yml").write_text("seats:\n  scribe:\n    model: claude-opus-5-5\n")
    assert tool(conf, "validate").returncode == 0
    (conf / "overrides.yml").write_text(
        "schema: hacf.seat-overrides/v1\nseats:\n  scribe:\n    model: claude-opus-5-5\n"
    )
    assert tool(conf, "validate").returncode == 0


def test_role_and_section_filters_and_usage(conf: Path) -> None:
    """--role and --section narrow the output; a bad role or section is a usage error (2)."""
    seats = resolved(conf, "--role", "audit", "--section", "seats")
    assert list(seats) == ["audit"] and list(seats["audit"]) == ["model"]
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
    assert doc["schema"] == "hacf.seat-config/v1" and doc["preset"] is None
    assert doc["sections"] == {"context": "caps.yml", "tools": "roles.yml"}
    assert doc["reserved"] == ["harness", "instructions", "skills", "tools", "mcp"]
    assert doc["seats"] == {role: expected_seat(role) for role in ROLES}
    assert not (HACF / "overrides.yml").exists() and not (HACF / "presets").exists()


def test_only_the_held_seats_ship_a_tiered_model() -> None:
    """(OPR.99.0.11.8 MR 6) the shipped seats off Opus are exactly the roles the note marks held."""
    doc = yaml.safe_load((HACF / "seat-config.yml").read_text())
    verdict = h5_verdict()
    off_opus = {role for role, value in doc["seats"].items() if value["model"] != OPUS}
    assert off_opus == {role for role, v in verdict.items() if v == "held"}
    for role in off_opus:
        assert doc["seats"][role] == TIERED[role]
