"""P98: the advisor check on fixture transcripts, argv and settings, one test per case."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import run_arm

from .fakes_support import record


def seat(tmp: Path, argv: list[str] | None = None, setting: bool = False, calls: int = 0):
    """A seat's argv, settings files and transcript."""
    cwd = tmp / "copy" / ".hacf-exec"
    (cwd / ".claude").mkdir(parents=True, exist_ok=True)
    local = cwd / ".claude" / "settings.local.json"
    local.write_text(json.dumps({"advisorModel": "x"} if setting else {}))
    transcript = tmp / "s.jsonl"
    lines = [record("claude-sonnet-5-5", advisor_call=i < calls, uid=f"m{i}") for i in range(3)]
    transcript.write_text("".join(json.dumps(line) + "\n" for line in lines))
    config = run_arm.advisor_configured(["claude", *(argv or [])], cwd, tmp / "copy", [])
    activity, field = run_arm.advisor_activity([transcript])
    return config, activity, field


def test_the_record_field_is_not_evidence(tmp_path: Path) -> None:
    """The field on every record, with no config and no activity, is absent."""
    config, activity, field = seat(tmp_path)
    assert (config, activity, field) == (False, 0, 3)
    assert run_arm.advisor_state(config, activity) == "absent"


@pytest.mark.parametrize("how", [{"setting": True}, {"argv": ["--advisor", "opus"]}])
def test_config_and_activity_is_present(tmp_path: Path, how: dict) -> None:
    """Config (the folder's key, or the argv) plus one advisor iteration is present."""
    config, activity, _field = seat(tmp_path, calls=1, **how)
    assert run_arm.advisor_state(config, activity) == "present"


def test_config_without_activity_is_unused(tmp_path: Path) -> None:
    """Configured and never called."""
    config, activity, _field = seat(tmp_path, setting=True)
    assert run_arm.advisor_state(config, activity) == "advisor-unused"


def test_activity_without_config_is_mixed(tmp_path: Path) -> None:
    """Called without being configured."""
    config, activity, _field = seat(tmp_path, calls=2)
    assert activity == 2 and run_arm.advisor_state(config, activity) == "mixed"


def test_advisor_tool_use_counts(tmp_path: Path) -> None:
    """An advisor tool use is activity too."""
    entry = record("claude-sonnet-5-5", field=False)
    entry["message"]["content"] = [{"type": "server_tool_use", "name": "advisor", "id": "t1"}]
    path = tmp_path / "t.jsonl"
    path.write_text(json.dumps(entry) + "\n")
    assert run_arm.advisor_activity([path]) == (1, 0)


@pytest.mark.parametrize(
    "argv",
    [
        ["--settings", '{"advisorModel": "x"}'],
        ["--settings={file}"],
        ["--settings", "{file}"],
        ["--settings", "/nonexistent.json"],
    ],
)
def test_settings_argument(tmp_path: Path, argv: list[str]) -> None:
    """--settings, inline or a file, configures; an unreadable one is unknown, never absent."""
    named = tmp_path / "named.json"
    named.write_text('{"advisorModel": "x"}')
    words = [word.replace("{file}", str(named)) for word in argv]
    got = run_arm.advisor_configured(["claude", *words], tmp_path, tmp_path, [])
    assert got is (None if "/nonexistent.json" in words else True)


def test_parents_and_user_settings(tmp_path: Path) -> None:
    """A key in a parent up to the copy, or in a guarded user file, configures."""
    copy = tmp_path / "copy"
    (copy / ".claude").mkdir(parents=True)
    (copy / "sub").mkdir()
    user = tmp_path / "user.json"
    user.write_text("{}")
    assert run_arm.advisor_configured(["claude"], copy / "sub", copy, [user]) is False
    (copy / ".claude" / "settings.json").write_text('{"advisorModel": "x"}')
    assert run_arm.advisor_configured(["claude"], copy / "sub", copy, [user]) is True
    (copy / ".claude" / "settings.json").write_text("{}")
    user.write_text('{"advisorModel": "x"}')
    assert run_arm.advisor_configured(["claude"], copy / "sub", copy, [user]) is True
    user.write_text("not json")
    assert run_arm.advisor_configured(["claude"], copy / "sub", copy, [user]) is None
