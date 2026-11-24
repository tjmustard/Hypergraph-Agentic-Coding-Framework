"""Tests of dev/telemetry/startup_probe.py (OPR.99.0.8.5), with a fake `claude` on PATH.

The fake records its argv, cwd, the probe dir's CLAUDE.local.md and the env keys it saw, then
prints canned stream-json. No real model call is made, and nothing is written outside tmp_path.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from conftest import REPO_ROOT, clean_env

TOOL = REPO_ROOT / "dev" / "telemetry" / "startup_probe.py"
RIG_DIR = REPO_ROOT / "openrig" / "hacf"
REPLY = "REPLY-TEXT-SENTINEL-41c9"
SECRET_BODY = "MANAGED-TEXT-SENTINEL-9b2e"
PROBE_LINE = "Probe: reply with the single word OK and call no tool."

FAKE = r"""#!/usr/bin/env python3
import json, os, sys
d = os.environ["FAKE_CLAUDE_DIR"]
if sys.argv[1:] == ["--version"]:
    print("9.9.9 (Fake Code)")
    sys.exit(0)
n = len([f for f in os.listdir(d) if f.startswith("call-")]) + 1
managed = open("CLAUDE.local.md").read()
json.dump({"argv": sys.argv[1:], "cwd": os.getcwd(), "managed": managed,
           "env": sorted(os.environ)}, open(os.path.join(d, "call-%d.json" % n), "w"))
code = int(open(os.path.join(d, "exit")).read()) if os.path.exists(os.path.join(d, "exit")) else 0
usages = json.load(open(os.path.join(d, "usages.json")))
u = usages[(n - 1) % len(usages)]
events = [
    {"type": "system", "subtype": "init", "tools": ["Bash", "Read", "mcp__x__y"],
     "mcp_servers": [{"name": "srv-a", "status": "connected"}],
     "slash_commands": ["hyper-execute", "loop"], "skills": ["loop", "dataviz"]},
    {"type": "assistant",
     "message": {"content": [{"type": "text", "text": "@@REPLY@@"}], "usage": u}},
    {"type": "result", "result": "@@REPLY@@", "usage": u},
]
if os.path.exists(os.path.join(d, "no-init")):
    events = events[1:]
for e in events:
    print(json.dumps(e))
sys.exit(code)
""".replace("@@REPLY@@", REPLY)


def managed_source(path: Path, culture: bool = True) -> Path:
    """A CLAUDE.local.md shaped like the real one: culture, start and onboarding blocks."""
    blocks = [("CULTURE-default.md", "default culture\n")]
    if culture:
        blocks.append(("CULTURE.md", f"rig culture {SECRET_BODY}\n"))
    blocks += [
        ("openrig-start.md", "start\n"),
        ("openrig-onboarding-01.md", "onboarding one\n"),
        ("openrig-onboarding-02.md", "onboarding two\n"),
    ]
    text = "\n".join(
        f"<!-- BEGIN OpenRig MANAGED BLOCK: {n} -->\n{b}<!-- END OpenRig MANAGED BLOCK: {n} -->\n"
        for n, b in blocks
    )
    path.write_text(text, encoding="utf-8")
    return path


class Probe:
    """A fake claude, a TMPDIR and a managed file under tmp_path."""

    def __init__(self, tmp: Path) -> None:
        """Lay out the fake and the default usage."""
        self.tmp = tmp
        self.fake = tmp / "fake"
        self.fake.mkdir()
        bin_dir = tmp / "bin"
        bin_dir.mkdir()
        (bin_dir / "claude").write_text(f"#!{sys.executable}\n" + FAKE.split("\n", 1)[1])
        (bin_dir / "claude").chmod(0o755)
        self.tmpdir = tmp / "probe-tmp"
        self.tmpdir.mkdir()
        self.managed = managed_source(tmp / "CLAUDE.local.md")
        self.out = tmp / "out.json"
        self.usages(
            [{"input_tokens": 3, "cache_creation_input_tokens": 100, "cache_read_input_tokens": 50}]
        )
        self.env = clean_env(
            PATH=os.pathsep.join([str(bin_dir), "/usr/bin", "/bin"]),
            TMPDIR=str(self.tmpdir),
            FAKE_CLAUDE_DIR=str(self.fake),
            OPENRIG_SESSION_NAME="build-execute@hacf",
            OPENRIG_NODE_ID="n-1",
            RIGGED_THING="x",
            CLAUDECODE="1",
        )

    def usages(self, items: list[dict]) -> None:
        """The usage objects successive runs report."""
        (self.fake / "usages.json").write_text(json.dumps(items))

    def run(self, *extra: str, role: str = "execute", env: dict | None = None):
        """Run the probe."""
        args = [
            sys.executable,
            str(TOOL),
            "--role",
            role,
            "--rig-dir",
            str(RIG_DIR),
            "--managed-file",
            str(self.managed),
            "--model",
            "m-test",
            "--out",
            str(self.out),
            *extra,
        ]
        e = dict(self.env) if env is None else env
        return subprocess.run(args, env=e, capture_output=True, text=True, timeout=120)

    def calls(self) -> list[dict]:
        """Each probe call the fake recorded, in order."""
        files = sorted(self.fake.glob("call-*.json"), key=lambda p: int(p.stem.split("-")[1]))
        return [json.loads(p.read_text()) for p in files]


@pytest.fixture
def probe(tmp_path: Path) -> Probe:
    """A fresh probe world."""
    return Probe(tmp_path)


def arg(argv: list[str], flag: str) -> str:
    """The value after a flag."""
    return argv[argv.index(flag) + 1]


def role_file(role: str) -> Path:
    """The profile's role file from agent.yaml."""
    agent = yaml.safe_load((RIG_DIR / "agents" / "hacf" / "agent.yaml").read_text())
    return RIG_DIR / "agents" / "hacf" / agent["profiles"][role]["startup"]["files"][0]["path"]


def expected_prompt(role: str, drop_protocol: bool = False) -> str:
    """The prompt the probe must send."""
    files = [RIG_DIR / "startup" / "context.md"]
    if not drop_protocol:
        files.append(RIG_DIR / "startup" / "protocol.md")
    if role == "lead":
        files.append(RIG_DIR / "startup" / "lead-first-move.md")
    files.append(role_file(role))
    return "\n\n".join(f.read_text().rstrip("\n") for f in files) + "\n\n" + PROBE_LINE


def test_argv_no_lever(probe: Probe) -> None:
    """The documented claude argv, the seat's deny list, the probe dir as cwd, no seat env."""
    result = probe.run()
    assert result.returncode == 0, result.stderr
    (call,) = probe.calls()
    argv = call["argv"]
    assert argv[0] == "-p" and argv[1] == expected_prompt("execute")
    assert argv[2:] == [
        "--model",
        "m-test",
        "--output-format",
        "stream-json",
        "--verbose",
        "--max-turns",
        "1",
        "--permission-mode",
        "bypassPermissions",
        "--settings",
        argv[-1],
    ]
    deny = json.loads(arg(argv, "--settings"))["permissions"]["deny"]
    rules = subprocess.run(
        [sys.executable, str(RIG_DIR / "bin" / "seat-rules.py"), "--role", "execute"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert deny == json.loads(rules.stdout)["deny"]
    assert Path(call["cwd"]).parent == probe.tmpdir.resolve()
    assert not [
        k for k in call["env"] if k.startswith(("OPENRIG_", "RIGGED_")) or k == "CLAUDECODE"
    ]
    assert "ENABLE_CLAUDEAI_MCP_SERVERS" not in call["env"]
    assert call["managed"] == probe.managed.read_text()


def test_lead_prompt_order(probe: Probe) -> None:
    """The lead gets context, protocol, lead-first-move, then guidance/lead.md."""
    assert probe.run(role="lead").returncode == 0
    prompt = probe.calls()[0]["argv"][1]
    assert prompt == expected_prompt("lead")
    order = [
        prompt.index((RIG_DIR / "startup" / name).read_text().strip()[:200])
        for name in ("context.md", "protocol.md", "lead-first-move.md")
    ]
    assert order == sorted(order)


def test_non_lead_has_no_lead_first_move(probe: Probe) -> None:
    """A non-lead prompt never carries lead-first-move.md."""
    assert probe.run(role="audit").returncode == 0
    prompt = probe.calls()[0]["argv"][1]
    assert prompt == expected_prompt("audit")
    assert (RIG_DIR / "startup" / "lead-first-move.md").read_text().strip()[:200] not in prompt


@pytest.mark.parametrize(
    "lever", ["env-no-claudeai-mcp", "strict-mcp", "no-onboarding", "no-culture", "no-protocol"]
)
def test_each_lever(probe: Probe, lever: str) -> None:
    """Each lever changes exactly its own input."""
    assert probe.run("--lever", lever).returncode == 0, lever
    call = probe.calls()[0]
    argv, managed = call["argv"], call["managed"]
    source = probe.managed.read_text()
    if lever == "env-no-claudeai-mcp":
        assert "ENABLE_CLAUDEAI_MCP_SERVERS" in call["env"]
    else:
        assert "ENABLE_CLAUDEAI_MCP_SERVERS" not in call["env"]
    if lever == "strict-mcp":
        assert argv[-3:] == ["--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}']
    else:
        assert "--strict-mcp-config" not in argv
    if lever == "no-onboarding":
        assert "openrig-onboarding-01.md" not in managed and "onboarding two" not in managed
        assert "openrig-start.md" in managed and SECRET_BODY in managed
    elif lever == "no-culture":
        assert SECRET_BODY not in managed
        assert (
            "<!-- BEGIN OpenRig MANAGED BLOCK: CULTURE.md -->\n"
            "<!-- END OpenRig MANAGED BLOCK: CULTURE.md -->" in managed
        )
    else:
        assert managed == source
    drop = lever == "no-protocol"
    assert argv[1] == expected_prompt("execute", drop_protocol=drop)


def test_deny_extra_and_levers_together(probe: Probe, tmp_path: Path) -> None:
    """deny-extra adds its names; levers combine."""
    extra = tmp_path / "deny.txt"
    extra.write_text("WebFetch\n\nWebSearch\nAskUserQuestion\n")
    result = probe.run("--lever", f"deny-extra:{extra}", "--lever", "strict-mcp")
    assert result.returncode == 0, result.stderr
    argv = probe.calls()[0]["argv"]
    deny = json.loads(arg(argv, "--settings"))["permissions"]["deny"]
    assert deny[-2:] == ["WebFetch", "WebSearch"] and deny.count("AskUserQuestion") == 1
    assert "--strict-mcp-config" in argv
    assert json.loads(probe.out.read_text())["levers"] == [f"deny-extra:{extra}", "strict-mcp"]


def test_culture_replacement(probe: Probe, tmp_path: Path) -> None:
    """--culture replaces the CULTURE.md block body and keeps its markers."""
    culture = tmp_path / "CULTURE-trim.md"
    culture.write_text("trimmed culture\n")
    assert probe.run("--culture", str(culture)).returncode == 0
    managed = probe.calls()[0]["managed"]
    assert (
        "<!-- BEGIN OpenRig MANAGED BLOCK: CULTURE.md -->\ntrimmed culture\n"
        "<!-- END OpenRig MANAGED BLOCK: CULTURE.md -->" in managed
    )
    assert SECRET_BODY not in managed and "default culture" in managed


def test_culture_without_marker_exits_2(probe: Probe, tmp_path: Path) -> None:
    """No CULTURE.md markers in the managed file: exit 2, no probe run."""
    managed_source(probe.managed, culture=False)
    culture = tmp_path / "c.md"
    culture.write_text("x\n")
    result = probe.run("--culture", str(culture))
    assert result.returncode == 2
    assert "CULTURE.md" in result.stderr
    assert probe.calls() == []


def test_turn1_and_median(probe: Probe) -> None:
    """turn1 = input + cache write + cache read; the median of three repeats."""
    probe.usages(
        [
            {"input_tokens": 1, "cache_creation_input_tokens": 10, "cache_read_input_tokens": 100},
            {"input_tokens": 2, "cache_creation_input_tokens": 20, "cache_read_input_tokens": 300},
            {"input_tokens": 3, "cache_creation_input_tokens": 30, "cache_read_input_tokens": 200},
        ]
    )
    assert probe.run("--repeat", "3").returncode == 0
    out = json.loads(probe.out.read_text())
    assert [r["turn1"] for r in out["runs"]] == [111, 322, 233]
    assert out["turn1_median"] == 233
    assert out["schema"] == "hacf.startup_probe/v1"
    assert out["role"] == "execute" and out["model"] == "m-test" and out["levers"] == []
    assert out["claude_version"] == "9.9.9 (Fake Code)"
    run = out["runs"][0]
    assert run["tools"] == ["Bash", "Read", "mcp__x__y"]
    assert run["mcp_servers"] == ["srv-a"]
    assert run["skills_or_commands"] == ["hyper-execute", "loop", "dataviz"]
    assert len(probe.calls()) == 3


@pytest.mark.parametrize("repeat", ["4", "0"])
def test_repeat_bounds(probe: Probe, repeat: str) -> None:
    """--repeat above 3 (or below 1) exits 2 before any run."""
    assert probe.run("--repeat", repeat).returncode == 2
    assert probe.calls() == []


def test_tmpdir_unset(probe: Probe) -> None:
    """TMPDIR unset: exit 2."""
    env = {k: v for k, v in probe.env.items() if k != "TMPDIR"}
    result = probe.run(env=env)
    assert result.returncode == 2 and "TMPDIR" in result.stderr
    assert probe.calls() == []


def test_tmpdir_under_tmp(probe: Probe) -> None:
    """TMPDIR under /tmp: exit 2."""
    result = probe.run(env=dict(probe.env, TMPDIR="/tmp"))
    assert result.returncode == 2
    assert probe.calls() == []


def test_probe_dir_removed(probe: Probe) -> None:
    """The probe directory is gone after the run, on success and on failure."""
    assert probe.run().returncode == 0
    (probe.fake / "exit").write_text("1")
    assert probe.run().returncode == 1
    assert not list(probe.tmpdir.glob("startup-probe-*"))
    assert len(probe.calls()) == 2


def test_missing_init_is_a_failure(probe: Probe) -> None:
    """No init event: exit 1 and no output file (never a guessed number)."""
    (probe.fake / "no-init").write_text("")
    result = probe.run()
    assert result.returncode == 1 and "init" in result.stderr
    assert not probe.out.exists()


def test_output_has_no_reply_or_managed_text(probe: Probe) -> None:
    """The output JSON and stdout hold neither the reply text nor the managed file's text."""
    result = probe.run("--repeat", "2")
    assert result.returncode == 0
    text = probe.out.read_text()
    for needle in (REPLY, SECRET_BODY, "default culture", "onboarding one"):
        assert needle not in text and needle not in result.stdout + result.stderr


def test_unknown_lever_and_role(probe: Probe) -> None:
    """An unknown lever or role exits 2."""
    assert probe.run("--lever", "no-such").returncode == 2
    assert probe.run(role="janitor").returncode == 2
    assert probe.calls() == []
