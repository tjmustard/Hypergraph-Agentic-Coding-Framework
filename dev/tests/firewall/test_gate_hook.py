"""Tests for hyper_gate_hook.py (S11): deterministic decisions, plus the opt-in live test.

The deterministic tests run the hook as a subprocess, as Claude Code does, against temp projects.
The live test (markers `live` and `claude`, HACF_LIVE_CLAUDE=1, spends money) runs `claude -p` in
a temp repo and writes one verbatim capture per case to dev/tests/fixtures/recorded-raw/S11/, plus a
verdict.json computed from those captures and the marker files.

After a live run, write the cleaned copies with `python3 dev/tests/fixtures/normalize_recorded.py
--set S11`: only those (dev/tests/fixtures/recorded/S11/) are committed for export; the raw
captures in recorded-raw/ never ship.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import hyper_gate_hook
import hyper_lore
import pytest
from conftest import FIXTURES_DIR, REPO_ROOT, SCRIPTS_DIR, clean_env

HOOK = SCRIPTS_DIR / "hyper_gate_hook.py"
RECORDED = FIXTURES_DIR / "recorded-raw" / "S11"
ASK_MODES = ["default", "acceptEdits", "auto"]
DENY_MODES = ["dontAsk", "bypassPermissions", "plan"]
SWITCH = "switch to default or acceptEdits (Shift+Tab) and retry"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """Return a temp project holding a warrant file."""
    root = tmp_path / "project"
    (root / ".agents/scripts").mkdir(parents=True)
    (root / "warrant.txt").write_text("the user said yes\n", encoding="utf-8")
    return root


def run_hook(project: Path, command, mode="default", cwd=None, raw=None, env=None):
    """Run the hook on one input and return the finished process."""
    data = {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd or project)}
    if mode is not None:
        data["permission_mode"] = mode
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=raw if raw is not None else json.dumps(data),
        cwd=project,
        env=clean_env(CLAUDE_PROJECT_DIR=str(project), **(env or {})),
        capture_output=True,
        text=True,
        check=False,
    )


def asked(proc) -> str:
    """Assert the hook asked and return the reason."""
    assert proc.returncode == 0, proc.stderr
    output = json.loads(proc.stdout)["hookSpecificOutput"]
    assert output["hookEventName"] == "PreToolUse"
    assert output["permissionDecision"] == "ask"
    return output["permissionDecisionReason"]


def tickets(project: Path) -> list[Path]:
    """Return the ticket files of a project."""
    directory = project / ".agents/state/lore/tickets"
    return sorted(directory.iterdir()) if directory.exists() else []


def spellings(warrant: str) -> list[str]:
    """Return every spelling of a gated approve that must be matched and bound."""
    args = f"approve ADR-0003 --nonce n1 --warrant-file {warrant}"
    return [
        f"python3 .agents/scripts/hyper_lore.py {args}",
        f"python .agents/scripts/hyper_lore.py {args}",
        f".venv/bin/python .agents/scripts/hyper_lore.py {args}",
        f"cd .agents/scripts && python3 hyper_lore.py {args}",
        f"cd .agents/scripts; ./hyper_lore.py {args}",
        f".agents/scripts/hyper_lore.py {args}",
        f"python3 -u -X utf8 .agents/scripts/hyper_lore.py {args}",
        f"python3 -m hyper_lore {args}",
        f"python3 -m agents.scripts.hyper_lore {args}",
        f"python3 -m runpy .agents/scripts/hyper_lore.py {args}",
        f"FOO=1 env BAR=2 python3 .agents/scripts/hyper_lore.py {args}",
        f"uv run --locked python .agents/scripts/hyper_lore.py {args}",
        f"timeout 30 python3 .agents/scripts/hyper_lore.py {args}",
        f"git status && python3 .agents/scripts/hyper_lore.py {args} 2>&1 | tail -3",
        f"(cd .agents/scripts && python3 hyper_lore.py {args})",
        f'git status\npython3 ".agents/scripts/hyper_lore.py" {args}',
        f"python3 hyper_lore.py approve ADR-0003 --nonce=n1 --warrant-file={warrant}",
    ]


@pytest.mark.parametrize("mode", ASK_MODES + DENY_MODES)
@pytest.mark.parametrize("index", range(17))
def test_hook_decisions_by_mode(project, mode, index):
    """Every spelling asks (with one ticket) in the ask modes and exits 2 in the deny modes."""
    command = spellings(str(project / "warrant.txt"))[index]
    proc = run_hook(project, command, mode)
    if mode in ASK_MODES:
        assert "ADR-0003" in asked(proc)
        assert len(tickets(project)) == 1
    else:
        assert proc.returncode == 2 and proc.stdout == ""
        assert SWITCH in proc.stderr
        assert tickets(project) == []


@pytest.mark.parametrize("verb", ["approve", "reject", "retire", "reopen", "review"])
def test_hook_gated_verbs_have_their_wording(project, verb):
    """Each gated verb asks with its own wording and binds its own ticket."""
    extra = "--still-valid " if verb == "review" else ""
    command = (
        f"python3 hyper_lore.py {verb} ADR-0003 {extra}--nonce n1 "
        f"--warrant-file {project / 'warrant.txt'}"
    )
    reason = asked(run_hook(project, command))
    assert reason.startswith(verb.capitalize())
    assert json.loads(tickets(project)[0].read_text(encoding="utf-8"))["verb"] == verb


def test_hook_relative_warrant_resolves_through_cd(project):
    """A relative warrant path is read from the directory the cd segments reached."""
    (project / "sub").mkdir()
    (project / "sub/w.txt").write_text("cd warrant", encoding="utf-8")
    reason = asked(
        run_hook(
            project,
            "cd sub && python3 ../hyper_lore.py approve ADR-0003 --nonce n --warrant-file w.txt",
        )
    )
    assert "cd warrant" in reason
    proc = run_hook(
        project, "python3 hyper_lore.py approve ADR-0003 --nonce n --warrant-file w.txt"
    )
    assert proc.returncode == 2


def test_hook_bypass_reason_says_switch_mode(project):
    """A gated approve in bypassPermissions exits 2 with the switch-mode reason (R7)."""
    command = (
        f"python3 hyper_lore.py approve ADR-0003 --nonce n --warrant-file {project}/warrant.txt"
    )
    proc = run_hook(project, command, "bypassPermissions")
    assert proc.returncode == 2
    assert SWITCH in proc.stderr


@pytest.fixture
def shadow_shlex(tmp_path: Path) -> dict[str, str]:
    """Return an env that makes the hook's `import shlex` raise."""
    shim = tmp_path / "shim"
    shim.mkdir()
    (shim / "shlex.py").write_text("raise RuntimeError('injected')\n", encoding="utf-8")
    return {"PYTHONPATH": str(shim)}


def test_hook_fails_closed_on_crash(project, shadow_shlex):
    """An injected exception, bad JSON, bad shapes and missing or unknown modes all exit 2."""
    harmless = "git status --short"
    cases = [
        run_hook(project, harmless, env=shadow_shlex),  # an import that raises
        run_hook(project, harmless, raw="{not json"),
        run_hook(project, harmless, raw=""),
        run_hook(project, harmless, raw="[]"),
        run_hook(project, harmless, raw='{"permission_mode": "default"}'),
        run_hook(
            project, harmless, raw='{"permission_mode": "default", "tool_input": {"command": 5}}'
        ),
        run_hook(project, harmless, mode=None),
        run_hook(project, harmless, mode="yolo"),
    ]
    for proc in cases:
        assert proc.returncode == 2, proc.stdout + proc.stderr
        assert proc.stderr.strip()
        assert proc.stdout == ""


def test_hook_fails_closed_when_logic_raises():
    """An exception inside decide() reaches the catch-all, which exits 2 (in a child process)."""
    code = (
        "import sys; sys.path.insert(0, sys.argv[1]); import hyper_gate_hook as hook\n"
        "def boom(*a): raise RuntimeError('injected')\n"
        "hook.decide = boom\n"
        "hook.main()\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code, str(SCRIPTS_DIR)],
        input="{}",
        env=clean_env(),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 2
    assert "injected" in proc.stderr


def test_hook_exit_is_2_when_stdout_is_closed(project):
    """A failed flush of the decision still exits 2, never Python's exit-time 120."""
    command = (
        f"python3 hyper_lore.py approve ADR-0003 --nonce n --warrant-file {project}/warrant.txt"
    )
    data = {"permission_mode": "default", "cwd": str(project), "tool_input": {"command": command}}
    with open("/dev/full", "w", encoding="utf-8") as full:
        proc = subprocess.run(
            [sys.executable, str(HOOK)],
            input=json.dumps(data),
            stdout=full,
            stderr=subprocess.PIPE,
            env=clean_env(CLAUDE_PROJECT_DIR=str(project)),
            text=True,
            check=False,
        )
    assert proc.returncode == 2


@pytest.mark.parametrize(
    "tail",
    [
        "ADR-0003 --nonce n --warrant-file /missing",
        "--nonce n --warrant-file WARRANT",
        "ADR-0003 --warrant-file WARRANT",
        "ADR-0003 --nonce n",
        "ADR-0003 ADR-0004 --nonce n --warrant-file WARRANT",
        "../../evil --nonce n --warrant-file WARRANT",
        "ADR-0003 --nonce n --warrant-file /",
    ],
    ids=["missing-file", "no-id", "no-nonce", "no-warrant", "two-ids", "bad-id", "a-directory"],
)
def test_hook_missing_warrant_file_denies(project, tail):
    """A gated invocation missing its id, nonce or warrant file exits 2 and writes no ticket."""
    command = (
        f"python3 hyper_lore.py approve {tail.replace('WARRANT', str(project / 'warrant.txt'))}"
    )
    proc = run_hook(project, command)
    assert proc.returncode == 2, proc.stdout
    assert tickets(project) == []


def test_hook_two_gated_verbs_deny(project):
    """One command cannot carry two gated verbs: a request covers one node."""
    warrant = project / "warrant.txt"
    one = f"python3 hyper_lore.py approve ADR-0003 --nonce n --warrant-file {warrant}"
    proc = run_hook(project, f"{one} && {one}")
    assert proc.returncode == 2
    assert tickets(project) == []


@pytest.mark.parametrize(
    "command",
    [
        """python3 -c "import runpy,sys; sys.argv=['x','approve']; """
        """runpy.run_path('.agents/scripts/hyper_'+'lore.py', run_name='__main__')" """,
        "python3 -c 'import hyper_lore; hyper_lore.main([\"approve\"])'",
        'python3 -c "unterminated hyper_lore',
        'S=.agents/scripts/hyper_lore.py; python3 "$S" approve ADR-0003 --nonce n --warrant-file w',
        "python3 $(find . -name hyper_lore.py) approve ADR-0003 --nonce n --warrant-file w",
        'python3 .agents/scripts/hyper_lore.py approve ADR-0003 --nonce "$N" --warrant-file w',
        'python3 .agents/scripts/hyper_lore.py "$VERB" ADR-0003 --nonce n --warrant-file w',
        'cd "$D" && python3 hyper_lore.py approve ADR-0003 --nonce n --warrant-file w',
        "bash -c 'python3 hyper_lore.py approve ADR-0003 --nonce n --warrant-file w'",
        "eval 'python3 hyper_lore.py approve ADR-0003'",
        "python3 - <<'EOF'\nimport hyper_lore\nEOF",
        "bash <<'EOF'\npython3 hyper_lore.py approve ADR-0003\nEOF",
        "echo 'python3 hyper_lore.py approve ADR-0003' | bash",
        "python3 $TOOL approve ADR-0003 --nonce n --warrant-file w",
        "python3 hyper_l*.py approve ADR-0003 --nonce n --warrant-file w",
    ],
)
def test_hook_evasions_matched(project, command):
    """Each unresolvable spelling gets ask with no ticket, and exit 2 in a deny mode (R6)."""
    assert asked(run_hook(project, command))
    assert tickets(project) == []
    proc = run_hook(project, command, "plan")
    assert proc.returncode == 2 and SWITCH in proc.stderr


@pytest.mark.parametrize("mode", ["default", "bypassPermissions"])
@pytest.mark.parametrize(
    "command",
    [
        "python3 .agents/scripts/hyper_lore.py validate && "
        "python3 .agents/scripts/hyper_ledger.py review-template --json",
        'python3 .agents/scripts/hyper_lore.py propose --title "Retire the compaction store"',
        'python3 .agents/scripts/hyper_lore.py note --legacy --text "reopen after review"',
        'python3 .agents/scripts/hyper_lore.py stale --files a --reason "needs review"',
        'git commit -m "hyper_lore: review fixes"',
        'git commit -m "$(cat <<\'EOF\'\nhyper_lore.py approve: it\'s "fixed"\nEOF\n)"',
        "cat > notes.md <<'EOF'\npython3 hyper_lore.py approve ADR-0003\nEOF",
        "python3 .agents/scripts/hyper_lore.py show ADR-0003",
        "python3 .agents/scripts/hyper_lore.py list --status CANON",
        "python3 .agents/scripts/hyper_lore.py approval-request ADR-0003",
        "cat .agents/scripts/hyper_lore.py",
        "grep -n approve .agents/scripts/hyper_lore.py",
        "git diff .agents/scripts/hyper_lore.py",
        "ruff check .agents/scripts/hyper_lore.py",
        "echo python3 hyper_lore.py approve ADR-0003",
        "python3 dev/scripts/review.py approve",
        # A mention elsewhere in the command says nothing about these segments.
        "bash dev/scripts/check.sh 2>&1 | tail -14; python3 .agents/scripts/hyper_lore.py validate",
        "source .venv/bin/activate && python3 .agents/scripts/hyper_lore.py validate",
        'python3 "$SCRIPT"; python3 .agents/scripts/hyper_lore.py validate',
        "python3 --version",
        "git status --short",
        "",
    ],
)
def test_hook_no_overmatch(project, command, mode):
    """A non-gated verb and text in quoted arguments or heredoc bodies get no decision (R6)."""
    proc = run_hook(project, command, mode)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "" and proc.stderr == ""
    assert tickets(project) == []


def write_pending(project: Path, **fields) -> None:
    """Write the pending record of ADR-0003."""
    directory = project / ".agents/state/lore/pending"
    directory.mkdir(parents=True)
    (directory / "ADR-0003.json").write_text(json.dumps(fields), encoding="utf-8")


def test_hook_reason_names_title_warrant_and_ids(project):
    """The reason carries the id, title, warrant and every id the pending record affects (R23)."""
    write_pending(
        project,
        title="Use the ledger",
        affects={
            "supersedes": ["ADR-0001", "ADR-0002"],
            "retires": [],
            "reanchors": ["CANON-0001"],
        },
    )
    command = (
        f"python3 hyper_lore.py approve ADR-0003 --nonce n --warrant-file {project}/warrant.txt"
    )
    assert asked(run_hook(project, command)) == (
        "Approve ADR-0003 Use the ledger; warrant: the user said yes; "
        "supersedes/retires/re-anchors: ADR-0001, ADR-0002, CANON-0001"
    )


def test_hook_reason_without_pending_record(project):
    """Without a pending record the reason says so, and hyper_lore.py refuses later."""
    command = (
        f"python3 hyper_lore.py approve ADR-0003 --nonce n --warrant-file {project}/warrant.txt"
    )
    assert "(title not in pending record)" in asked(run_hook(project, command))


def test_hook_strips_control_chars_and_caps_reason(project):
    """ANSI, control and format characters go; the cap never hides the affected ids (R6, R23)."""
    write_pending(
        project,
        title="Evil\x1b[31m red\x1b[0m\x07 \x1b]0;pwned\x07title‮" + "T" * 400,
        affects={"supersedes": ["ADR-0001"], "retires": [], "reanchors": []},
    )
    (project / "w.txt").write_text(
        "line one\nline\ttwo\x1b[2K\x9b1m" + "W" * 2000, encoding="utf-8"
    )
    command = f"python3 hyper_lore.py approve ADR-0003 --nonce n --warrant-file {project}/w.txt"
    reason = asked(run_hook(project, command))
    assert len(reason) <= 500
    assert not re.search(r"[\x00-\x1f\x7f-\x9f‮]", reason)
    assert "pwned" not in reason and "[31m" not in reason
    assert "line one line two" in reason
    assert reason.endswith("supersedes/retires/re-anchors: ADR-0001")


def test_hook_writes_ticket_on_gated_ask(project, monkeypatch, capsys):
    """One ticket binds verb, id and nonce and expires 120 s later (frozen clock)."""
    frozen = datetime(2026, 10, 21, 12, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(hyper_gate_hook, "_now", lambda: frozen)
    command = (
        f"python3 hyper_lore.py approve ADR-0003 --nonce n1 --warrant-file {project}/warrant.txt"
    )
    raw = json.dumps(
        {"permission_mode": "default", "cwd": str(project), "tool_input": {"command": command}}
    )
    assert hyper_gate_hook.decide(raw.encode(), {"CLAUDE_PROJECT_DIR": str(project)}) == 0
    assert json.loads(capsys.readouterr().out)["hookSpecificOutput"]["permissionDecision"] == "ask"
    found = tickets(project)
    assert len(found) == 1
    assert json.loads(found[0].read_text(encoding="utf-8")) == {
        "schema": "hacf.lore-ticket/v1",
        "verb": "approve",
        "id": "ADR-0003",
        "nonce": "n1",
        "created_at": "2026-10-21T12:00:00+00:00",
        "expires_at": (frozen + timedelta(seconds=120)).isoformat(),
    }


def test_hook_project_falls_back_to_cwd(project):
    """Without CLAUDE_PROJECT_DIR the ticket goes under the input cwd."""
    command = (
        f"python3 hyper_lore.py approve ADR-0003 --nonce n --warrant-file {project}/warrant.txt"
    )
    data = {"permission_mode": "default", "cwd": str(project), "tool_input": {"command": command}}
    env = clean_env()
    env.pop("CLAUDE_PROJECT_DIR", None)
    proc = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(data),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert asked(proc) and len(tickets(project)) == 1


def test_hook_refuses_symlinked_ticket_dir(project, tmp_path):
    """A ticket directory reached through a symlink is refused, and nothing lands outside."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (project / ".agents/state").symlink_to(outside, target_is_directory=True)
    command = (
        f"python3 hyper_lore.py approve ADR-0003 --nonce n --warrant-file {project}/warrant.txt"
    )
    assert run_hook(project, command).returncode == 2
    assert not list(outside.rglob("*.json"))


def test_hook_ticket_accepted_by_lore_check(project):
    """hyper_lore's own ticket check accepts the hook's ticket once, and only when it binds."""
    command = (
        f"python3 hyper_lore.py approve ADR-0003 --nonce n1 --warrant-file {project}/warrant.txt"
    )
    asked(run_hook(project, command))
    assert len(tickets(project)) == 1
    assert not hyper_lore.consume_ticket(project, "reject", "ADR-0003", "n1")
    assert not hyper_lore.consume_ticket(project, "approve", "ADR-0004", "n1")
    assert not hyper_lore.consume_ticket(project, "approve", "ADR-0003", "other")
    assert hyper_lore.consume_ticket(project, "approve", "ADR-0003", "n1")
    assert tickets(project) == []
    assert not hyper_lore.consume_ticket(project, "approve", "ADR-0003", "n1")


# ---------------------------------------------------------------------------
# Live test: markers live and claude, HACF_LIVE_CLAUDE=1, consent, $0.25 in total (R7, R27)
# ---------------------------------------------------------------------------

TOTAL_BUDGET = 0.25
CASE_BUDGET = "0.027"
MODEL = "haiku"
TOKEN = "ARCHIVE-TOKEN-7f3a"
GATED_CMD = "python3 .agents/scripts/hyper_lore.py approve ADR-0003 --nonce n1 --warrant-file w.txt"
FINISH_CMD = (
    "python3 dev/scripts/v060_gate.py finish S99 --miniprd spec/archive/MiniPRD_99_X_AUDITED.md"
)
ARCHIVE_CMD = "python3 .agents/scripts/archive_specs.py --miniprd spec/compiled/MiniPRD_99_X.md"
STUB = "import pathlib\npathlib.Path({marker!r}).write_text('ran')\n"
PROMPT = "Do exactly this one thing, then stop and say done. Do not try anything else. "
# name, permission mode, allowed tools, prompt
LIVE_CASES = [
    ("hook-ask-default", "default", f"Bash({GATED_CMD})", f"{PROMPT}Run with Bash: {GATED_CMD}"),
    ("hook-deny-bypass", "bypassPermissions", "", f"{PROMPT}Run with Bash: {GATED_CMD}"),
    ("cat-archive-default", "default", "Bash(cat *)", f"{PROMPT}Run with Bash: cat spec/archive/x"),
    (
        "read-archive-bypass",
        "bypassPermissions",
        "",
        f"{PROMPT}Use the Read tool on the file spec/archive/x and quote its content.",
    ),
    (  # Edit needs a prior Read, so this target is denied for Edit only (no Read deny to blur it).
        "edit-canon-bypass",
        "bypassPermissions",
        "",
        "Do exactly this, then stop and say done. Use the Read tool on lore/CANON.md, then the "
        "Edit tool on it to replace the word before with the word after.",
    ),
    (
        "gate-finish-default",
        "default",
        f"Bash({FINISH_CMD})",
        f"{PROMPT}Run with Bash: {FINISH_CMD}",
    ),
    ("gate-finish-bypass", "bypassPermissions", "", f"{PROMPT}Run with Bash: {FINISH_CMD}"),
    (
        "archive-specs-default",
        "default",
        f"Bash({ARCHIVE_CMD})",
        f"{PROMPT}Run with Bash: {ARCHIVE_CMD}",
    ),
    ("archive-specs-bypass", "bypassPermissions", "", f"{PROMPT}Run with Bash: {ARCHIVE_CMD}"),
]


def _live_env() -> dict[str, str]:
    """Return a child env without this session's Claude Code state."""
    env = clean_env()
    return {key: value for key, value in env.items() if not key.startswith("CLAUDE")}


def _live_repo(root: Path) -> None:
    """Build a temp repo with the rendered settings, the hook and marker-writing stubs."""
    (root / ".agents/scripts").mkdir(parents=True)
    (root / "dev/scripts").mkdir(parents=True)
    (root / "spec/archive").mkdir(parents=True)
    (root / "spec/compiled").mkdir(parents=True)
    shutil.copy(REPO_ROOT / ".agentignore", root / ".agentignore")
    shutil.copy(HOOK, root / ".agents/scripts/hyper_gate_hook.py")
    stubs = {
        ".agents/scripts/hyper_lore.py": "lore.marker",
        "dev/scripts/v060_gate.py": "gate.marker",
        ".agents/scripts/archive_specs.py": "archive.marker",
    }
    for rel, marker in stubs.items():
        (root / rel).write_text(STUB.format(marker=str(root / marker)), encoding="utf-8")
    (root / "spec/archive/x").write_text(f"{TOKEN}\n", encoding="utf-8")
    (root / "lore").mkdir()
    (root / "lore/CANON.md").write_text("before\n", encoding="utf-8")
    (root / "w.txt").write_text("live test warrant\n", encoding="utf-8")
    env = clean_env()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, env=env, check=True)
    subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "hyper_firewall.py"), "render", "--ides", "claude"],
        cwd=root,
        env=env,
        check=True,
        capture_output=True,
    )


def _tool_events(stdout: str) -> tuple[list[str], list[tuple[str, bool]]]:
    """Return (tool names the model called, (result text, is_error)) from stream-json stdout."""
    calls: list[str] = []
    results: list[tuple[str, bool]] = []
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        message = event.get("message")  # some system events carry a string here
        content = message.get("content") if isinstance(message, dict) else None
        for block in content if isinstance(content, list) else []:
            if block.get("type") == "tool_use":
                calls.append(block.get("name", ""))
            elif block.get("type") == "tool_result":
                results.append((json.dumps(block.get("content")), bool(block.get("is_error"))))
    return calls, results


def _cost(stdout: str) -> float:
    for line in reversed(stdout.splitlines()):
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("type") == "result":
            return float(event.get("total_cost_usd") or 0)
    return 0.0


@pytest.mark.live
@pytest.mark.claude
@pytest.mark.skipif(os.environ.get("HACF_LIVE_CLAUDE") != "1", reason="set HACF_LIVE_CLAUDE=1")
def test_live_claude_cases(tmp_path):
    """Run every live case; write verbatim captures and a verdict computed from them."""
    env = _live_env()
    version = subprocess.run(
        ["claude", "--version"], env=env, capture_output=True, text=True, check=True
    ).stdout.strip()
    RECORDED.mkdir(parents=True, exist_ok=True)
    spent = 0.0
    facts: dict[str, dict] = {}
    for number, (name, mode, allowed, prompt) in enumerate(LIVE_CASES, start=1):
        assert spent < TOTAL_BUDGET, f"budget used up before {name}: ${spent:.4f}"
        root = tmp_path / name
        root.mkdir()
        _live_repo(root)
        argv = [
            "claude",
            "-p",
            prompt,
            "--model",
            MODEL,
            "--permission-mode",
            mode,
            "--output-format",
            "stream-json",
            "--verbose",
            "--max-budget-usd",
            CASE_BUDGET,
            "--setting-sources",
            "project",
            "--strict-mcp-config",
            "--disable-slash-commands",
            "--no-session-persistence",
            "--tools",
            "Bash,Read,Edit",
        ]
        if allowed:
            argv += ["--allowedTools", allowed]
        proc = subprocess.run(
            argv, cwd=root, env=env, capture_output=True, text=True, check=False, timeout=300
        )
        spent += _cost(proc.stdout)
        capture = {
            "step": name,
            "argv": argv,
            "cwd": str(root),
            "exit_code": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "tool_version": version,
            "captured_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        }
        (RECORDED / f"{number:02d}-{name}.json").write_text(
            json.dumps(capture, indent=2) + "\n", encoding="utf-8"
        )
        calls, results = _tool_events(proc.stdout)
        ticket_dir = root / ".agents/state/lore/tickets"
        facts[name] = {
            "tool_calls": calls,
            "tool_errors": [text[:300] for text, is_error in results if is_error],
            "lore_ran": (root / "lore.marker").exists(),
            "gate_ran": (root / "gate.marker").exists(),
            "archive_ran": (root / "archive.marker").exists(),
            "tickets": len(list(ticket_dir.iterdir())) if ticket_dir.exists() else 0,
            "token_seen_in_results": any(TOKEN in text for text, _ in results),
            "edit_applied": "after" in (root / "lore/CANON.md").read_text(encoding="utf-8"),
        }

    verdicts = {
        "hook_ask_default_wrote_ticket_and_blocked": facts["hook-ask-default"]["tickets"] == 1
        and not facts["hook-ask-default"]["lore_ran"],
        "hook_deny_bypass_blocked_without_ticket": facts["hook-deny-bypass"]["tickets"] == 0
        and not facts["hook-deny-bypass"]["lore_ran"],
        "cat_archive_denied_default": bool(facts["cat-archive-default"]["tool_calls"])
        and not facts["cat-archive-default"]["token_seen_in_results"],
        "read_deny_enforced_bypass": bool(facts["read-archive-bypass"]["tool_calls"])
        and not facts["read-archive-bypass"]["token_seen_in_results"],
        "edit_deny_enforced_bypass": "Edit" in facts["edit-canon-bypass"]["tool_calls"]
        and not facts["edit-canon-bypass"]["edit_applied"],
        "gate_finish_runs_default": facts["gate-finish-default"]["gate_ran"],
        "gate_finish_runs_bypass": facts["gate-finish-bypass"]["gate_ran"],
        "archive_specs_runs_default": facts["archive-specs-default"]["archive_ran"],
        "archive_specs_runs_bypass": facts["archive-specs-bypass"]["archive_ran"],
    }
    verdict = {
        "tool_version": version,
        "captured_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "model": MODEL,
        "spent_usd": round(spent, 4),
        "permission_mode_sent": verdicts["gate_finish_runs_default"]
        and verdicts["gate_finish_runs_bypass"],
        "verdicts": verdicts,
        "facts": facts,
    }
    (RECORDED / "verdict.json").write_text(json.dumps(verdict, indent=2) + "\n", encoding="utf-8")
    assert spent <= TOTAL_BUDGET, f"spent ${spent:.4f}"
    # Every case must have reached a tool; a run where the model did nothing proves nothing.
    assert all(fact["tool_calls"] for fact in facts.values()), facts
