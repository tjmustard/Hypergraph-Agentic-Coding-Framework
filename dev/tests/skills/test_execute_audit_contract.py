"""Contract tests for hyper_gates.py and the /hyper-execute, /hyper-audit, /hyper-lore-approve text.

The skills are prose, so most of this is grep. The exception is test_legacy_walkthrough: it runs
every command of the `bash legacy` fences, in order, in a temp git repository built from the flat
fixture, with its own copy of .agents/scripts, because archive_specs.py archives the spec/ beside
its own .agents/. Nothing here runs a gated lore verb (R6) and nothing writes inside the repository.
Autonomous behavior is proven later by the stub executor in S09a2.
"""

from __future__ import annotations

import json
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import hyper_gates
import pytest
import yaml
from conftest import FIXTURES_DIR, REPO_ROOT, SCRIPTS_DIR, GitRepo, clean_env

EXECUTE = REPO_ROOT / ".agents/skills/hyper-execute/SKILL.md"
AUDIT = REPO_ROOT / ".agents/skills/hyper-audit/SKILL.md"
LORE_APPROVE = REPO_ROOT / ".agents/skills/hyper-lore-approve/SKILL.md"
GATES = SCRIPTS_DIR / "hyper_gates.py"
FIXTURE = FIXTURES_DIR / "legacy_flat"
MINIPRD = "spec/compiled/MiniPRD_07_FlatWidget.md"
ARCHIVED = "spec/archive/MiniPRD_07_FlatWidget_AUDITED.md"
SID = "MiniPRD_07_FlatWidget"

LEGACY_FENCE = re.compile(r"^[ \t]*```bash legacy\n(.*?)^[ \t]*```", re.M | re.S)
GATED_VERB = re.compile(r"hyper_lore\.py (approve|reject|retire|reopen|review|approval-request)\b")
PLACEHOLDER = re.compile(r"<[A-Za-z_ ]+>")


def text_of(path: Path) -> str:
    """Return a file's text."""
    return path.read_text(encoding="utf-8")


def steps_of(text: str) -> list[str]:
    """Split a skill into its top-level numbered steps and headed phases."""
    return re.split(r"^(?=\d+\.\s|#{2,4}\s)", text, flags=re.M)


def phase(text: str, name: str) -> str:
    """Return the `### [<name>...` section of hyper-audit."""
    sections = [s for s in steps_of(text) if s.startswith(f"### [{name}:")]
    assert len(sections) == 1, name
    return sections[0]


def legacy_commands(path: Path) -> list[str]:
    """Return the commands of a skill's `bash legacy` fences, in document order."""
    blocks = LEGACY_FENCE.findall(text_of(path))
    return [line.strip() for block in blocks for line in block.splitlines() if line.strip()]


def run_gates(args: list[str], cwd: Path, **extra: str) -> subprocess.CompletedProcess:
    """Run hyper_gates.py under this interpreter with the HACF variables cleared."""
    env = {k: v for k, v in clean_env(**extra).items() if k in extra or not k.startswith("HACF_")}
    return subprocess.run(
        [sys.executable, str(GATES), *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("path", [EXECUTE, AUDIT], ids=["execute", "audit"])
def test_skill_text_contract(path: Path) -> None:
    """Resolve, no hand moves or ledger writes, python3 only, an autonomous branch per question."""
    text = text_of(path)
    assert "hyper_spec.py resolve" in text
    assert "mv spec/compiled" not in text
    assert not re.search(r"\bpython\b(?!3)", text), "bare python"
    assert "emoji" not in text.lower()
    assert "progress percentage" not in text
    for line in text.splitlines():
        if "architecture.yml" in line:
            assert "hypergraph_updater.py" in line, f"direct ledger write? {line}"
    asked = [step for step in steps_of(text) if "AskUserQuestion" in step]
    assert asked
    for step in asked:
        branch = [line for line in step.splitlines() if "Autonomous:" in line]
        assert branch, f"no Autonomous: branch in {step.splitlines()[0]}"
        assert "hyper_gates.py record" in branch[0]
        assert "stop" in branch[0]


def test_skill_text_autonomous_audit_verdict_only() -> None:
    """The autonomous Phase 4 writes only audit.json and names no proof, progress or ref command."""
    section = phase(text_of(AUDIT), "PHASE 4")
    branch = [line for line in section.splitlines() if "**Autonomous (engine):**" in line]
    assert len(branch) == 1
    assert re.findall(r"\$HACF_OUTBOX/\S+", branch[0]) == ["$HACF_OUTBOX/audit.json`,"]
    assert "Never write PROOF, PROGRESS or candidate refs" in branch[0]
    for command in ("hyper_spec", "--candidate-sha", "update-ref", "git "):
        assert command not in branch[0]


def test_autonomy_prompt_contract() -> None:
    """The engine's prompt states the mode, the gate call, the outbox and the git ban."""
    text = text_of(REPO_ROOT / ".agents/schemas/autonomy_prompt.md")
    for phrase in (
        "HACF_AUTONOMOUS=1",
        "hyper_gates.py record",
        "$HACF_OUTBOX",
        "$HACF_OUTBOX/audit.json",
        "Never write PROOF, PROGRESS or candidate refs",
        "git commit",
        "git push",
        "git checkout",
    ):
        assert phrase in text, phrase


def test_skill_text_unmapped_is_finding() -> None:
    """Phase 3 makes a non-empty unmapped list a finding with two ways out; execute reports it."""
    section = phase(text_of(AUDIT), "PHASE 3")
    assert "`unmapped` is a finding" in section
    assert "non-empty `unmapped`" in section
    assert "register a node" in section
    assert "accept the gap in writing in the audit report" in section
    assert "hyper_ledger.py review-template --json" in section
    assert "hyper_ledger.py apply-review --patch" in section
    assert "never hand-merged" in section
    assert "`unmapped`" in text_of(EXECUTE)


def test_skill_text_novel_outputs_quarantined() -> None:
    """Phase 2 routes novel outputs to the quarantine and records the gate."""
    section = phase(text_of(AUDIT), "PHASE 2")
    assert "tests/candidate_outputs/<SID>/" in section
    assert "novel_fixture_needed" in section


def test_lore_approve_lists_superseded_and_recommends_commit() -> None:
    """The question lists every affected id; the skill recommends a commit and never makes one."""
    text = text_of(LORE_APPROVE)
    assert "supersedes, retires or re-anchors" in text
    for option in ("Approve (becomes CANON)", "Reject (kept in the graveyard)", "Keep PROPOSED"):
        assert option in text
    assert "no approve-all" in text
    assert "one direct Bash call" in text
    assert "`autonomous`, refuse to run" in text
    assert "bypassPermissions, dontAsk and plan" in text
    assert "default or acceptEdits" in text
    last = [step for step in steps_of(text) if step.strip()][-1]
    assert "recommending a commit that touches only lore paths" in last
    assert "git commit" not in text
    assert "supersedes, retires or re-anchors" in phase(text_of(AUDIT), "PHASE 3.5")


def test_bridges_and_meta() -> None:
    """The bridges name both inputs, and the new skill has its bridges and a current model id."""
    for rel in (
        ".claude/commands/hyper-execute.md",
        ".claude/commands/hyper-audit.md",
        ".windsurf/workflows/hyper-execute.md",
        ".windsurf/workflows/hyper-audit.md",
    ):
        text = text_of(REPO_ROOT / rel)
        assert "SPEC.md" in text and "dot-id" in text and "legacy MiniPRD" in text, rel
    for bridge_dir in (".claude/commands", ".windsurf/workflows"):
        rel = f"{bridge_dir}/hyper-lore-approve.md"
        assert "`.agents/skills/hyper-lore-approve/SKILL.md`" in text_of(REPO_ROOT / rel), rel
    meta = yaml.safe_load(text_of(LORE_APPROVE.parent / "META.yml"))
    assert meta["assigned_model"] == "sonnet"
    assert meta["model_version"] == "claude-sonnet-5-5"


def test_legacy_walkthrough(tmp_git_repo: GitRepo, tmp_path: Path) -> None:
    """Every legacy command of both skills exits 0 on the flat fixture, execute then audit."""
    repo = tmp_git_repo
    shutil.copytree(FIXTURE, repo.path, dirs_exist_ok=True)
    shutil.copytree(
        SCRIPTS_DIR, repo.path / ".agents/scripts", ignore=shutil.ignore_patterns("__pycache__")
    )
    repo.commit("fixture")
    repo.write("src/widget.txt", "widget v2\n")  # the Builder's change

    patch = tmp_path / "patch.json"
    body = tmp_path / "body.md"
    body.write_text("proof body\n", encoding="utf-8")
    values = {
        "<spec>": MINIPRD,
        "<SID>": SID,
        "<NN>": "07",
        "<base>": "HEAD",
        "<node_ids>": "flat_widget",
        "<radius_ids>": "flat_widget",
        "<note>": "flat fixture note",
        "<one line>": "walkthrough",
        "<patch_file>": str(patch),
        "<body>": str(body),
    }
    execute, audit = legacy_commands(EXECUTE), legacy_commands(AUDIT)
    commands = execute + audit
    joined = "\n".join(commands)
    for step in (
        "hyper_spec.py resolve",
        "ensure-node --from-spec",
        "hypergraph_updater.py spec/compiled/architecture.yml",
        "hyper_spec.py progress",
        "note --legacy",
        "hyper_ledger.py review-template",
        "hypergraph_updater.py --from-diff",
        "hyper_ledger.py apply-review",
        "lore-check",
        "hyper_spec.py proof",
        "archive_specs.py --miniprd",
    ):
        assert step in joined, f"no legacy command for {step}"
    assert not GATED_VERB.search(joined), "a walkthrough must never run a gated lore verb (R6)"

    outputs: dict[str, str] = {}
    for command in commands:
        filled = command
        for token, value in values.items():
            filled = filled.replace(token, value)
        assert not PLACEHOLDER.search(filled), f"undocumented placeholder: {filled}"
        argv = shlex.split(filled)
        if argv[0] == "python3":  # run under this leg's interpreter, not PATH's
            argv[0] = sys.executable
        proc = repo.run(argv)
        assert proc.returncode == 0, f"{filled}\n{proc.stdout}\n{proc.stderr}"
        outputs[command] = proc.stdout
        if "review-template" in command:
            nodes = json.loads(proc.stdout)["nodes"]
            patch.write_text(json.dumps({node["id"]: {} for node in nodes}), encoding="utf-8")

    def output_of(fragment: str) -> str:
        return "\n".join(out for command, out in outputs.items() if fragment in command)

    assert json.loads(output_of("hyper_spec.py resolve"))["layout"] == "legacy"
    assert "progress skipped" in output_of("--event executed")
    assert "progress skipped" in output_of("--event audited")
    assert "proof skipped" in output_of("hyper_spec.py proof")
    assert "flat_widget" in json.loads(output_of("--from-diff"))["nodes"]
    assert [row["id"] for row in json.loads(output_of("lore-check"))] == ["ADR-0001"]
    assert not (repo.path / MINIPRD).exists()
    assert (repo.path / ARCHIVED).is_file()
    notes = (repo.path / ".agents/memory/activeContext.md").read_text(encoding="utf-8")
    assert "## Field Notes (legacy)" in notes and "flat fixture note" in notes
    ledger = yaml.safe_load((repo.path / "spec/compiled/architecture.yml").read_text("utf-8"))
    assert {node["id"]: node["status"] for node in ledger["nodes"]}["flat_widget"] == "clean"
    for validate in (".agents/scripts/hyper_ledger.py", ".agents/scripts/hyper_lore.py"):
        proc = repo.run([sys.executable, validate, "validate"])
        assert proc.returncode == 0, f"{validate}\n{proc.stdout}\n{proc.stderr}"


def test_gates_mode_and_record(tmp_path: Path) -> None:
    """Record appends one +00:00 JSON line to the outbox; without one it only prints."""
    args = ["record", "--gate", "low_confidence", "--slice", "S99", "--reason", "score 7\nof 10"]
    outbox = tmp_path / "outbox"
    proc = run_gates(args, tmp_path, HACF_OUTBOX=str(outbox))
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == ""
    lines = (outbox / "gates.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert {k: record[k] for k in ("gate", "slice", "reason")} == {
        "gate": "low_confidence",
        "slice": "S99",
        "reason": "score 7\nof 10",
    }
    assert record["at"].endswith("+00:00")

    run_gates(args, tmp_path, HACF_OUTBOX=str(outbox))
    assert len((outbox / "gates.jsonl").read_text(encoding="utf-8").splitlines()) == 2

    bare = tmp_path / "bare"
    bare.mkdir()
    proc = run_gates(args, bare)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["gate"] == "low_confidence"
    assert list(bare.iterdir()) == []

    assert run_gates(["record", "--gate", "x"], bare).returncode == 2
    assert run_gates([], bare).returncode == 2


def test_gates_mode_only_hacf_autonomous(tmp_path: Path) -> None:
    """Mode reads only HACF_AUTONOMOUS, and only the value 1 means autonomous (R7)."""
    for extra in (
        {},
        {"CI": "1"},
        {"CLAUDE_CODE_ENTRYPOINT": "sdk-cli", "CLAUDE_CODE_PERMISSION_MODE": "dontAsk"},
        {"HACF_OUTBOX": str(tmp_path)},
        {"HACF_AUTONOMOUS": "0"},
        {"HACF_AUTONOMOUS": ""},
    ):
        assert run_gates(["mode"], tmp_path, **extra).stdout == "interactive\n", extra
    assert run_gates(["mode"], tmp_path, HACF_AUTONOMOUS="1").stdout == "autonomous\n"


def test_gates_lore_check_selection(tmp_git_repo: GitRepo) -> None:
    """Live nodes needing a decision are selected by source or by a stale blast-radius anchor."""

    def node(nid: str, status: str = "PROPOSED", source: str = "other", **rest: object) -> dict:
        anchors = {"components": rest.pop("components", [])}
        return {
            "id": nid,
            "status": status,
            "provenance": {"source": source},
            "anchors": anchors,
            "stale": rest.get("stale", False),
        }

    nodes = [
        node("BY-SLICE", source="S99"),
        node("BY-PATH", source=MINIPRD),
        node("OTHER"),
        node("DECIDED", status="CANON", source="S99"),
        node("REJECTED", status="REJECTED", source="S99", stale=True, components=["a"]),
        node("STALE-IN", status="CANON", stale=True, components=["a", "z"]),
        node("STALE-OUT", stale=True, components=["z"]),
        node("FRESH-IN", components=["a"]),
    ]
    rows = hyper_gates.select_nodes(nodes, {"S99", MINIPRD}, {"a", "b"})
    assert {row["id"]: row["selected_by"] for row in rows} == {
        "BY-SLICE": ["source"],
        "BY-PATH": ["source"],
        "STALE-IN": ["blast_radius"],
    }
    assert [row["id"] for row in hyper_gates.select_nodes(nodes, {"S99"}, set())] == ["BY-SLICE"]

    # No lore graph: nothing to select, and the check still succeeds.
    proc = run_gates(["lore-check", "--slice", "S99", "--json"], tmp_git_repo.path)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == []
