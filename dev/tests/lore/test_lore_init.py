"""Deterministic tests for `hyper_lore.py init` (S15a2, R49; MiniPRD_27 Tests 7-9).

Every test works in a fresh temp git repository (conftest's tmp_git_repo) with no lore/ and no
graph. init runs in-process through hyper_lore.main, with the working directory inside the temp
repository; nothing writes into this checkout.
"""

from __future__ import annotations

import io
import os
import sys
from pathlib import Path

import hyper_lore
import pytest
import yaml
from conftest import GitRepo

GRAPH = "lore_hypergraph.yml"
MAP = "lore/MAP.md"
CANON_MD = "lore/CANON.md"
WATCHED = (GRAPH, MAP, CANON_MD)
HACF_ENV = ("HACF_OUTBOX", "HACF_AUTONOMOUS", "HACF_SLICE", "HACF_RUN_ID")
EMPTY = {"schema": "hacf.lore/v1", "id_counters": {"ADR": 0, "STORY": 0, "CANON": 0}, "nodes": []}


@pytest.fixture
def repo(tmp_git_repo: GitRepo, monkeypatch: pytest.MonkeyPatch) -> GitRepo:
    """Return a fresh repository (no lore/, no graph) with the working directory inside it."""
    for key in list(os.environ):
        if key.startswith(("GIT_", "GITHEAD_")) or key in HACF_ENV:
            monkeypatch.delenv(key)
    tmp_git_repo.git("config", "user.name", "Lore Tester")
    tmp_git_repo.git("config", "user.email", "lore@example.invalid")
    for key, value in tmp_git_repo.env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    monkeypatch.chdir(tmp_git_repo.path)
    assert not os.path.lexists(tmp_git_repo.path / "lore")
    assert not os.path.lexists(tmp_git_repo.path / GRAPH)
    return tmp_git_repo


def lore(capsys: pytest.CaptureFixture, *args: str) -> tuple[int, str, str]:
    """Run hyper_lore.main in-process; return (exit code, stdout, stderr)."""
    capsys.readouterr()
    code = hyper_lore.main(list(args))
    out, err = capsys.readouterr()
    return code, out, err


def state(root: Path) -> dict[str, bytes | None]:
    """Return the bytes of the graph, MAP.md and CANON.md (None when absent)."""
    found: dict[str, bytes | None] = {}
    for rel in WATCHED:
        path = root / rel
        found[rel] = path.read_bytes() if os.path.lexists(path) else None
    return found


def paths(root: Path) -> list[str]:
    """Return every path under root outside .git, sorted."""
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if ".git" not in p.parts)


def test_lore_init_creates_empty_graph(repo: GitRepo, capsys) -> None:
    """Test 7: init writes the empty hacf.lore/v1 graph, MAP.md and CANON.md; validate passes."""
    code, out, err = lore(capsys, "init")
    assert code == 0, err
    assert GRAPH in out and MAP in out and CANON_MD in out
    data = yaml.safe_load((repo.path / GRAPH).read_text(encoding="utf-8"))
    assert data == EMPTY
    assert (repo.path / MAP).is_file()
    assert (repo.path / CANON_MD).is_file()
    code, out, err = lore(capsys, "validate")
    assert code == 0, out + err
    code, out, err = lore(capsys, "index")
    assert code == 0, err
    assert out.strip() == "index: no change"


def _with_proposed_node(repo: GitRepo, capsys) -> None:
    assert lore(capsys, "init")[0] == 0
    body = repo.path.parent / "body.md"
    body.write_text("# A decision\n\nWe decided it.\n", encoding="utf-8")
    code, _, err = lore(
        capsys, "propose", "--type", "decision", "--title", "A decision", "--body-file", str(body)
    )
    assert code == 0, err
    assert (repo.path / MAP).is_file() and (repo.path / CANON_MD).is_file()


def _empty_file(repo: GitRepo, capsys) -> None:
    (repo.path / GRAPH).write_bytes(b"")


def _unparsable(repo: GitRepo, capsys) -> None:
    (repo.path / GRAPH).write_bytes(b"schema: [unclosed\n  - :\n")


@pytest.mark.parametrize("make", [_with_proposed_node, _empty_file, _unparsable])
def test_lore_init_refuses_existing(repo: GitRepo, capsys, make) -> None:
    """Test 8: any existing graph makes init exit 4 with every byte (and absence) unchanged."""
    make(repo, capsys)
    before = state(repo.path)
    paths_before = paths(repo.path)
    code, _, err = lore(capsys, "init")
    assert code == 4
    assert GRAPH in err
    assert state(repo.path) == before
    assert paths(repo.path) == paths_before  # nothing created


def test_lore_init_uses_locked_writer(repo: GitRepo, capsys, monkeypatch) -> None:
    """Test 9: init waits on lore.lock (exit 3), is refused under HACF_OUTBOX, writes via CAS."""
    monkeypatch.setattr(hyper_lore, "LOCK_TIMEOUT", 0.3)
    with hyper_lore.lore_lock(repo.path):
        code, _, err = lore(capsys, "init")
    assert code == 3, err
    assert state(repo.path) == dict.fromkeys(WATCHED)

    monkeypatch.setenv("HACF_OUTBOX", str(repo.path.parent / "outbox"))
    code, _, err = lore(capsys, "init")
    assert code == 4, err
    assert state(repo.path) == dict.fromkeys(WATCHED)
    assert not (repo.path.parent / "outbox").exists()
    monkeypatch.delenv("HACF_OUTBOX")

    calls: list[Path] = []
    real = hyper_lore._cas_write

    def spy(path: Path, original, text: str) -> None:
        calls.append(Path(path))
        real(path, original, text)

    monkeypatch.setattr(hyper_lore, "_cas_write", spy)
    code, _, err = lore(capsys, "init")
    assert code == 0, err
    assert repo.path / GRAPH in calls
    assert yaml.safe_load((repo.path / GRAPH).read_text(encoding="utf-8")) == EMPTY
