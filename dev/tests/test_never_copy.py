"""NEVER_COPY is enforced by HACF-install.sh (S12b Test 14, dev/design/v0.6.0/schemas.md).

A fresh install from an upstream that carries every NEVER_COPY path, each holding a sentinel,
leaves no sentinel in the project. The only NEVER_COPY paths that may exist afterwards are the
ones HACF creates or merges itself (activeContext.md from its template, the scaffold's root
items and directories, the empty lore graph created by `hyper_lore.py init`, and the merge-only
files), and none of them is a copy.
"""

from __future__ import annotations

import re
from pathlib import Path

from installer.conftest import INSTALLER, build_upstream, git, git_env, run_installer

SENTINEL = "NEVER-COPY-SENTINEL-7f3c"

# schemas.md#target-layout, NEVER_COPY, one representative path each.
NEVER_COPY_PATHS = (
    "SPEC.md",
    "project.yaml",
    "workspace.yaml",
    "lore_hypergraph.yml",
    ".gitattributes",
    "lore/ADRs/ADR-0999-upstream.md",
    "missions/release-9.9.9/SPEC.md",
    "exhaust/run.log",
    ".openrig/state.json",
    "openrig/hacf/rig.yaml",
    "spec/compiled/architecture.yml",
    "tests/fixtures/upstream.txt",
    "dev/tests/upstream.py",
    "PRDs/v9/upstream.md",
    "docs/upstream.md",
    ".agents/local/hacf.yml",
    ".agents/state/upstream.json",
    ".agents/.backup/upstream.txt",
    ".agents/logs/upstream.jsonl",
    ".agents/memory/activeContext.md",
    ".claude/settings.json",
    ".claude/settings.local.json",
    ".mcp.json",
    ".cursor/mcp.json",
    ".roo/mcp.json",
    ".gitignore",
    "CHANGELOG.md",
    "README.md",
    "CITATION.cff",
    "pyproject.toml",
    "uv.lock",
    "requirements.txt",
    ".agent",
    "HACF-ROOT-ONLY.md",  # an HACF root file on no copy list
)
# Created or merged by HACF itself, never copied: they may exist, without the sentinel.
MAY_EXIST = {
    "SPEC.md",
    "project.yaml",
    "workspace.yaml",
    "lore_hypergraph.yml",
    ".agents/local/hacf.yml",
    ".agents/memory/activeContext.md",
    ".claude/settings.json",
    ".gitignore",
}
EXPECTED_ARRAY = (
    "SPEC.md project.yaml workspace.yaml lore_hypergraph.yml .gitattributes lore/ missions/ "
    "exhaust/ .openrig/ openrig/ spec/ tests/ dev/ PRDs/ docs/ .agents/local/ .agents/state/ "
    ".agents/.backup/ .agents/logs/ .agents/memory/ .claude/settings*.json .mcp.json "
    ".cursor/mcp.json .roo/mcp.json .gitignore CHANGELOG.md README.md CITATION.cff "
    "pyproject.toml uv.lock requirements.txt .agent"
).split()


def test_never_copy_list_matches_schema() -> None:
    """The installer's NEVER_COPY array is exactly the schemas.md list."""
    text = INSTALLER.read_text(encoding="utf-8")
    body = re.search(r"^NEVER_COPY=\((.*?)^\)", text, re.S | re.M)
    assert body is not None
    items = re.findall(r'"([^"]+)"', body.group(1))
    assert items == EXPECTED_ARRAY


def test_never_copy(tmp_path: Path) -> None:
    """A fresh install from an upstream holding every NEVER_COPY path copies none of them."""
    scratch = tmp_path / "scratch"
    upstream = build_upstream(
        tmp_path / "upstream",
        scratch,
        {rel: f"{SENTINEL} {rel}\n" for rel in NEVER_COPY_PATHS},
    )
    project = tmp_path / "project"
    project.mkdir()
    git(["init", "-q", "-b", "main"], project, git_env(scratch))
    run = run_installer(scratch, upstream.url, project, "-y", "--ides=all")
    assert run.returncode == 0, run.output
    assert "refused (NEVER_COPY): .cursor/mcp.json" in run.output
    assert "refused (NEVER_COPY): .roo/mcp.json" in run.output
    for rel in NEVER_COPY_PATHS:
        path = project / rel
        if rel in MAY_EXIST:
            assert not path.is_file() or SENTINEL not in path.read_text(encoding="utf-8"), rel
        else:
            assert not path.exists(), rel
    leaked = [
        p.relative_to(project).as_posix()
        for p in project.rglob("*")
        if p.is_file()
        and ".git" not in p.relative_to(project).parts
        and SENTINEL.encode() in p.read_bytes()
    ]
    assert leaked == []
    assert (project / ".agents/memory/activeContext.md").is_file()  # created when missing
