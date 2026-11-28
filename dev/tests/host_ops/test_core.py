"""Tests of hacf-host.py's core (OPR.99.0.16.1): state, config, tiers, tickets, adapter, status."""

from __future__ import annotations

import fcntl
import json
import os
import stat
import subprocess
import sys
import threading
import time
from argparse import Namespace
from pathlib import Path

import pytest
import yaml

from .conftest import EXAMPLE, REPO_ROOT, TOOL

SKEW_LINE = "host state is schema v2; this hacf-host is v1: newer HACF owns it; waiting"


def cli(*args: str, **env: str) -> subprocess.CompletedProcess:
    """Run the script as a user does, with the isolated environment."""
    return subprocess.run(
        [sys.executable, str(TOOL), *args],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", **env},
        timeout=60,
        check=False,
    )


def tree(root: Path) -> dict[str, tuple[bytes, int]]:
    """Every file under `root`: its bytes and mtime."""
    if not root.exists():
        return {}
    return {
        str(p.relative_to(root)): (p.read_bytes(), p.stat().st_mtime_ns)
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def put(path: Path, data: object) -> None:
    """Write `data` as JSON to `path`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


# --------------------------------------------------------------------------------------- script


def test_script_is_executable_with_python3_shebang() -> None:
    """The script is an executable python3 program (MR 1).

    The exact mode is not asserted: git records only the executable bit, and a checkout's
    other bits follow the user's umask (0755 under 022, 0775 under 002, 0700 under 077).
    """
    assert TOOL.read_text(encoding="utf-8").startswith("#!/usr/bin/env python3\n")
    staged = subprocess.run(
        ["git", "ls-files", "--stage", "--", str(TOOL.relative_to(REPO_ROOT))],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    assert staged.stdout.split()[:1] == ["100755"]
    assert TOOL.stat().st_mode & stat.S_IXUSR


@pytest.mark.parametrize(
    "argv", [[], ["bogus"], ["config"], ["job"], ["config", "check", "--bogus"]]
)
def test_usage_error(argv: list[str]) -> None:
    """A usage error is exit 2, and is not reported as an unbuilt verb."""
    done = cli(*argv)
    assert done.returncode == 2
    assert "not in this build" not in done.stderr


def test_help_names_the_state_dir_config_and_exit_codes() -> None:
    """--help shows the module docstring's facts."""
    done = cli("--help")
    assert done.returncode == 0
    for word in ("HACF_HOST_STATE_DIR", "host.yml", "Exit codes", "75"):
        assert word in done.stdout


# ---------------------------------------------------------------------------------------- state


def test_state_dir_resolution(tool, tmp_path: Path) -> None:
    """The override wins; XDG_STATE_HOME counts only when absolute."""
    home = {"HOME": str(tmp_path / "h")}
    assert tool.state_dir({"HACF_HOST_STATE_DIR": "/x/y", **home}) == Path("/x/y")
    assert tool.state_dir({"XDG_STATE_HOME": "/s", **home}) == Path("/s/hacf/host")
    fallback = tmp_path / "h" / ".local" / "state" / "hacf" / "host"
    assert tool.state_dir({"XDG_STATE_HOME": "relative", **home}) == fallback
    assert tool.state_dir(home) == fallback


def test_write_is_atomic_locked_and_carries_the_schema(tool, host, monkeypatch) -> None:
    """A temp file in the same dir replaces the target under the lock."""
    seen: list[tuple[Path, Path]] = []
    real = tool.os.replace

    def replace(src, dst) -> None:
        # The lock is held while the file is moved into place.
        with open(host.state / "state.lock") as other, pytest.raises(BlockingIOError):
            fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
        seen.append((Path(src), Path(dst)))
        real(src, dst)

    monkeypatch.setattr(tool.os, "replace", replace)
    tool.update_state("rigs/a.json", lambda d: d.update(state="running"))
    target = host.state / "rigs" / "a.json"
    assert json.loads(target.read_text()) == {"schema": "hacf.host/v1", "state": "running"}
    [(src, dst)] = seen
    assert dst == target and src.parent == target.parent
    assert sorted(p.name for p in target.parent.iterdir()) == ["a.json"]


def test_failed_write_keeps_the_old_file(tool, host, monkeypatch) -> None:
    """A failed replace leaves the old content and no temp file."""
    tool.update_state("supervisor.json", lambda d: d.update(version="1"))
    before = tree(host.state)

    def fail(src, dst) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(tool.os, "replace", fail)
    with pytest.raises(OSError):
        tool.update_state("supervisor.json", lambda d: d.update(version="2"))
    assert tree(host.state) == before


def test_update_keeps_unknown_keys(tool, host) -> None:
    """Read-modify-write keeps a key this version does not know."""
    path = host.state / "rigs" / "a.json"
    put(path, {"schema": "hacf.host/v1", "from_a_later_minor": {"x": 1}, "state": "running"})
    tool.update_state("rigs/a.json", lambda d: d.update(state="parked-waiting"))
    assert json.loads(path.read_text()) == {
        "schema": "hacf.host/v1",
        "from_a_later_minor": {"x": 1},
        "state": "parked-waiting",
    }


def test_writers_wait_for_the_lock(tool, host) -> None:
    """An update blocks while another process holds state.lock."""
    host.state.mkdir(parents=True)
    done = threading.Event()
    with open(host.state / "state.lock", "w") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        writer = threading.Thread(
            target=lambda: (tool.update_state("a.json", lambda d: None), done.set())
        )
        writer.start()
        assert not done.wait(0.3)
        assert not (host.state / "a.json").exists()
        fcntl.flock(held, fcntl.LOCK_UN)
    writer.join(timeout=10)
    assert done.is_set()


@pytest.mark.parametrize("text", ["{not json", "[1]", '{"state": "x"}', '{"schema": "other/v1"}'])
def test_update_refuses_a_file_it_cannot_read(tool, host, text: str) -> None:
    """A file that is not a v1 state object is never overwritten."""
    path = host.state / "a.json"
    path.parent.mkdir(parents=True)
    path.write_text(text)
    with pytest.raises(tool.StateError):
        tool.update_state("a.json", lambda d: d.update(state="running"))
    assert path.read_text() == text


@pytest.mark.parametrize("name", ["../out.json", "/abs.json", "rigs/../../out.json"])
def test_update_stays_inside_the_state_dir(tool, host, name: str) -> None:
    """A name that leaves the state dir is refused."""
    with pytest.raises(tool.StateError):
        tool.update_state(name, lambda d: None)
    assert not (host.base / "out.json").exists()


@pytest.mark.parametrize("newer", ["a.json", "rigs/b.json", "queue/t1.json", "idle-point.json"])
def test_skew_writes_nothing(tool, host, newer: str) -> None:
    """A newer file anywhere in the state dir stops every write."""
    put(host.state / newer, {"schema": "hacf.host/v2", "x": 1})
    put(host.state / "supervisor.json", {"schema": "hacf.host/v1"})
    before = tree(host.state)
    for name in (newer, "supervisor.json", "rigs/new.json"):
        with pytest.raises(tool.NewerSchema) as caught:
            tool.update_state(name, lambda d: d.update(state="running"))
        assert str(caught.value) == SKEW_LINE
    # Not even the lock file.
    assert tree(host.state) == before


def test_skew_seen_after_the_lock_writes_nothing(tool, host, monkeypatch) -> None:
    """A newer client writes while this one waits for the lock."""
    real = tool.fcntl.flock

    def flock(fd, op) -> None:
        real(fd, op)
        if op == fcntl.LOCK_EX:
            put(host.state / "a.json", {"schema": "hacf.host/v2"})

    monkeypatch.setattr(tool.fcntl, "flock", flock)
    with pytest.raises(tool.NewerSchema):
        tool.update_state("b.json", lambda d: None)
    assert not (host.state / "b.json").exists()


def test_mutating_verb_exits_75_on_skew(tool, host, monkeypatch, capsys) -> None:
    """A stand-in handler takes the place of `resume`, so only the state write is judged."""
    put(host.state / "rigs" / "a.json", {"schema": "hacf.host/v2"})
    before = tree(host.state)
    monkeypatch.setitem(
        tool.HANDLERS,
        "resume",
        lambda args: tool.update_state("rigs/a.json", lambda d: d.update(state="running")),
    )
    assert tool.main(["resume", "a"]) == 75
    assert capsys.readouterr().err == SKEW_LINE + "\n"
    assert tree(host.state) == before


# --------------------------------------------------------------------------------------- config


def test_missing_config_is_all_defaults(tool, host) -> None:
    """No file means every default and a passing check."""
    config, problems = tool.load_config(host.config)
    assert problems == []
    assert config == {
        "priority": [],
        "projects": {},
        "ignore_rigs": [],
        "reserve_mb": 2048,
        "reserve_after_minutes": 10,
        "seat_ram_mb": 330,
        "foreign_heavy_patterns": [],
        "paused_files": [],
        "human": {"destinations": [], "tags": []},
        "autopark": {
            "enabled": False,
            "grace_minutes": 15,
            "max_parks_per_day": 3,
            "fyi_tags": ["hacf:update"],
            "restore_timeout_s": 900,
        },
    }
    done = cli("config", "check")
    assert done.returncode == 0, done.stderr


def test_config_path_resolution(tool, tmp_path: Path) -> None:
    """XDG_CONFIG_HOME counts only when absolute."""
    home = {"HOME": str(tmp_path)}
    assert tool.config_path({"XDG_CONFIG_HOME": "/c", **home}) == Path("/c/hacf/host.yml")
    fallback = tmp_path / ".config" / "hacf" / "host.yml"
    assert tool.config_path({"XDG_CONFIG_HOME": "rel", **home}) == fallback
    assert tool.config_path(home) == fallback


def test_a_full_valid_config(tool, host) -> None:
    """Valid keys are taken; unset sub-keys keep their defaults."""
    root = host.project("one")
    host.write_config(
        f"""
schema: hacf.host-config/v1
priority: [a, [b, c], d]
projects: {{a: {root}}}
ignore_rigs: [k]
reserve_mb: 1000
foreign_heavy_patterns: ['train\\.py']
paused_files: [~/paused.txt, /var/paused]
human: {{destinations: [q@r]}}
autopark: {{enabled: true, grace_minutes: 1}}
"""
    )
    config, problems = tool.load_config(host.config)
    assert problems == []
    assert config["projects"] == {"a": str(root)}
    assert config["human"] == {"destinations": ["q@r"], "tags": []}
    assert config["autopark"]["enabled"] is True
    assert config["autopark"]["grace_minutes"] == 1
    assert config["autopark"]["max_parks_per_day"] == 3
    done = cli("config", "check")
    assert done.returncode == 0, done.stderr


PROBLEMS = [
    ("bogus: 1", "bogus: unknown key"),
    ("autopark: {trigger: x}", "autopark.trigger: unknown key"),
    ("human: {people: []}", "human.people: unknown key"),
    ("schema: hacf.host-config/v2", "schema: expected hacf.host-config/v1"),
    ("priority: a", "priority: expected a list"),
    ("priority: [1]", "priority[0]: expected a rig name or a list of rig names"),
    ("priority: [[a, [b]]]", "priority[0][1]: expected a rig name"),
    ("priority: ['']", "priority[0]: expected a rig name or a list of rig names"),
    ("priority: [a, a]", "priority[1]: rig a is named twice in priority"),
    ("priority: [a, [b, a]]", "priority[1][1]: rig a is named twice in priority"),
    ("projects: [a]", "projects: expected a mapping"),
    ("projects:", "projects: expected a mapping"),
    ("projects: {1: /x}", "projects.1: expected a rig name"),
    ("projects: {a: 1}", "projects.a: expected a project root path"),
    ("projects: {a: rel/dir}", "projects.a: the project root is not absolute"),
    ("projects: {a: ~/p}", "projects.a: the project root is not absolute"),
    ("projects: {a: /nonexistent-hacf-root}", "projects.a: the project root does not exist"),
    ("ignore_rigs: k", "ignore_rigs: expected a list"),
    ("ignore_rigs: [1]", "ignore_rigs[0]: expected a string"),
    ("reserve_mb: lots", "reserve_mb: expected an integer"),
    ("seat_ram_mb: true", "seat_ram_mb: expected an integer"),
    ("reserve_after_minutes: 0", "reserve_after_minutes: expected a positive integer"),
    ("reserve_after_minutes: 1.5", "reserve_after_minutes: expected a positive integer"),
    ("autopark: {grace_minutes: -1}", "autopark.grace_minutes: expected a positive integer"),
    ("autopark: {max_parks_per_day: 0}", "autopark.max_parks_per_day: expected a positive integer"),
    (
        "autopark: {restore_timeout_s: '9'}",
        "autopark.restore_timeout_s: expected a positive integer",
    ),
    ("autopark: {enabled: 'yes please'}", "autopark.enabled: expected true or false"),
    ("autopark: {fyi_tags: x}", "autopark.fyi_tags: expected a list"),
    ("autopark: []", "autopark: expected a mapping"),
    ("human: {tags: [2]}", "human.tags[0]: expected a string"),
    ("foreign_heavy_patterns: ['(']", "foreign_heavy_patterns[0]: not a regular expression"),
    ("paused_files: [rel.txt]", "paused_files[0]: not an absolute path"),
    ("- a", "(top level): expected a mapping"),
    ("a: [", "(top level): not valid YAML"),
]


@pytest.mark.parametrize(("text", "line"), PROBLEMS)
def test_each_problem_exits_1_with_its_line(host, text: str, line: str) -> None:
    """One bad key gives exit 1 and exactly its line."""
    host.write_config(text + "\n")
    done = cli("config", "check")
    assert done.returncode == 1, done.stderr
    lines = done.stderr.splitlines()
    assert len(lines) == 1, lines
    assert lines[0].startswith(f"host.yml: {line}"), lines


def test_root_without_missions_dir(host) -> None:
    """A bound root must hold missions/."""
    root = host.base / "bare"
    root.mkdir()
    host.write_config(f"projects: {{a: {root}}}\n")
    done = cli("config", "check")
    assert done.returncode == 1
    assert done.stderr == "host.yml: projects.a: the project root has no missions/ dir\n"


def test_rig_both_mapped_and_ignored(host) -> None:
    """A rig cannot be bound and ignored."""
    root = host.project("one")
    host.write_config(f"projects: {{a: {root}}}\nignore_rigs: [a]\n")
    done = cli("config", "check")
    assert done.returncode == 1
    assert done.stderr == "host.yml: ignore_rigs[0]: rig a is in both projects and ignore_rigs\n"


def test_unreadable_config_is_a_problem(host) -> None:
    """A config that cannot be read fails the check."""
    host.config.mkdir(parents=True)
    done = cli("config", "check")
    assert done.returncode == 1
    assert done.stderr.startswith("host.yml: (top level): cannot be read")


def test_every_problem_gets_one_line(host) -> None:
    """Several problems give one line each, even for a key with a newline."""
    host.write_config('bogus: 1\nreserve_mb: x\n"two\\nlines": 1\n')
    done = cli("config", "check")
    assert done.returncode == 1
    assert len(done.stderr.splitlines()) == 3


def test_invalid_keys_fall_back_to_their_defaults(tool, host) -> None:
    """An invalid value never reaches the resolved config."""
    host.write_config("reserve_mb: x\nignore_rigs: [k]\nautopark: {grace_minutes: 0}\n")
    config, problems = tool.load_config(host.config)
    assert len(problems) == 2
    assert config["reserve_mb"] == 2048
    assert config["autopark"]["grace_minutes"] == 15
    assert config["ignore_rigs"] == ["k"]


def test_unmapped_running_rig_warns(host) -> None:
    """Running and partly running unmapped rigs warn; exit stays 0."""
    root = host.project("one")
    host.write_config(f"projects: {{mapped: {root}}}\nignore_rigs: [ignored]\n")
    host.rig.reply(
        "ps --json",
        [
            {"name": "mapped", "status": "running"},
            {"name": "ignored", "status": "running"},
            {"name": "loose", "status": "running"},
            {"name": "half", "status": "partial"},
            {"name": "down", "status": "stopped"},
        ],
    )
    done = cli("config", "check")
    assert done.returncode == 0, done.stderr
    tail = "is unmapped: the idle point counts it in flight and it is never parked"
    assert done.stderr.splitlines() == [
        f"warning: running rig half {tail}",
        f"warning: running rig loose {tail}",
    ]
    assert host.rig.calls == [["ps", "--json"]]


def test_warnings_do_not_hide_problems(host) -> None:
    """Problems come first and decide the exit code."""
    host.write_config("bogus: 1\n")
    host.rig.running("loose")
    done = cli("config", "check")
    assert done.returncode == 1
    assert done.stderr.splitlines()[0] == "host.yml: bogus: unknown key"
    assert done.stderr.splitlines()[1].startswith("warning: running rig loose ")


@pytest.mark.parametrize(
    "reply",
    [
        {"stdout": "[]", "rc": 3, "stderr": "daemon down\n"},
        {"stdout": "not json"},
        {"stdout": '{"entries": []}'},
        {"stdout": "[1]"},
    ],
)
def test_failed_rig_ps_is_a_warning(host, reply: dict) -> None:
    """An unusable `rig ps` answer is a warning, not an error."""
    host.rig.reply("ps --json", **reply)
    done = cli("config", "check")
    assert done.returncode == 0, done.stderr
    [line] = done.stderr.splitlines()
    assert line.startswith("warning: rig ps failed")


def test_missing_rig_binary_is_a_warning(host) -> None:
    """No `rig` at all is a warning too."""
    done = cli("config", "check", HACF_HOST_RIG=str(host.base / "no-such-rig"))
    assert done.returncode == 0, done.stderr
    assert done.stderr.startswith("warning: rig ps failed")


def test_config_check_json(host) -> None:
    """--json carries the problems and warnings."""
    host.write_config("bogus: 1\n")
    host.rig.running("loose")
    done = cli("config", "check", "--json")
    assert done.returncode == 1
    report = json.loads(done.stdout)
    assert report["ok"] is False
    assert report["path"] == str(host.config)
    assert report["problems"] == ["host.yml: bogus: unknown key"]
    assert report["warnings"][0].startswith("warning: running rig loose ")


def test_nothing_writes_the_config_or_state(host) -> None:
    """The built verbs are read-only."""
    host.write_config("ignore_rigs: [k]\n")
    before = tree(host.config.parent)
    for argv in (["config", "check"], ["status", "--json"]):
        assert cli(*argv).returncode == 0
    assert tree(host.config.parent) == before
    assert not host.state.exists()


# ------------------------------------------------------------------------------- tiers, tickets


def test_tiers(tool) -> None:
    """Tier index; unlisted rigs and None share the lowest."""
    priority = ["a", ["b", "c"], "d"]
    assert [tool.tier_of(r, priority) for r in "abcd"] == [0, 1, 1, 2]
    assert tool.tier_of("unlisted", priority) == 3
    assert tool.tier_of(None, priority) == 3
    assert tool.tier_of("a", []) == tool.tier_of(None, []) == tool.tier_of("z", []) == 0


@pytest.mark.parametrize(
    ("flag", "session", "expected"),
    [
        ("given", "seat@env-rig", "given"),
        (None, "build-execute@env-rig.2", "env-rig.2"),
        (None, None, None),
        (None, "no-at-sign", None),
        (None, "a@b@c", None),
        (None, "seat@", None),
        (None, "@rig", None),
        (None, "seat@my rig", None),
        (None, "seat@rig\n", None),
    ],
)
def test_job_rig(tool, flag, session, expected) -> None:
    """--rig, else the rig part of a well-formed session name."""
    env = {} if session is None else {"OPENRIG_SESSION_NAME": session}
    assert tool.job_rig(Namespace(rig=flag), env) == expected


# -------------------------------------------------------------------------------------- adapter


def test_adapter(tool, host) -> None:
    """Argv in, (rc, stdout, stderr) or parsed JSON out."""
    host.rig.reply("a b", "out", rc=4, stderr="err")
    assert tool.rig("a", "b", timeout=30) == (4, "out", "err")
    host.rig.reply("c", {"x": 1})
    assert tool.json_rig("c", timeout=30) == {"x": 1}
    host.rig.reply("d", "{broken")
    assert tool.json_rig("d", timeout=30) is None
    assert host.rig.calls == [["a", "b"], ["c"], ["d"]]


def test_adapter_uses_rig_from_path_without_the_override(tool, host, monkeypatch) -> None:
    """Without HACF_HOST_RIG, `rig` comes from PATH."""
    monkeypatch.delenv("HACF_HOST_RIG")
    monkeypatch.setenv("PATH", str(host.rig.base))
    host.rig.reply("x", "found")
    assert tool.rig("x", timeout=30)[:2] == (0, "found")


def test_adapter_missing_binary(tool, host, monkeypatch) -> None:
    """A binary that cannot run is exit 127."""
    monkeypatch.setenv("HACF_HOST_RIG", str(host.base / "no-such-rig"))
    rc, out, err = tool.rig("ps", timeout=5)
    assert (rc, out) == (127, "")
    assert "no-such-rig" in err


def test_adapter_timeout_ends_the_whole_command(tool, host, monkeypatch) -> None:
    """A `rig` whose own child holds the pipes still returns at the timeout."""
    slow = host.base / "slow-rig"
    slow.write_text("#!/bin/sh\necho partial\nsleep 30\necho never\n")
    slow.chmod(0o755)
    monkeypatch.setenv("HACF_HOST_RIG", str(slow))
    started = time.monotonic()
    rc, out, err = tool.rig("ps", timeout=1)
    assert time.monotonic() - started < 15
    assert rc == 124
    assert "never" not in out
    assert "timed out" in err


# --------------------------------------------------------------------------------------- status


def test_status_json_skeleton(host) -> None:
    """The skeleton's keys and one row per configured or recorded rig."""
    root = host.project("one")
    host.write_config(f"priority: [top, mapped]\nprojects: {{mapped: {root}}}\nignore_rigs: [k]\n")
    put(host.state / "rigs" / "seen.json", {"schema": "hacf.host/v1"})
    done = cli("status", "--json")
    assert done.returncode == 0, done.stderr
    report = json.loads(done.stdout)
    assert sorted(report) == [
        "blocked_head",
        "config",
        "foreign_heavy",
        "generated_at",
        "holder",
        "idle_point",
        "locked",
        "mem_available_mb",
        "newer_schema_files",
        "park_log",
        "queue",
        "reserved_for",
        "rig_ps_error",
        "rigs",
        "schema",
        "state_dir",
        "supervisor",
    ]
    assert report["schema"] == "hacf.host-status/v1"
    assert report["state_dir"] == str(host.state)
    assert report["newer_schema_files"] == []
    assert report["config"]["reserve_mb"] == 2048
    rows = {r["name"]: r for r in report["rigs"]}

    def row(name: str, tier: int, state: str, project: str | None = None) -> dict:
        return {
            "name": name,
            "mapped": project is not None,
            "project": project,
            "ignored": state == "ignored",
            "tier": tier,
            "state": state,
            "since": None,
            "reason": reason if name == "seen" else None,
            "waits_on": [],
            "parks_24h": None if name == "seen" else 0,
            "last_restore_s": None,
            "ps_status": "not listed",
        }

    reason = f"unreadable record: {host.state / 'rigs' / 'seen.json'}"
    assert rows == {
        "k": row("k", 2, "ignored"),
        "mapped": row("mapped", 1, "running", str(root)),
        "seen": row("seen", 2, "unmapped"),
        "top": row("top", 0, "unmapped"),
    }
    # `status` asks `rig` for the rig list and nothing else.
    assert host.rig.calls == [["ps", "--json"]]


def test_status_prints_what_it_can_and_marks_newer_files(host) -> None:
    """Status still prints under skew, and writes nothing."""
    host.write_config("ignore_rigs: [k]\nbogus: 1\n")
    put(host.state / "rigs" / "new.json", {"schema": "hacf.host/v3"})
    put(host.state / "supervisor.json", {"schema": "hacf.host/v2"})
    (host.state / "idle-point.json").write_text("{broken")
    before = tree(host.state)
    done = cli("status", "--json")
    assert done.returncode == 0, done.stderr
    report = json.loads(done.stdout)
    assert report["newer_schema_files"] == ["rigs/new.json", "supervisor.json"]
    assert [r["name"] for r in report["rigs"]] == ["k", "new"]
    assert "rigs/new.json: newer schema" in done.stderr
    assert "host.yml: bogus: unknown key" in done.stderr
    assert tree(host.state) == before


# -------------------------------------------------------------------------------------- example


def test_example_config(tool, host) -> None:
    """The example parses to the defaults, passes the check and names no home."""
    text = EXAMPLE.read_text(encoding="utf-8")
    for root in ("/" + "home/", "/" + "Users/"):  # built here: this file must hold neither
        assert root not in text
    data = yaml.safe_load(text)
    assert data.pop("schema") == "hacf.host-config/v1"
    defaults = tool.load_config(host.config)[0]
    assert data == defaults  # every key, at its default
    config, problems = tool.load_config(EXAMPLE)
    assert (config, problems) == (defaults, [])
    host.write_config(text)
    done = cli("config", "check")
    assert done.returncode == 0, done.stderr


def test_example_comments_every_key() -> None:
    """Each key has a comment; placeholders only."""
    lines = EXAMPLE.read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and ":" in stripped:
            above = lines[i - 1].strip() if i else ""
            assert "#" in stripped or above.startswith("#"), line
    assert "my-project-hacf" in "\n".join(lines)
    assert "/path/to/project" in "\n".join(lines)
