"""Tests for upgrades that reuse the previous install's record (slice 32, OPR.0.6.0.32; R58).

An upgrade of a --source install with no --source, --repo-url or --branch takes the recorded
checkout's current HEAD, and refuses when that checkout is unusable. An upgrade without --ides
keeps the manifest's recorded `ides`. Every run happens in a temp project; nothing is written
inside the HACF checkout.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from .conftest import (
    INSTALLER,
    add_commit,
    git_env,
    install_env,
    make_local_source,
    run_installer,
    snapshot,
)
from .test_uninstall import run_in_pty

WRITTEN = "Nothing was written."
MANIFEST_REL = ".agents/hacf-manifest.json"
NO_URL = {"HACF_REPO_URL": None}  # HACF_TEST stays 1; without a URL the record is read
OTHER_IDE_DIRS = (".cursor", ".windsurf", ".clinerules", ".roo")
ALL_IDES = sorted(["claude", "antigravity", "windsurf", "cursor", "cline", "roo", "universal"])


def manifest_of(project: Path) -> dict:
    """Return the project's parsed manifest."""
    return json.loads((project / MANIFEST_REL).read_text(encoding="utf-8"))


def write_manifest(project: Path, manifest: dict) -> None:
    """Replace the project's manifest (to simulate an older one)."""
    (project / MANIFEST_REL).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Test 5 (US-004): the upgrade takes the recorded checkout's HEAD, or refuses
# ---------------------------------------------------------------------------


def test_upgrade_uses_recorded_source(tmp_path: Path, project: Path, scratch: Path) -> None:
    """Test 5: c1 installed, c2 committed, plain -y upgrades to c2; a removed source refuses."""
    src, c1 = make_local_source(tmp_path / "local-source", tmp_path / "source-scratch")
    run = run_installer(scratch, "file:///nonexistent", project, "-y", "--ides=claude",
                        f"--source={src}", f"--ref={c1}")  # fmt: skip
    assert run.returncode == 0, run.output
    assert manifest_of(project)["installed_commit"] == c1
    c2 = add_commit(src, git_env(scratch), "c2")

    run = run_installer(scratch, "file:///nonexistent", project, "-y", env=NO_URL)
    assert run.returncode == 0, run.output
    recorded = "source: recorded by the previous install (pass --repo-url to switch to a remote)"
    assert recorded in run.output
    assert f"source: local {src.resolve()} @ {c2} (ref HEAD)" in run.output
    manifest = manifest_of(project)
    assert manifest["installed_commit"] == c2
    assert manifest["source"] == {"kind": "local", "commit": c2}
    record = json.loads((project / ".agents/state/install/source.json").read_text(encoding="utf-8"))
    assert record["commit"] == c2 and record["path"] == str(src.resolve())

    shutil.rmtree(src)
    before = snapshot(project)
    run = run_installer(scratch, "file:///nonexistent", project, "-y", env=NO_URL)
    assert run.returncode == 1, run.output
    assert WRITTEN in run.output
    assert "this project was installed from a local HACF checkout" in run.output
    assert "the source not a directory" in run.output
    assert "Pass --source=<checkout> or --repo-url=<https url>" in run.output
    assert snapshot(project) == before


def test_remote_flags_override_record(
    tmp_path: Path, project: Path, scratch: Path, upstream
) -> None:
    """Test 5: under HACF_TEST=1 a set HACF_REPO_URL overrides the record (the remote path)."""
    src, _ = make_local_source(tmp_path / "local-source", tmp_path / "source-scratch")
    run = run_installer(scratch, upstream.url, project, "-y", "--ides=claude", f"--source={src}")
    assert run.returncode == 0, run.output
    shutil.rmtree(src)  # the record is unusable, but it is never read
    run = run_installer(scratch, upstream.url, project, "-y")
    assert run.returncode == 0, run.output
    assert "recorded by the previous install (pass" not in run.output
    assert manifest_of(project)["source"]["kind"] == "remote"
    assert manifest_of(project)["installed_commit"] == upstream.sha


# ---------------------------------------------------------------------------
# Tests 6-7 (US-005): the recorded IDE selection
# ---------------------------------------------------------------------------


def test_upgrade_keeps_recorded_ides(project: Path, run_install) -> None:
    """Test 6: an -y upgrade without --ides keeps claude only; --ides= replaces the record."""
    run = run_install(project, "-y", "--ides=claude")
    assert run.returncode == 0, run.output
    assert manifest_of(project)["ides"] == ["claude"]

    run = run_install(project, "-y")
    assert run.returncode == 0, run.output
    recorded = "IDE selection: claude (recorded by the previous install; use --ides= to change)"
    assert recorded in run.output
    assert "IDEs: claude (recorded)" in run.output
    for rel in OTHER_IDE_DIRS:
        assert not (project / rel).exists(), rel
    assert manifest_of(project)["ides"] == ["claude"]

    run = run_install(project, "-y", "--ides=cursor,claude,cursor")
    assert run.returncode == 0, run.output
    assert "(recorded)" not in run.output
    assert manifest_of(project)["ides"] == ["claude", "cursor"]
    assert (project / ".cursor").is_dir()


def test_skills_mode_and_old_manifest(project: Path, run_install) -> None:
    """Test 7: --mode=skills keeps `ides`; a manifest without `ides` keeps the `all` default."""
    run = run_install(project, "-y", "--ides=claude,cursor")
    assert run.returncode == 0, run.output
    run = run_install(project, "-y", "--mode=skills")
    assert run.returncode == 0, run.output
    assert manifest_of(project)["ides"] == ["claude", "cursor"]

    manifest = manifest_of(project)
    del manifest["ides"]
    write_manifest(project, manifest)
    run = run_install(project, "-y")
    assert run.returncode == 0, run.output
    assert "IDE selection: all (non-interactive default" in run.output
    assert manifest_of(project)["ides"] == ALL_IDES


def test_unknown_recorded_ide_dropped(project: Path, run_install) -> None:
    """Task 7: an id unknown to IDE_DEFS is dropped with a warning; the rest is kept."""
    run = run_install(project, "-y", "--ides=claude")
    assert run.returncode == 0, run.output
    manifest = manifest_of(project)
    manifest["ides"] = ["claude", "vim"]
    write_manifest(project, manifest)
    run = run_install(project, "-y")
    assert run.returncode == 0, run.output
    assert "ignoring unknown IDE id 'vim'" in run.output
    assert manifest_of(project)["ides"] == ["claude"]


# ---------------------------------------------------------------------------
# Test 8 (US-005): the interactive prompt defaults to the record
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("typed", "expected"), [("", ["claude", "cursor"]), ("a", ALL_IDES)])
def test_interactive_prompt(
    project: Path, run_install, scratch: Path, upstream, typed: str, expected: list[str]
) -> None:
    """Test 8: Enter selects the recorded IDEs, `a` selects all; other questions get Enter."""
    run = run_install(project, "-y", "--ides=claude,cursor")
    assert run.returncode == 0, run.output
    env = install_env(scratch, upstream.url)

    def answer(output: str) -> bytes:
        waiting = output.rstrip().endswith("a = all]:")  # the IDE prompt is the last output
        return f"{typed}\n".encode() if waiting else b"\n"

    code, text = run_in_pty(["bash", str(INSTALLER)], project, env, answer)
    assert code == 0, text
    assert "Selection [Enter = recorded: 1 4 (claude,cursor); a = all]:" in text
    assert manifest_of(project)["ides"] == expected
    assert ("IDEs: claude cursor (recorded)" in text) == (typed == "")


def test_fresh_interactive_prompt_unchanged(project: Path, scratch: Path, upstream) -> None:
    """Task 7: a fresh install shows today's prompt and Enter still means all."""
    env = install_env(scratch, upstream.url)
    code, text = run_in_pty(["bash", str(INSTALLER)], project, env, lambda _output: b"\n")
    assert code == 0, text
    assert "Selection [a = all]:" in text and "Enter = recorded" not in text
    assert manifest_of(project)["ides"] == ALL_IDES
