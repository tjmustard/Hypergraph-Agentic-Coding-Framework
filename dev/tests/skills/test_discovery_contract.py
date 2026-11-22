"""Contract tests for the /hyper-discover, /hyper-baseline, /hyper-grill-docs, /hyper-status text.

The skills are prose, so most checks are greps. The init test runs discover's documented ledger
commands in a throwaway repository under tmp_path; nothing here writes inside the repository.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest
import yaml
from conftest import REPO_ROOT, SCRIPTS_DIR

SKILLS = REPO_ROOT / ".agents" / "skills"
DISCOVER = SKILLS / "hyper-discover" / "SKILL.md"
BASELINE = SKILLS / "hyper-baseline" / "SKILL.md"
GRILL = SKILLS / "hyper-grill-docs" / "SKILL.md"
ADR_FORMAT = SKILLS / "hyper-grill-docs" / "ADR-FORMAT.md"
STATUS = SKILLS / "hyper-status" / "SKILL.md"
LEDGER = "spec/compiled/architecture.yml"
LEDGER_SCRIPT = str(SCRIPTS_DIR / "hyper_ledger.py")

LEDGER_CMD = re.compile(r"`(python3 \.agents/scripts/hyper_ledger\.py [^`]+)`")
# A write verb before the ledger file on one line; allowed only when the line forbids it.
LEDGER_WRITE = re.compile(r"\b(save|write|create|update|edit|modify)s?\b.*architecture\.yml", re.I)
GATED_VERB = re.compile(r"hyper_lore\.py (approve|reject|retire|reopen|review|approval-request)\b")
REQUIRED = {
    DISCOVER: (
        "hyper_codegraph.py index --json",
        "hyper_codegraph.py symbol_impact",
        "ensure-node --id <id> --dimension Module --parent <layer_id> --files <globs>",
        "set-edges --id <caller_node> --add-depends-on",
        "mark --needs-review --ids",
        "hypergraph_updater.py spec/compiled/architecture.yml <ids>",
    ),
    BASELINE: (
        "hyper_scope.py project init --check-only --json",
        "hyper_scope.py project init --intent",
        "hyper_scope.py project update --intent",
        "write nothing",
        "Current Core Value Loop",
        "Existing Data Models",
        "Established Patterns",
        "Identified Technical Debt",
    ),
    GRILL: ("hyper_lore.py propose --type decision --title", "--body-file", "lore/ADRs/"),
    ADR_FORMAT: (
        "lore/ADRs/ADR-NNNN-<slug>.md",
        "hyper_lore.py propose --type decision",
        "/hyper-lore-approve",
        "## When to Offer an ADR",
    ),
    STATUS: (
        "root `SPEC.md`",
        "lore/MAP.md",
        "hyper_spec.py status --json",
        "hyper_lore.py list --json",
        "stale CANON nodes (binding until retired or superseded)",
        "PROPOSED nodes with `origin: user-decision` (binding until rejected)",
        "REJECTED, SUPERSEDED and RETIRED lore nodes",
        "`SuperPRD_*.md`, `MiniPRD_*.md`",
    ),
}
IDS = ["discover", "baseline", "grill-docs", "adr-format", "status"]


def lines_of(path: Path) -> list[str]:
    """Return a file's lines."""
    return path.read_text(encoding="utf-8").splitlines()


@pytest.mark.parametrize("path", list(REQUIRED), ids=IDS)
def test_discovery_contract(path: Path) -> None:
    """No docs/adr writes, memory-template reads, ledger writes, SuperPRD.md or bare python."""
    lines = lines_of(path)
    text = "\n".join(lines)
    for line in lines:
        if "docs/adr" in line:
            assert "import" in line, line
        if LEDGER_WRITE.search(line):
            assert "never" in line.lower() or "hypergraph_updater.py" in line, line
    assert "productContext" not in text and "systemPatterns" not in text
    assert "SuperPRD.md" not in text
    assert not re.search(r"\bpython\b(?!3)", text), "bare python"
    assert not GATED_VERB.search(text), "a gated lore verb"
    for phrase in REQUIRED[path]:
        assert phrase in text, phrase


def test_discover_edges_from_code_graph() -> None:
    """With a backend, edges come from the code graph; LLM-read edges only with backend none."""
    lines = lines_of(DISCOVER)
    assert any("edges come from the code graph" in line and "backend" in line for line in lines)
    llm = [line for line in lines if re.search(r"llm|imports", line, re.I)]
    assert llm and all("`none`" in line for line in llm), llm
    assert any("needs_review" in line or "--needs-review" in line for line in llm)
    assert "Trace the data flow" not in "\n".join(lines)


def test_discover_maps_callers_by_bare_name() -> None:
    """Search matches bare names: a caller is searched by its last segment, kept by full name."""
    text = DISCOVER.read_text(encoding="utf-8")
    step = next(line for line in text.splitlines() if "<caller_name>" in line)
    assert "search --pattern '^<caller_name>$'" in step
    assert "last dot-separated segment of the caller's qualified name" in step
    assert "whose `symbol` equals that qualified name" in step
    assert "no such row" in step and "step 5" in step
    assert "'^<caller>$'" not in text


def test_discover_calls_ledger_init_when_missing(tmp_git_repo, tmp_path: Path) -> None:
    """Discover runs init first on a missing ledger; a Module under a Layer validates (R21)."""
    text = DISCOVER.read_text(encoding="utf-8")
    commands = LEDGER_CMD.findall(text)
    init = "python3 .agents/scripts/hyper_ledger.py init"
    assert commands[0] == init
    assert "does not exist" in next(line for line in text.splitlines() if init in line)
    layer = next(c for c in commands if "--dimension Layer" in c)
    module = next(c for c in commands if "--dimension Module --parent" in c)
    description = tmp_path / "description.txt"
    description.write_text("A test node.\n", encoding="utf-8")
    fill = {
        "<layer_id>": "app_layer",
        "<id>": "core",
        "<globs>": "src/core/**",
        "<file>": str(description),
    }
    ledger = tmp_git_repo.path / LEDGER
    assert not ledger.exists()
    for command in (init, layer, module):
        words = [fill.get(word, word) for word in command.split()[2:]]
        proc = tmp_git_repo.run([sys.executable, LEDGER_SCRIPT, *words], HACF_OUTBOX="")
        assert proc.returncode == 0, (command, proc.stderr)
        if command == init:
            empty = yaml.safe_load(ledger.read_text(encoding="utf-8"))
            assert empty == {"schema": "hacf.ledger/v2", "nodes": []}
    nodes = {n["id"]: n for n in yaml.safe_load(ledger.read_text(encoding="utf-8"))["nodes"]}
    assert nodes["core"]["edges"]["member_of"] == ["app_layer"]
    assert not nodes["core"]["edges"].get("implements")
    assert nodes["core"]["files"] == ["src/core/**"]
    proc = tmp_git_repo.run([sys.executable, LEDGER_SCRIPT, "validate", "--json"], HACF_OUTBOX="")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert json.loads(proc.stdout)["errors"] == []
