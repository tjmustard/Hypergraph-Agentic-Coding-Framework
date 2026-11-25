"""Tests for HACF-uninstall.sh removing HACF's legacy leftovers only on proof (OPR.99.0.11.10, K9).

K9 must never remove a file that may hold a user's unfinished merge. A legacy hyper-tutorial pointer
goes only on a sha256 the uninstaller's own clone lists for that exact path; a
<STEM>.hacf-v0.6.0.md sidecar goes only when it equals the sidecar of the HACF block still in its
instruction file (a finished merge), checked before the block is removed. Every run happens in a
temp project; nothing is written inside the HACF checkout.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from .conftest import REPO_ROOT, run_uninstall_script

sys.path.insert(0, str(REPO_ROOT / ".agents" / "scripts"))
import hyper_blocks  # noqa: E402

CLAUDE_PTR = ".claude/commands/hyper-tutorial.md"
WINDSURF_PTR = ".windsurf/workflows/hyper-tutorial.md"
# The v0.5.x windsurf pointer (its sha256 is listed for that path in legacy_file_hashes.json).
WINDSURF_BYTES = (
    b'---\ndescription: "Step-by-step walkthrough of the Hypergraph framework for new users"\n---\n'
    b"Read `.agents/skills/hyper-tutorial/SKILL.md` and follow its instructions precisely.\n"
)
SIDECAR = "AGENTS.hacf-v0.6.0.md"
NO_URL = {"HACF_REPO_URL": None}


@pytest.fixture
def installed(project: Path, run_install) -> Path:
    """Return a project with a Claude Code install (manifest mode, AGENTS.md holds the block)."""
    run = run_install(project, "-y", "--ides=claude")
    assert run.returncode == 0, run.output
    return project


def _leftover(project: Path, rel: str) -> Path:
    """Make rel a file HACF's manifest does not list (what an upgrade leaves behind); return it."""
    manifest = project / ".agents/hacf-manifest.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["files"].pop(rel, None)
    manifest.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    path = project / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or path.exists():
        path.unlink()
    return path


def _block(agents: Path) -> bytes:
    doc = hyper_blocks.parse_doc(agents.read_bytes())
    block = hyper_blocks.find_block(hyper_blocks.scan(doc, hyper_blocks.MARKDOWN), "hacf")
    assert block is not None
    return b"".join(doc.lines[block.begin : block.end + 1])


def _uninstall(project: Path, scratch: Path, upstream, *args: str):
    return run_uninstall_script(scratch, upstream.url, project, *args, "-y", env=NO_URL)


def test_a_empty_claude_pointer_is_removed_in_manifest_mode(
    installed: Path, scratch: Path, upstream
) -> None:
    """(a) The empty v0.5.x pointer (the legacy sha) is removed and reported."""
    _leftover(installed, CLAUDE_PTR).write_bytes(b"")
    run = _uninstall(installed, scratch, upstream)
    assert run.returncode == 0, run.output
    assert not (installed / CLAUDE_PTR).exists()
    assert f"removed {CLAUDE_PTR} (HACF legacy file, sha matched)" in run.output


def test_a_empty_claude_pointer_is_removed_in_legacy_mode(
    v059_project, scratch: Path, upstream
) -> None:
    """(a) Legacy mode (no manifest): the same pointer goes."""
    project = v059_project("default")
    (project / CLAUDE_PTR).parent.mkdir(parents=True, exist_ok=True)
    (project / CLAUDE_PTR).write_bytes(b"")
    run = _uninstall(project, scratch, upstream)
    assert run.returncode == 0, run.output
    assert not (project / CLAUDE_PTR).exists()


def test_b_an_edited_claude_pointer_is_kept(installed: Path, scratch: Path, upstream) -> None:
    """(b) One byte added: kept and reported."""
    _leftover(installed, CLAUDE_PTR).write_bytes(b"\n")
    run = _uninstall(installed, scratch, upstream)
    assert run.returncode == 0, run.output
    assert (installed / CLAUDE_PTR).read_bytes() == b"\n"
    assert (
        f"kept {CLAUDE_PTR} (content differs from HACF's; delete it by hand if unwanted)"
        in run.output
    )


def test_c_the_windsurf_pointer_goes_by_its_own_sha(
    installed: Path, scratch: Path, upstream
) -> None:
    """(c) The legacy windsurf pointer, by its own listed sha, is removed."""
    (installed / WINDSURF_PTR).parent.mkdir(parents=True, exist_ok=True)
    (installed / WINDSURF_PTR).write_bytes(WINDSURF_BYTES)
    run = _uninstall(installed, scratch, upstream)
    assert run.returncode == 0, run.output
    assert not (installed / WINDSURF_PTR).exists()


@pytest.mark.parametrize("content", [WINDSURF_BYTES + b"x", b""])
def test_c_other_windsurf_content_is_kept(
    installed: Path, scratch: Path, upstream, content: bytes
) -> None:
    """(c) Different bytes, or the sha listed only for the claude pointer, are kept."""
    (installed / WINDSURF_PTR).parent.mkdir(parents=True, exist_ok=True)
    (installed / WINDSURF_PTR).write_bytes(content)
    run = _uninstall(installed, scratch, upstream)
    assert run.returncode == 0, run.output
    assert (installed / WINDSURF_PTR).read_bytes() == content
    assert f"kept {WINDSURF_PTR} (content differs" in run.output


def test_d_a_finished_merge_sidecar_is_removed(installed: Path, scratch: Path, upstream) -> None:
    """(d) AGENTS.md holds the block B and the sidecar equals sidecar_text(AGENTS.md, B)."""
    agents = installed / "AGENTS.md"
    sidecar = installed / SIDECAR
    sidecar.write_bytes(hyper_blocks.sidecar_text(Path("AGENTS.md"), _block(agents)))
    run = _uninstall(installed, scratch, upstream)
    assert run.returncode == 0, run.output
    assert not sidecar.exists()
    assert f"removed {SIDECAR} (merge finished" in run.output


def test_e_an_unfinished_merge_sidecar_is_kept(installed: Path, scratch: Path, upstream) -> None:
    """(e) K9 must never remove a possibly unfinished merge: no block, pristine sidecar, kept."""
    agents = installed / "AGENTS.md"
    pristine = hyper_blocks.sidecar_text(Path("AGENTS.md"), _block(agents))
    agents.write_bytes(b"# Our own instructions, no HACF block.\n")
    (installed / SIDECAR).write_bytes(pristine)
    run = _uninstall(installed, scratch, upstream)
    assert run.returncode == 0, run.output
    assert (installed / SIDECAR).read_bytes() == pristine
    assert f"kept {SIDECAR} (it may hold an unfinished merge; delete it by hand once merged)" in (
        run.output
    )


def test_f_an_edited_sidecar_is_kept(installed: Path, scratch: Path, upstream) -> None:
    """(f) The user added a note to the sidecar: kept."""
    agents = installed / "AGENTS.md"
    edited = hyper_blocks.sidecar_text(Path("AGENTS.md"), _block(agents)) + b"\nmy merge note\n"
    (installed / SIDECAR).write_bytes(edited)
    run = _uninstall(installed, scratch, upstream)
    assert run.returncode == 0, run.output
    assert (installed / SIDECAR).read_bytes() == edited


def test_g_a_different_block_version_is_kept(installed: Path, scratch: Path, upstream) -> None:
    """(g) AGENTS.md holds a block that differs by one byte from the sidecar's: kept."""
    agents = installed / "AGENTS.md"
    block = _block(agents)
    sidecar_bytes = hyper_blocks.sidecar_text(Path("AGENTS.md"), block)
    changed = block.replace(b"\n", b" \n", 2)  # one trailing space on the first body line
    assert changed != block
    agents.write_bytes(agents.read_bytes().replace(block, changed))
    (installed / SIDECAR).write_bytes(sidecar_bytes)
    run = _uninstall(installed, scratch, upstream)
    assert run.returncode == 0, run.output
    assert (installed / SIDECAR).read_bytes() == sidecar_bytes


def test_h_symlinks_are_not_followed(installed: Path, scratch: Path, upstream, tmp_path) -> None:
    """(h) A symlink at a pointer path or the sidecar path is kept and its target stays."""
    target = tmp_path / "outside.md"
    target.write_bytes(b"")
    _leftover(installed, CLAUDE_PTR).symlink_to(target)
    agents = installed / "AGENTS.md"
    pristine = hyper_blocks.sidecar_text(Path("AGENTS.md"), _block(agents))
    target2 = tmp_path / "outside-sidecar.md"
    target2.write_bytes(pristine)
    (installed / SIDECAR).symlink_to(target2)
    run = _uninstall(installed, scratch, upstream)
    assert run.returncode == 0, run.output
    assert (installed / CLAUDE_PTR).is_symlink() and target.read_bytes() == b""
    assert (installed / SIDECAR).is_symlink() and target2.read_bytes() == pristine


def test_dry_run_lists_each_decision_and_removes_nothing(
    installed: Path, scratch: Path, upstream
) -> None:
    """MR 4: the dry run names the decisions and leaves every file in place."""
    _leftover(installed, CLAUDE_PTR).write_bytes(b"")
    pristine = hyper_blocks.sidecar_text(Path("AGENTS.md"), _block(installed / "AGENTS.md"))
    (installed / SIDECAR).write_bytes(pristine)
    run = run_uninstall_script(scratch, upstream.url, installed, "--dry-run", env=NO_URL)
    assert run.returncode == 0, run.output
    assert f"{CLAUDE_PTR} (HACF legacy file, sha matched)" in run.output
    assert f"{SIDECAR} (merge finished" in run.output
    assert (installed / CLAUDE_PTR).is_file() and (installed / SIDECAR).read_bytes() == pristine
