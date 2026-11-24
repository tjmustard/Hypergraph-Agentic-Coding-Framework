"""Tests for retiring the unwired stores: Final Truth, dead memory templates, handoffs rule (S10).

Ledger and lore checks run on a temp repository under tmp_path, never on the real files.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from conftest import REPO_ROOT, GitRepo

RETIRED_NAMES = (
    "compaction_engine",
    "interrupt_detector",
    "final_truth_template",
    "productContext",
    "systemPatterns",
    "spec/handoffs",
)
DELETED_FILES = (
    ".agents/scripts/compaction_engine.py",
    ".agents/scripts/interrupt_detector.py",
    ".agents/schemas/final_truth_template.md",
    ".agents/memory/productContext.md",
    ".agents/memory/systemPatterns.md",
)
# Clarifying question 2: the code paths. Skills, docs and instruction files are later slices'.
CODE_PATHS = (
    ".agents/scripts",
    ".agents/config",
    ".agents/rules",
    ".claude",
    ".windsurf",
    "HACF-install.sh",
    "HACF-uninstall.sh",
)
LEDGER_REL = "spec/compiled/architecture.yml"


def _code_files() -> list[Path]:
    """Return the tracked and untracked files under CODE_PATHS (no bytecode)."""
    out = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z", "--",
         *CODE_PATHS],
        cwd=REPO_ROOT, capture_output=True, check=True,
    ).stdout  # fmt: skip
    files = []
    for rel in filter(None, out.decode().split("\0")):
        path = REPO_ROOT / rel
        if "__pycache__" in rel or not path.is_file():
            continue
        # v0.5.x deletion data for the S12c uninstaller, not a code reference; R44.
        if rel == ".agents/config/legacy_file_hashes.json":
            continue
        # The v0.5.x migration reader by design: it reads productContext.md and systemPatterns.md
        # (Tasks 10-11); R46.
        if rel == ".agents/scripts/hyper_migrate.py":
            continue
        files.append(path)
    return files


def test_no_code_refs_to_retired_stores() -> None:
    """No code path names a retired store, and the retired files are gone."""
    files = _code_files()
    assert any(p.name == "hyper_ledger.py" for p in files)
    found = [
        (p.relative_to(REPO_ROOT).as_posix(), name)
        for p in files
        for name in RETIRED_NAMES
        if name.encode() in p.read_bytes()
    ]
    assert found == []
    for rel in DELETED_FILES:
        assert not (REPO_ROOT / rel).exists(), rel


def _ledger(repo: GitRepo, run_script, *args: str) -> subprocess.CompletedProcess:
    return run_script(".agents/scripts/hyper_ledger.py", *args, cwd=repo.path, env=repo.env)


def _ledger_with_context_compaction(repo: GitRepo, run_script) -> None:
    """Commit a temp ledger holding context_compaction, which has no inbound edges."""
    for args in (
        ("init",),
        ("ensure-node", "--id", "orchestration_layer", "--dimension", "Layer"),
        ("ensure-node", "--id", "context_compaction", "--dimension", "Module",
         "--member-of", "orchestration_layer"),
    ):  # fmt: skip
        proc = _ledger(repo, run_script, *args)
        assert proc.returncode == 0, proc.stderr
    repo.commit("temp ledger")


def _remove_context_compaction(repo: GitRepo, run_script) -> None:
    proc = _ledger(repo, run_script, "remove-node", "context_compaction", "--reason", "D9",
                   "--slice", "S10")  # fmt: skip
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_ledger_validate_passes_after_removal(tmp_git_repo: GitRepo, run_script) -> None:
    """After remove-node, validate passes and reports no dropped id against the old ledger."""
    repo = tmp_git_repo
    _ledger_with_context_compaction(repo, run_script)
    before = repo.scratch / "architecture.before.yml"
    shutil.copyfile(repo.path / LEDGER_REL, before)
    _remove_context_compaction(repo, run_script)
    proc = _ledger(repo, run_script, "validate")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    proc = _ledger(repo, run_script, "validate", "--json", "--against-file", str(before))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "dropped_node" not in proc.stdout
    assert "context_compaction" not in json.dumps(json.loads(proc.stdout).get("findings", []))
