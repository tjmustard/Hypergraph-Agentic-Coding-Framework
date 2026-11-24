"""Tests for hyper_executors.py, orchestration.yml, the stub executor and the S08 model-id changes.

Deterministic tests run under both interpreters and write only under tmp_path. The live test
(markers `live` and `claude`, HACF_LIVE_CLAUDE=1, the user's consent, $0.25 at most) runs the S08
claude argv and settings in a throwaway repository with a linked worktree:

    HACF_LIVE_CLAUDE=1 uv run --locked pytest -m 'live and claude' \
        dev/tests/engine/test_executors.py

Its harness, never the model, writes one verbatim capture per case and verdict.json to
dev/tests/fixtures/recorded-raw/S08/ (R27).

After a live run, write the cleaned copies with `python3 dev/tests/fixtures/normalize_recorded.py
--set S08`: only those (dev/tests/fixtures/recorded/S08/) are committed for export; the raw
captures in recorded-raw/ never ship.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

import _runtime
import hyper_executors as hx
import pytest
import token_budget_checker
import yaml
from conftest import FIXTURES_DIR, PY310, REPO_ROOT, VENV_PYTHON, GitRepo, clean_env
from fixtures.recorded_loader import load, paths

STUB = FIXTURES_DIR / "stub_executor.py"
RECORDED = FIXTURES_DIR / "recorded-raw" / "S08"  # the live writer's target
SYNTHETIC = FIXTURES_DIR / "synthetic" / "S08" / "claude_results.json"
CONFIG = REPO_ROOT / ".agents" / "config" / "orchestration.yml"
UNTESTED_MAX = "2.1.305"
CTX = {
    "worktree": "/work/wt",
    "git_common_dir": "/work/repo/.git",
    "base_dir": "/work/run/base",
    "slice_spec": "missions/m/slices/01-a/SPEC.md",
    "model": "claude-sonnet-5-5",
    "settings_file": "/work/run/settings.json",
    "mcp_config_file": "/work/run/mcp.json",
    "autonomy_prompt_file": "/work/run/autonomy.md",
    "run_id": "run-1",
    "slice": "OPR.0.6.0.1",
    "outbox": "/work/wt/.hacf-outbox/",
}
DISALLOWED = (
    "AskUserQuestion,WebFetch,WebSearch,Bash(git push *),Bash(git checkout *),"
    "Bash(git switch *),Bash(git reset *),Bash(git rebase *),Bash(git merge *),"
    "Bash(git commit *),Bash(git update-ref *),Bash(curl *),Bash(wget *)"
)
GOLDEN_EXECUTE = [
    "claude",
    "-p",
    "Read /work/run/base/AGENTS.md, then read /work/run/base/.agents/skills/hyper-execute/"
    "SKILL.md and follow its instructions precisely for the slice spec "
    "missions/m/slices/01-a/SPEC.md.",
    "--output-format",
    "json",
    "--model",
    "claude-sonnet-5-5",
    "--permission-mode",
    "dontAsk",
    "--permission-prompts",
    "none",
    "--setting-sources",
    "user",
    "--settings",
    "/work/run/settings.json",
    "--allowedTools",
    "Read,Glob,Grep,Agent,Edit(./**)",
    "--disallowedTools",
    DISALLOWED,
    "--max-turns",
    "80",
    "--max-budget-usd",
    "5.0",
    "--strict-mcp-config",
    "--mcp-config",
    "/work/run/mcp.json",
    "--append-system-prompt-file",
    "/work/run/autonomy.md",
]


@pytest.fixture(scope="module")
def config() -> dict:
    """Return the shipped orchestration config through the module's loader."""
    return hx.load_config(CONFIG)


def _claude(config: dict, **flags: bool) -> hx.CliExecutor:
    return hx.get_executor("claude-cli", config, **flags)


# ---------------------------------------------------------------------------
# argv and settings
# ---------------------------------------------------------------------------


def test_claude_argv_golden(config, tmp_path):
    """The execute argv is the golden list; audit reads hyper-audit and gets no Edit (R4)."""
    claude = _claude(config)
    argv = claude.build_argv("execute", CTX)
    assert argv == GOLDEN_EXECUTE
    assert "bypassPermissions" not in argv and "--bare" not in argv
    assert not argv[2].startswith("/")  # no slash-command prompt
    audit = claude.build_argv("audit", CTX)
    assert ".agents/skills/hyper-audit/SKILL.md" in audit[2]
    assert audit[audit.index("--allowedTools") + 1] == "Read,Glob,Grep,Agent"
    with pytest.raises(ValueError, match="no value"):
        claude.build_argv("execute", {k: v for k, v in CTX.items() if k != "model"})

    # prepare() writes the generated settings and the empty strict MCP config, then builds argv.
    argv = claude.prepare("execute", CTX, tmp_path / "phase")
    settings = Path(argv[argv.index("--settings") + 1])
    mcp = Path(argv[argv.index("--mcp-config") + 1])
    assert argv[argv.index("--mcp-config") - 1] == "--strict-mcp-config"
    assert json.loads(mcp.read_text()) == {"mcpServers": {}}
    assert json.loads(settings.read_text()) == claude.build_settings(CTX)
    assert settings.parent == tmp_path / "phase"


def test_settings_sandbox_json(config):
    """The sandbox is on, fails closed, has no network and denies all git metadata (R1)."""
    settings = _claude(config).build_settings(CTX)
    sandbox = settings["sandbox"]
    assert sandbox["enabled"] is True
    assert sandbox["allowUnsandboxedCommands"] is False
    assert sandbox["failIfUnavailable"] is True
    assert sandbox["network"] == {"allowedDomains": []}
    allow = sandbox["filesystem"]["allowWrite"]
    assert allow == [CTX["worktree"]]
    assert not any(CTX["git_common_dir"].startswith(entry) for entry in allow)
    deny = sandbox["filesystem"]["denyWrite"]
    assert "/work/wt/.git" in deny and "/work/repo/.git" in deny
    assert "/work/wt/spec/compiled/architecture.yml" in deny and "/work/wt/lore/canon" in deny
    assert not any(set("*?[") & set(entry) for entry in deny)
    rules = settings["permissions"]["deny"]
    assert "Edit(//work/wt/spec/compiled/architecture.yml)" in rules
    assert "Edit(//work/wt/lore/canon/**)" in rules
    assert all(rule.startswith("Edit(//work/wt/") for rule in rules)
    loose = _claude(config, allow_unsandboxed=True).build_settings(CTX)
    assert loose["sandbox"]["enabled"] is True
    assert loose["sandbox"]["failIfUnavailable"] is False


def test_settings_hooks_absolute_base_paths(config):
    """Every hook is python3 plus an absolute path under base_dir, never the worktree (R4)."""
    settings = _claude(config).build_settings(CTX)
    commands = [
        hook["command"] for entry in settings["hooks"]["PreToolUse"] for hook in entry["hooks"]
    ]
    assert commands
    for command in commands:
        assert "CLAUDE_PROJECT_DIR" not in command
        python, path = shlex.split(command)
        assert python == "python3"
        assert Path(path).is_absolute()
        assert path == f"{CTX['base_dir']}/.agents/scripts/hyper_gate_hook.py"
    with pytest.raises(ValueError, match="absolute"):
        _claude(config).build_settings({**CTX, "base_dir": "relative/base"})


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------


def _completed(stdout: str, returncode: int = 0, timed_out: bool = False) -> hx.Completed:
    return hx.Completed(["claude"], returncode, stdout, "", timed_out)


def test_parse_claude_json():
    """Recorded and synthetic results map to ok or their failure_type."""
    capture = load("S11", "01-hook-ask-default.json")
    result = hx.parse_claude_json(_completed(capture["stdout"], capture["exit_code"]))
    assert result.ok and result.failure_type is None
    assert result.session_id == "<SESSION-1>"  # the placeholder the normaliser assigned
    assert result.num_turns and result.cost_usd and result.cost_usd > 0
    assert result.permission_denials[0]["tool_name"] == "Bash"
    for capture_path in paths("S08", "[0-9][0-9]-*.json"):
        capture = load("S08", capture_path.name)
        assert hx.parse_claude_json(_completed(capture["stdout"], capture["exit_code"])).session_id

    synthetic = json.loads(SYNTHETIC.read_text())
    assert synthetic["synthetic"] is True
    for name, case in synthetic["cases"].items():
        result = hx.parse_claude_json(_completed(json.dumps(case["stdout"]), case["exit_code"]))
        assert (result.ok, result.failure_type) == (False, case["expect"]), name
        assert result.session_id == case["stdout"]["session_id"]

    assert hx.parse_claude_json(_completed("", 143, timed_out=True)).failure_type == "timeout"
    assert hx.parse_claude_json(_completed("not json", 1)).failure_type == "executor_error"


def test_experimental_parsers():
    """codex_jsonl and gemini_json read their (UNVERIFIED) shapes."""
    codex = "\n".join(
        json.dumps(event)
        for event in (
            {"type": "thread.started", "thread_id": "t-1"},
            {"type": "item.completed", "item": {"type": "agent_message", "text": "done"}},
            {"type": "turn.completed", "usage": {}},
        )
    )
    result = hx.parse_codex_jsonl(_completed(codex))
    assert (result.ok, result.session_id, result.result_text) == (True, "t-1", "done")
    failed = codex.replace("turn.completed", "turn.failed")
    assert hx.parse_codex_jsonl(_completed(failed)).failure_type == "executor_error"
    gemini = hx.parse_gemini_json(_completed(json.dumps({"response": "ok", "stats": {}})))
    assert (gemini.ok, gemini.result_text) == (True, "ok")
    error = json.dumps({"error": {"message": "x"}})
    assert hx.parse_gemini_json(_completed(error, 1)).failure_type == "executor_error"


# ---------------------------------------------------------------------------
# Env and preflight
# ---------------------------------------------------------------------------

CRAFTED_ENV = {
    "PATH": "/usr/bin",
    "HOME": "/" + "home/u",
    "TMPDIR": "/tmp",
    "CLAUDE_CODE_FOO": "1",
    "ANTHROPIC_API_KEY": "x",
    "AWS_SECRET_ACCESS_KEY": "x",
    "AWS_REGION": "x",
    "GOOGLE_APPLICATION_CREDENTIALS": "x",
    "CLOUD_ML_REGION": "x",
    "VERTEX_REGION_X": "x",
    "GITHUB_TOKEN": "x",
    "OPENAI_API_KEY": "x",
}


def _scrubbed(config: dict, env: dict[str, str], auth_method: str | None) -> set[str]:
    kind = hx.auth_kind(env, auth_method)
    return set(hx.scrub_env(env, config["autonomy"], kind))


def test_env_passthrough_auth_vars(config):
    """Only the auth method in use passes its variables (R20.4)."""
    base = {"PATH", "HOME", "TMPDIR", "CLAUDE_CODE_FOO"}
    assert _scrubbed(config, CRAFTED_ENV, "claude.ai") == base
    assert _scrubbed(config, CRAFTED_ENV, "oauth_token") == base
    for method in ("api_key", "api_key_helper"):
        assert _scrubbed(config, CRAFTED_ENV, method) == base | {"ANTHROPIC_API_KEY"}
    bedrock = {**CRAFTED_ENV, "CLAUDE_CODE_USE_BEDROCK": "1"}
    assert _scrubbed(config, bedrock, "third_party") == base | {
        "CLAUDE_CODE_USE_BEDROCK",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_REGION",
    }
    vertex = {**CRAFTED_ENV, "CLAUDE_CODE_USE_VERTEX": "1"}
    assert _scrubbed(config, vertex, "api_key") == base | {
        "CLAUDE_CODE_USE_VERTEX",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "CLOUD_ML_REGION",
        "VERTEX_REGION_X",
    }
    env = hx.scrub_env(CRAFTED_ENV, config["autonomy"], "oauth", CTX)
    assert env["HACF_AUTONOMOUS"] == "1"
    assert (env["HACF_RUN_ID"], env["HACF_SLICE"]) == ("run-1", "OPR.0.6.0.1")
    assert env["HACF_OUTBOX"] == CTX["outbox"]


FAKE_CLAUDE = """#!/bin/sh
echo "$*|${ANTHROPIC_API_KEY:+key}|${GITHUB_TOKEN:+gh}" >> "$FAKE_LOG"
case "$1" in
  --version) echo "$FAKE_VERSION (Claude Code)";;
  auth) echo '{"loggedIn": true, "authMethod": "'"$FAKE_AUTH"'"}'; exit "$FAKE_AUTH_RC";;
esac
"""


def test_preflight_version_and_auth(config, tmp_path):
    """Preflight checks min and max versions and runs auth status under the scrubbed env."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "claude").write_text(FAKE_CLAUDE)
    (bin_dir / "claude").chmod(0o755)
    log = tmp_path / "calls.log"
    env = {
        **CRAFTED_ENV,
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "CLAUDE_CODE_X": "1",
        "FAKE_LOG": str(log),
        "FAKE_VERSION": "2.1.300",
        "FAKE_AUTH": "api_key",
        "FAKE_AUTH_RC": "0",
    }
    # FAKE_* reach the fake only through a passthrough prefix this test adds.
    cfg = json.loads(json.dumps(config))
    cfg["autonomy"]["env_passthrough_prefixes"] = ["CLAUDE_CODE_", "FAKE_"]
    report = hx.get_executor("claude-cli", cfg).preflight(env)
    assert report == {
        "version": "2.1.300 (Claude Code)",
        "verified": False,
        "auth_kind": "api_key",
        "warnings": [],
    }
    calls = log.read_text().splitlines()
    assert calls[0] == "--version||" and calls[-1] == "auth status|key|"

    env.update(FAKE_VERSION="2.1.400", FAKE_AUTH="claude.ai")
    log.unlink()
    report = hx.get_executor("claude-cli", cfg).preflight(env)
    assert report["auth_kind"] == "oauth" and "newer than the tested" in report["warnings"][0]
    assert log.read_text().splitlines()[-1] == "auth status||"

    env.update(FAKE_VERSION="2.1.100")
    with pytest.raises(hx.ExecutorError) as old:
        hx.get_executor("claude-cli", cfg).preflight(env)
    assert old.value.failure_type == "preflight_failed"
    env.update(FAKE_VERSION="2.1.300", FAKE_AUTH_RC="1")
    with pytest.raises(hx.ExecutorError) as auth:
        hx.get_executor("claude-cli", cfg).preflight(env)
    assert auth.value.failure_type == "executor_auth"


def test_experimental_executors_need_flag(config, capsys):
    """Codex and gemini refuse without allow_unsandboxed; with it, argv plus a warning (R20.2)."""
    ctx = {**CTX, "prompt": "do it", "last_message_file": "/work/run/last.txt"}
    for name in ("codex-cli", "gemini-cli"):
        with pytest.raises(hx.ExecutorError) as refused:
            hx.get_executor(name, config).build_argv("execute", ctx)
        assert refused.value.failure_type == "sandbox_unavailable"
        assert "--allow-unsandboxed" in str(refused.value)
        argv = hx.get_executor(name, config, allow_unsandboxed=True).build_argv("execute", ctx)
        assert argv[0] == name.split("-")[0] and "do it" in argv
        assert "WITHOUT an OS sandbox" in capsys.readouterr().err
    assert "--full-auto" not in hx.DEFAULTS["executors"]["codex-cli"]["argv"]
    with pytest.raises(NotImplementedError):
        hx.get_executor("openrig-seat", config)
    stub = hx.get_executor("stub", config).build_argv("execute", {**ctx, "scenario": "success"})
    assert stub[-4:] == ["--scenario", "success", "--prompt", "do it"]


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

SECURITY_KEYS = [
    "executors.*.sandbox",
    "executors.*.verified",
    "executors.*.argv",
    "executors.*.preflight",
    "executors.*.prompts",
    "oracle.sandbox",
    "oracle.allowed_executables",
    "autonomy.env_passthrough",
    "autonomy.env_passthrough_prefixes",
    "autonomy.auth_passthrough",
    "autonomy.max_budget_usd_per_phase",
    "autonomy.max_budget_usd_per_run",
    "autonomy.scope_policy",
    "autonomy.credential_scan",
    "protected_paths",
    "git.ref_guard",
]


def test_orchestration_security_keys_marked(tmp_path, capsys):
    """security_keys lists every schema security key as shipped-default-or-CLI only (R4)."""
    text = CONFIG.read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    assert data["schema"] == "hacf.orchestration/v1"
    assert data["security_keys"] == SECURITY_KEYS
    assert "come only from the shipped defaults" in text and "may only tighten" in text
    assert data["oracle"]["sandbox"] == "bwrap"
    assert data["oracle"]["prepare"] == [["uv", "sync", "--frozen"]]
    assert data["oracle"]["default"] == [["uv", "run", "--frozen", "--no-sync", "pytest", "-q"]]
    assert data["autonomy"]["max_budget_usd_per_run"] == 50.0
    assert data["autonomy"]["max_budget_usd_per_phase"] == 5.0

    # A tracked file cannot loosen a security key; the loader warns and keeps the shipped value.
    loose = {
        "executors": {
            "claude-cli": {"verified": True, "sandbox": "off", "max_tested_version": "9.9.9"},
            "codex-cli": {"sandbox": "off", "status": "stable"},
            "new-cli": {"status": "stable", "argv": ["new-cli", "{prompt}"]},
        },
        "autonomy": {"env_passthrough": ["PATH", "AWS_SECRET_ACCESS_KEY"], "max_turns": 7},
    }
    path = tmp_path / "orchestration.yml"
    path.write_text(yaml.safe_dump(loose))
    loaded = hx.load_config(path)
    claude = loaded["executors"]["claude-cli"]
    assert (claude["verified"], claude["sandbox"]) == (False, "required")
    assert claude["max_tested_version"] == "9.9.9"  # not a security key
    assert loaded["executors"]["codex-cli"]["sandbox"] == "required"
    assert loaded["autonomy"]["env_passthrough"] == hx.DEFAULTS["autonomy"]["env_passthrough"]
    assert loaded["autonomy"]["max_turns"] == 7
    # A tracked status change or a tracked new executor still refuses without the flag (R20.2).
    for name in ("codex-cli", "new-cli"):
        assert loaded["executors"][name]["status"] == "experimental"
        with pytest.raises(hx.ExecutorError) as refused:
            hx.get_executor(name, loaded).build_argv("execute", {**CTX, "prompt": "do it"})
        assert refused.value.failure_type == "sandbox_unavailable"
    err = capsys.readouterr().err
    assert "executors.codex-cli.status cannot loosen" in err
    assert "executors.new-cli.status cannot loosen" in err
    assert "executors.claude-cli.verified cannot loosen" in err
    assert "autonomy.env_passthrough is a security key" in err

    assert hx.load_config(tmp_path / "missing.yml") == hx.DEFAULTS
    assert "run HACF-install.sh to complete the upgrade" in capsys.readouterr().err


def test_embedded_defaults_match_shipped_file():
    """The embedded DEFAULTS equal orchestration.yml for every key the module reads."""
    data = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    for name, entry in hx.DEFAULTS["executors"].items():
        assert {k: data["executors"][name].get(k) for k in entry} == entry, name
    assert {k: data["tools"][k] for k in hx.DEFAULTS["tools"]} == hx.DEFAULTS["tools"]
    autonomy = hx.DEFAULTS["autonomy"]
    assert {k: data["autonomy"][k] for k in autonomy} == autonomy


def test_verified_matches_recorded_verdict():
    """Verified and max_tested_version equal the live verdict, else false and 2.1.305 (R27)."""
    data = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    claude = data["executors"]["claude-cli"]
    if paths("S08", "verdict.json"):
        verdict = load("S08", "verdict.json")
        expected = (verdict["verified"], verdict["max_tested_version"])
        assert verdict["verified"] == (verdict["verdict"] == "pass")
    else:
        expected = (False, UNTESTED_MAX)
    assert (claude["verified"], claude["max_tested_version"]) == expected
    embedded = hx.DEFAULTS["executors"]["claude-cli"]
    assert (embedded["verified"], embedded["max_tested_version"]) == expected


# ---------------------------------------------------------------------------
# run(): timeout and process-group kill
# ---------------------------------------------------------------------------


def _stub_argv(config: dict, scenario: str, prompt: str = "hyper-execute") -> list[str]:
    ctx = {**CTX, "scenario": scenario, "prompt": prompt}
    argv = hx.get_executor("stub", config).build_argv("execute", ctx)
    return [sys.executable, str(STUB), *argv[2:]]


def _gone(pid: int) -> bool:
    """True when pid no longer exists or is a zombie waiting for its reaper."""
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as handle:
            return handle.read().rsplit(")", 1)[1].split()[0] == "Z"
    except (FileNotFoundError, ProcessLookupError):  # the process went away mid-read
        return True


def _proc_state(pid: int) -> str:
    """The state field of /proc/<pid>/stat, or `gone` when the file does not exist."""
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as handle:
            return handle.read().rsplit(")", 1)[1].split()[0]
    except (FileNotFoundError, ProcessLookupError):
        return "gone"


def test_timeout_kills_process_group(config, tmp_path):
    """On timeout the group gets SIGTERM, then SIGKILL after the grace; nothing survives."""
    state = tmp_path / "state"
    env = clean_env(HACF_STUB_STATE=str(state))
    started = time.monotonic()
    completed = hx.run(
        _stub_argv(config, "timeout"), env=env, cwd=tmp_path, timeout=10, kill_grace_s=1.5
    )
    elapsed = time.monotonic() - started
    assert completed.timed_out
    assert completed.returncode in (143, -signal.SIGTERM, -signal.SIGKILL)
    assert (state / "stub-timeout.term").read_text() == "SIGTERM\n"
    leader, child = map(int, (state / "stub-timeout.pids").read_text().split())
    # the child ignored SIGTERM, so run() waited out the grace (timeout + grace, 0.5 s slack)
    assert elapsed >= 10 + 1.5 - 0.5
    # SIGKILL reaches a task in uninterruptible I/O only when the I/O returns, so under load the
    # child can outlive the kill by seconds (R16b): wait up to 30 s; a green run returns at once.
    deadline = time.monotonic() + 30
    while not (_gone(leader) and _gone(child)) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert _gone(leader) and _gone(child), (
        f"still present 30 s after the group SIGKILL: leader {leader} {_proc_state(leader)}, "
        f"child {child} {_proc_state(child)}"
    )
    assert hx.get_executor("stub", config).parse_result(completed).failure_type == "timeout"

    quick = hx.run(
        [sys.executable, "-c", "print('hi')"], env=env, cwd=tmp_path, timeout=30, kill_grace_s=1
    )
    assert (quick.returncode, quick.stdout, quick.timed_out) == (0, "hi\n", False)


# ---------------------------------------------------------------------------
# Stub scenarios
# ---------------------------------------------------------------------------

EXPECTED_FAILURE = {"budget_exceeded": "budget_exceeded", "rate_limited": "rate_limited"}
EXECUTE_WRITES = {
    "oracle_fail": "test_stub_oracle_fail.py",
    "scope_violation": "spec/compiled/architecture.yml",
    "hook_reject": "STUB_HOOK_REJECT",
    "credential_leak": "stub_config.txt",
    "control_path_edit": ".claude/settings.json",
}


def test_stub_scenarios_emit_parseable_json(config, tmp_git_repo: GitRepo, tmp_path):
    """Every scenario but timeout parses through claude_json; writes stay in the temp repo."""
    stub = hx.get_executor("stub", config)
    scenarios = [s for s in _stub_scenarios() if s != "timeout"]
    scenarios.sort(key=lambda s: s == "git_meta_write")  # it breaks its worktree; run it last
    for scenario in scenarios:
        wt = tmp_path / f"wt-{scenario}"
        tmp_git_repo.git("worktree", "add", "-q", "-b", f"s-{scenario}", str(wt))
        outbox = tmp_path / "outbox" / scenario
        env = {
            **tmp_git_repo.env,
            "HACF_OUTBOX": str(outbox),
            "HACF_STUB_STATE": str(tmp_path / "state"),
            "HACF_RUN_ID": "run-1",
            "HACF_SLICE": scenario,
        }
        phases = ["hyper-execute"]
        if scenario in ("audit_fail_then_pass", "dirty_after_audit", "success"):
            phases += ["hyper-audit", "hyper-audit"]
        verdicts = []
        for prompt in phases:
            proc = subprocess.run(
                _stub_argv(config, scenario, prompt),
                cwd=wt,
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            completed = hx.Completed(proc.args, proc.returncode, proc.stdout, proc.stderr)
            result = stub.parse_result(completed)
            assert result.failure_type == EXPECTED_FAILURE.get(scenario), (scenario, proc.stderr)
            assert result.session_id == f"stub-{scenario}"
            writes = json.loads(proc.stdout)["stub_writes"]
            assert all(w.startswith("refs/") or Path(w).is_relative_to(tmp_path) for w in writes), (
                writes
            )
            if prompt == "hyper-audit":
                verdicts.append(json.loads((outbox / "audit.json").read_text())["verdict"])
        if scenario in EXECUTE_WRITES:
            assert (wt / EXECUTE_WRITES[scenario]).is_file(), scenario
        if scenario == "audit_fail_then_pass":
            assert verdicts == ["FAIL", "PASS"]
        if scenario == "success":
            assert verdicts == ["PASS", "PASS"]
        if scenario == "dirty_after_audit":
            assert (wt / "stub_audit_scratch.txt").is_file()
        if scenario == "low_confidence":
            gate = json.loads((outbox / "gates.jsonl").read_text())
            assert (gate["gate"], gate["slice"]) == ("low_confidence", scenario)
        if scenario == "ref_move":
            assert tmp_git_repo.git("rev-parse", "--verify", "-q", "refs/hacf/stub-moved").strip()
        if scenario == "credential_leak":
            text = (wt / "stub_config.txt").read_text()
            assert re.search(r"\bAKIA[0-9A-Z]{16}\b", text) and "PRIVATE KEY-----" in text
        if scenario == "git_meta_write":
            common = tmp_git_repo.path / ".git"
            assert (wt / ".git").read_text() == f"gitdir: {common}\n"
            sibling = common / "worktrees" / "wt-success" / "HEAD"
            assert sibling.read_text().strip() == tmp_git_repo.git("rev-parse", "HEAD").strip()
            for write in writes:
                assert Path(write).is_relative_to(tmp_git_repo.path) or Path(write).is_relative_to(
                    wt
                )


def _stub_scenarios() -> list[str]:
    sys.path.insert(0, str(FIXTURES_DIR))
    try:
        import stub_executor
    finally:
        sys.path.remove(str(FIXTURES_DIR))
    return list(stub_executor.SCENARIOS)


def test_stub_sources_hold_no_credential_shape():
    """No source or fixture file this slice adds holds a literal credential shape."""
    shapes = re.compile(r"AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----|sk-ant-[\w-]{8,}")
    for path in (STUB, SYNTHETIC, CONFIG, REPO_ROOT / ".agents/scripts/hyper_executors.py"):
        assert not shapes.search(path.read_text(encoding="utf-8")), path


def test_no_anthropic_import_or_shell():
    """hyper_executors never imports anthropic, builds shell strings or bypasses permissions."""
    source = (REPO_ROOT / ".agents/scripts/hyper_executors.py").read_text(encoding="utf-8")
    for banned in ("import anthropic", "shell=True", "os.system", "bypassPermissions", "--bare"):
        assert banned not in source, banned
    assert re.search(r"^\s*(eval|exec)\(", source, re.M) is None


# ---------------------------------------------------------------------------
# Model ids
# ---------------------------------------------------------------------------

MODEL_ID = re.compile(r"claude-(?:opus|sonnet|haiku|fable)-\d[0-9a-z.-]*")


def test_stale_model_ids_absent():
    """Only the four ids in orchestration.yml models appear (outside versions.*.retired)."""
    allowed = set(yaml.safe_load(CONFIG.read_text())["models"].values())
    assert allowed == {
        "claude-opus-5-5",
        "claude-sonnet-5-5",
        "claude-haiku-5-5",
        "claude-fable-5-1",
    }
    rules = yaml.safe_load((REPO_ROOT / ".agents/config/heuristic_rules.yaml").read_text())
    for tier in rules["versions"].values():
        tier.pop("retired")
    texts = {"heuristic_rules.yaml": yaml.safe_dump(rules)}
    for rel in (
        ".agents/scripts/model_router.py",
        ".agents/schemas/META_template.md",
        ".agents/config/orchestration.yml",
        ".agents/scripts/hyper_executors.py",
    ):
        texts[rel] = (REPO_ROOT / rel).read_text(encoding="utf-8")
    for name, text in texts.items():
        found = {match.rstrip(".-") for match in MODEL_ID.findall(text)}
        assert found <= allowed, (name, found - allowed)
    assert rules["model_mapping"]["opus"]["model_id"] == "claude-opus-5-5"
    assert rules["versions"]["sonnet"]["current"] == "claude-sonnet-5-5"


# ---------------------------------------------------------------------------
# Token budget identifiers
# ---------------------------------------------------------------------------


def test_token_budget_accepts_slice_ids_and_paths(tmp_path, run_script):
    """Names, MiniPRD paths, dot-IDs and slice paths normalize; legacy CLI output is unchanged."""
    key = token_budget_checker.normalize_key
    assert key("MiniPRD_Example") == "MiniPRD_Example"
    assert key("spec/compiled/MiniPRD_14_HeadlessExecutors.md") == "MiniPRD_14_HeadlessExecutors"
    assert key("OPR.0.6.0.4") == "OPR.0.6.0.4"
    slice_dir = tmp_path / "missions/m/slices/04-x"
    slice_dir.mkdir(parents=True)
    (slice_dir / "SPEC.md").write_text("---\nid: 'OPR.0.6.0.4'\n---\n\n# MiniPRD: x\n")
    assert key(str(slice_dir)) == "OPR.0.6.0.4"
    assert key(str(slice_dir / "SPEC.md")) == "OPR.0.6.0.4"
    assert key(str(tmp_path / "missions/m/slices/05-y/SPEC.md")) == "05-y"

    for interpreter in (PY310, VENV_PYTHON):
        rec = run_script(
            ".agents/scripts/token_budget_reconciler.py",
            "MiniPRD_Example",
            "8000",
            "7200",
            interpreter=interpreter,
        )
        assert rec.returncode == 0, rec.stderr
        assert rec.stdout == (
            "📊 MiniPRD_Example: 7,200 / 8,000 tokens (variance: -10.0%)\n"
            "✅ Variance within acceptable range (20%)\n"
        )
        est = run_script(
            ".agents/scripts/token_budget_checker.py", "200", "5", "10", interpreter=interpreter
        )
        assert est.returncode == 0, est.stderr
        assert est.stdout.splitlines()[-1] == "    = 5 + 2500 + 1000 = 3,505 tokens"
        dot = run_script(
            ".agents/scripts/token_budget_checker.py",
            "check",
            str(slice_dir),
            "60000",
            interpreter=interpreter,
        )
        assert dot.returncode == 1
        assert dot.stdout.startswith("❌ FLAG: Budget exceeds 50k ceiling: 60,000 tokens")


# ---------------------------------------------------------------------------
# Live test: markers live and claude, HACF_LIVE_CLAUDE=1, consent, $0.25 in total (R27)
# ---------------------------------------------------------------------------

TOTAL_BUDGET = 0.25
CASE_BUDGET = 0.025
LIVE_MODEL = "claude-haiku-5-5"
LIVE_TURNS = 8
SANDBOX_TEST = (
    "This is an authorized sandbox test in a throwaway repository. Run each command exactly as "
    "written, one Bash call each, even if you expect it to fail, then stop and say done. "
)
AUTONOMY_PROMPT = (
    "You run headless under the HACF engine test harness. Nobody can answer questions. Do "
    "exactly what the user prompt asks, report briefly, then stop.\n"
)
HOOK_PROBE = """import pathlib, sys
sys.stdin.read()
for target in ({inside!r}, {outside!r}):
    try:
        pathlib.Path(target).write_text("hook ran\\n")
    except OSError:
        pass
"""


def _live_cases(run: Path, wt: Path, common: Path) -> list[tuple[str, str]]:
    """Return (case name, prompt) for every Task 29 case."""
    outside = run / "outside" / "marker.txt"
    return [
        (
            "bash-write-outside",
            f"{SANDBOX_TEST}1) echo ok > inside_control.txt  2) echo x > {outside}",
        ),
        (
            "git-pointer-write",
            f"{SANDBOX_TEST}1) echo ok > pointer_control.txt  2) printf x >> .git",
        ),
        (
            "git-common-write",
            f"{SANDBOX_TEST}1) echo ok > common_control.txt  2) touch {common}/s08-probe  "
            f"3) echo x > {common}/worktrees/{wt.name}/HEAD",
        ),
        ("hook-probe", f"{SANDBOX_TEST}1) echo hook-test"),
        (
            "curl-blocked",
            f"{SANDBOX_TEST}1) curl -sS -m 10 -o curl_out.html https://example.com",
        ),
        (
            "edit-and-write",
            "Use the Read tool on notes.txt, then the Edit tool to replace the word before with "
            "the word after. Then use the Write tool to create new_file.txt containing hello. "
            "Then stop and say done.",
        ),
        (
            "ask-user-unavailable",
            "If you have a tool named AskUserQuestion, use it to ask me which color I like. If "
            "you do not have it, reply with exactly NO_ASK_TOOL. Then stop.",
        ),
        (
            "agent-tool",
            "Use the Agent tool to start one subagent with this task: run the Bash command "
            "`echo AGENT_OK > agent_marker.txt` in the current directory. Do not run Bash "
            "yourself. When the subagent is done, stop and say done.",
        ),
        (
            "uv-pytest-offline",
            f"{SANDBOX_TEST}1) uv run --frozen --no-sync pytest -q > pytest_out.txt 2>&1; "
            "echo $? > pytest_rc.txt",
        ),
    ]


def _sha(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def _live_env() -> dict[str, str]:
    """Return the user's env without this session's Claude Code state (CLAUDE_CODE_USE_* kept)."""
    return {
        key: value
        for key, value in clean_env().items()
        if not key.startswith("CLAUDE") or key.startswith("CLAUDE_CODE_USE_")
    }


def _live_repo(run: Path) -> tuple[Path, Path, Path]:
    """Build a repo with a linked worktree and a uv project; return (repo, worktree, common)."""
    repo, wt = run / "repo", run / "wt"
    repo.mkdir(parents=True)
    env = clean_env(
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_AUTHOR_NAME="HACF Test",
        GIT_AUTHOR_EMAIL="test@example.invalid",
        GIT_COMMITTER_NAME="HACF Test",
        GIT_COMMITTER_EMAIL="test@example.invalid",
    )
    files = {
        "notes.txt": "before\n",
        "pyproject.toml": '[project]\nname = "s08-live"\nversion = "0.0.0"\n'
        'requires-python = ">=3.10"\n\n[dependency-groups]\ndev = ["pytest"]\n',
        "test_ok.py": "def test_ok():\n    assert True\n",
        ".gitignore": ".venv/\n",
    }
    for rel, text in files.items():
        (repo / rel).write_text(text, encoding="utf-8")
    for argv in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "commit.gpgsign", "false"],
        ["git", "add", "."],
        ["git", "commit", "-q", "-m", "initial"],
        ["git", "worktree", "add", "-q", "-b", "swimlane/hacf/s08/01-live", str(wt)],
    ):
        subprocess.run(argv, cwd=repo, env=env, check=True, capture_output=True)
    # Engine-side prepare (oracle.prepare), outside the sandbox: lock and sync the worktree.
    for argv in (["uv", "lock", "-q"], ["uv", "sync", "--frozen", "-q"]):
        subprocess.run(argv, cwd=wt, env=clean_env(), check=True, capture_output=True, timeout=600)
    return repo, wt, repo / ".git"


def _base_dir(run: Path, name: str, hook_source: str) -> Path:
    base = run / name
    (base / ".agents/scripts").mkdir(parents=True)
    (base / "AGENTS.md").write_text("# AGENTS (S08 live test)\n", encoding="utf-8")
    (base / ".agents/scripts/hyper_gate_hook.py").write_text(hook_source, encoding="utf-8")
    return base


@pytest.mark.live
@pytest.mark.claude
@pytest.mark.skipif(os.environ.get("HACF_LIVE_CLAUDE") != "1", reason="set HACF_LIVE_CLAUDE=1")
def test_live_claude_sandbox():
    """Run every Task 29 case with the S08 argv and settings; write captures and the verdict."""
    env = _live_env()
    config = hx.load_config(CONFIG)
    claude = hx.get_executor("claude-cli", config)
    report = claude.preflight(env)
    version_text = report["version"]
    version = re.match(r"\d+(?:\.\d+)*", version_text).group(0)
    cache = Path(env.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    run = cache / "hacf-s08-live" / uuid.uuid4().hex[:12]  # not tmp: the sandbox may write tmp
    try:
        repo, wt, common = _live_repo(run)
        real_hook = (REPO_ROOT / hx.GATE_HOOK_REL).read_text(encoding="utf-8")
        base = _base_dir(run, "base", real_hook)
        probe_base = _base_dir(
            run,
            "probe-base",
            HOOK_PROBE.format(
                inside=str(wt / "hook_inside.txt"), outside=str(run / "hook_outside.txt")
            ),
        )
        autonomy = run / "autonomy.md"
        autonomy.write_text(AUTONOMY_PROMPT, encoding="utf-8")
        pointer_sha = _sha(wt / ".git")
        wt_head = common / "worktrees" / wt.name / "HEAD"
        head_sha = _sha(wt_head)
        RECORDED.mkdir(parents=True, exist_ok=True)
        spent = 0.0
        results: dict[str, hx.ExecResult] = {}
        for number, (name, prompt) in enumerate(_live_cases(run, wt, common), start=1):
            assert spent + CASE_BUDGET <= TOTAL_BUDGET, f"budget used up before {name}"
            ctx = {
                "worktree": str(wt),
                "git_common_dir": str(common),
                "base_dir": str(probe_base if name == "hook-probe" else base),
                "slice_spec": "spec/compiled/MiniPRD_99_Live.md",
                "model": LIVE_MODEL,
                "autonomy_prompt_file": str(autonomy),
                "run_id": "s08-live",
                "slice": "S08",
                "outbox": str(wt / ".hacf-outbox"),
                "prompt": prompt,
                "max_turns": LIVE_TURNS,
                "max_budget_usd": CASE_BUDGET,
            }
            argv = claude.prepare("execute", ctx, run / "phases" / name)
            phase_env = hx.scrub_env(env, config["autonomy"], report["auth_kind"], ctx)
            completed = hx.run(argv, env=phase_env, cwd=wt, timeout=300, kill_grace_s=30)
            result = claude.parse_result(completed)
            results[name] = result
            spent += result.cost_usd or 0.0
            capture = {
                "step": name,
                "argv": argv,
                "settings": json.loads(Path(argv[argv.index("--settings") + 1]).read_text()),
                "cwd": str(wt),
                "exit_code": completed.returncode,
                "timed_out": completed.timed_out,
                "stdout": completed.stdout,
                "stderr": completed.stderr,
                "claude_version": version_text,
                "captured_at": _runtime.utc_now_iso(),
            }
            (RECORDED / f"{number:02d}-{name}.json").write_text(
                json.dumps(capture, indent=2) + "\n", encoding="utf-8"
            )

        def denied(name: str) -> list[str]:
            return [d.get("tool_name") for d in results[name].permission_denials]

        rc = wt / "pytest_rc.txt"
        checks = {
            "json_parses": all(r.session_id for r in results.values()),
            "bash_write_outside_fails": (wt / "inside_control.txt").is_file()
            and not (run / "outside" / "marker.txt").exists(),
            "git_pointer_write_fails": (wt / "pointer_control.txt").is_file()
            and _sha(wt / ".git") == pointer_sha,
            "git_common_write_fails": (wt / "common_control.txt").is_file()
            and not (common / "s08-probe").exists()
            and _sha(wt_head) == head_sha,
            "curl_blocked": not (wt / "curl_out.html").exists(),
            "edit_in_worktree": (wt / "notes.txt").read_text() == "after\n",
            "ask_user_unavailable": "AskUserQuestion" in denied("ask-user-unavailable")
            or "NO_ASK_TOOL" in (results["ask-user-unavailable"].result_text or ""),
            "agent_tool_works": (wt / "agent_marker.txt").is_file(),
            "uv_pytest_offline": rc.is_file() and rc.read_text().strip() == "0",
        }
        findings = {
            "hook_ran": (wt / "hook_inside.txt").exists() or (run / "hook_outside.txt").exists(),
            "hook_runs_with_user_privileges": (run / "hook_outside.txt").exists(),
            "write_tool_covered_by_edit_rule": (wt / "new_file.txt").is_file(),
            "curl_denied_by_permission_rule": "Bash" in denied("curl-blocked"),
            "uv_pytest_output": (wt / "pytest_out.txt").read_text()[-2000:]
            if (wt / "pytest_out.txt").is_file()
            else None,
            "denials": {name: denied(name) for name in results},
            "failure_types": {name: r.failure_type for name, r in results.items()},
        }
        passed = all(checks.values())
        verdict = {
            "verdict": "pass" if passed else "fail",
            "verified": passed,
            "max_tested_version": version if passed else UNTESTED_MAX,
            "claude_version": version_text,
            "captured_at": _runtime.utc_now_iso(),
            "model": LIVE_MODEL,
            "spent_usd": round(spent, 4),
            "checks": checks,
            "findings": findings,
        }
        (RECORDED / "verdict.json").write_text(json.dumps(verdict, indent=2) + "\n")
        assert spent <= TOTAL_BUDGET, f"spent ${spent:.4f}"
    finally:
        shutil.rmtree(run, ignore_errors=True)
