"""Tests for upgrades that reuse the previous install's record (slice 32, OPR.0.6.0.32; R58).

An upgrade of a --source install with no --source, --repo-url or --branch reads the recorded
checkout, and refuses when that checkout is unusable. Since D1 (OPR.99.0.6.1) such a re-run
without --ref reinstalls installed_commit and refuses when the checkout's HEAD has moved; --ref
alone moves. An upgrade without --ides keeps the manifest's recorded `ides`. Every run happens in
a temp project; nothing is written inside the HACF checkout.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from .conftest import (
    INSTALLER,
    add_commit,
    git,
    git_env,
    install_env,
    make_local_source,
    network_wrapper,
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
    """Test 5 (D1): c1 installed, c2 committed, plain -y refuses, --ref=c2 alone upgrades to c2.

    Updated by OPR.99.0.6.1 Mini-requirement 8: a plain re-run no longer follows HEAD.
    """
    src, c1 = make_local_source(tmp_path / "local-source", tmp_path / "source-scratch")
    run = run_installer(scratch, "file:///nonexistent", project, "-y", "--ides=claude",
                        f"--source={src}", f"--ref={c1}")  # fmt: skip
    assert run.returncode == 0, run.output
    assert manifest_of(project)["installed_commit"] == c1
    c2 = add_commit(src, git_env(scratch), "c2")

    run = run_installer(scratch, "file:///nonexistent", project, "-y", env=NO_URL)
    assert run.returncode == 1, run.output
    assert "refusing a plain re-run" in run.output and WRITTEN in run.output
    assert manifest_of(project)["installed_commit"] == c1

    run = run_installer(scratch, "file:///nonexistent", project, "-y", f"--ref={c2}", env=NO_URL)
    assert run.returncode == 0, run.output
    recorded = "source: recorded by the previous install (pass --repo-url to switch to a remote)"
    assert recorded in run.output
    assert f"source: local {src.resolve()} @ {c2} (ref {c2})" in run.output
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


# ---------------------------------------------------------------------------
# D1 (OPR.99.0.6.1): a re-run without --ref is pinned to installed_commit; --ref alone moves
# ---------------------------------------------------------------------------

NOWHERE = "file:///nonexistent"


def refusal_lines(path: Path, head: str, installed: str, source_arg: str = "") -> list[str]:
    """Return the four lines of the D1 refusal (Mini-requirement 1), values quoted like `q`."""
    again = f"bash {bash_quote(str(path))}/HACF-install.sh{source_arg}"
    return [
        f"refusing a plain re-run: the recorded HACF checkout {bash_quote(str(path))} is at {head},"
        f" but this project is installed at {installed}.",
        f"  to reinstall the installed commit:  {again} --ref={installed}",
        f"  to move to the checkout's HEAD:     {again} --ref={head}",
        WRITTEN,
    ]


def bash_quote(value: str) -> str:
    """Return bash's `printf %q` of value (the installer's `q`)."""
    proc = subprocess.run(
        ["bash", "-c", 'printf %q "$1"', "_", value], capture_output=True, text=True, check=True
    )
    return proc.stdout


def assert_refusal(output: str, lines: list[str]) -> None:
    """Assert the refusal's lines appear consecutively (the first after die's marker)."""
    first = output.index(lines[0])
    block = output[first:].splitlines()[: len(lines)]
    assert block == lines, output
    assert output[:first].endswith("❌  "), output


def run_printed(scratch: Path, project: Path, line: str, env: dict | None = None):
    """Run one printed advice command exactly as printed (a shell line) in the project."""
    full = install_env(scratch, NOWHERE, {**NO_URL, **(env or {})})
    proc = subprocess.run(
        [*network_wrapper(), "bash", "-c", line],
        cwd=project,
        env=full,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        timeout=300,
        check=False,
    )
    return proc.returncode, proc.stdout.decode("utf-8", "replace")


@pytest.fixture
def recorded(tmp_path: Path, project: Path, scratch: Path) -> tuple[Path, str]:
    """Return (checkout, c1): a project installed from a local checkout (a path with a space)."""
    src, c1 = make_local_source(tmp_path / "hacf source", tmp_path / "source-scratch")
    run = run_installer(scratch, NOWHERE, project, "-y", "--ides=claude", f"--source={src}")
    assert run.returncode == 0, run.output
    assert manifest_of(project)["installed_commit"] == c1
    return src, c1


def test_d1_plain_rerun_same_head_reinstalls(project: Path, scratch: Path, recorded) -> None:
    """D1: HEAD == installed_commit, so a plain re-run reinstalls it as before."""
    src, c1 = recorded
    run = run_installer(scratch, NOWHERE, project, "-y", env=NO_URL)
    assert run.returncode == 0, run.output
    assert f"@ {c1} (ref HEAD)" in run.output
    assert manifest_of(project)["installed_commit"] == c1


def test_d1_plain_rerun_moved_head_refuses(project: Path, scratch: Path, recorded) -> None:
    """D1: HEAD moved (same HACF_VERSION), so a plain re-run exits 1 and writes nothing."""
    src, c1 = recorded
    c2 = add_commit(src, git_env(scratch), "c2")
    before = snapshot(project)
    source_before = snapshot(src)
    run = run_installer(scratch, NOWHERE, project, "-y", env=NO_URL)
    assert run.returncode == 1, run.output
    assert_refusal(run.output, refusal_lines(src.resolve(), c2, c1))
    assert snapshot(project) == before
    assert snapshot(src) == source_before
    assert manifest_of(project)["installed_commit"] == c1


@pytest.mark.parametrize("via", ["direct", "symlink"])
def test_d1_same_source_without_ref_refuses(
    tmp_path: Path, project: Path, scratch: Path, recorded, via: str
) -> None:
    """D1: --source=<the recorded checkout> (or a symlink to it) without --ref is pinned too."""
    src, c1 = recorded
    c2 = add_commit(src, git_env(scratch), "c2")
    given = src
    if via == "symlink":
        given = tmp_path / "link-to-hacf"
        given.symlink_to(src, target_is_directory=True)
    before = snapshot(project)
    run = run_installer(scratch, NOWHERE, project, "-y", f"--source={given}")
    assert run.returncode == 1, run.output
    lines = refusal_lines(src.resolve(), c2, c1, f" --source={bash_quote(str(given))}")
    assert_refusal(run.output, lines)
    assert snapshot(project) == before


def test_d1_other_source_takes_its_head(
    tmp_path: Path, project: Path, scratch: Path, recorded
) -> None:
    """D1: --source naming another checkout is a new source: its HEAD, as before."""
    src, c1 = recorded
    other = tmp_path / "other-hacf"
    shutil.copytree(src, other, symlinks=True)
    c3 = add_commit(other, git_env(scratch), "c3 in the other checkout")
    run = run_installer(scratch, NOWHERE, project, "-y", f"--source={other}")
    assert run.returncode == 0, run.output
    assert "refusing a plain re-run" not in run.output
    assert f"source: local {other.resolve()} @ {c3} (ref HEAD)" in run.output
    assert manifest_of(project)["installed_commit"] == c3


def test_d1_ref_without_source(project: Path, scratch: Path, recorded) -> None:
    """D1: --ref alone resolves in the recorded checkout; an unknown ref writes nothing."""
    src, c1 = recorded
    c2 = add_commit(src, git_env(scratch), "c2")

    before = snapshot(project)
    run = run_installer(scratch, NOWHERE, project, "-y", "--ref=nonexistent", env=NO_URL)
    assert run.returncode == 1, run.output
    assert "refusing --ref nonexistent: unknown ref in" in run.output and WRITTEN in run.output
    assert snapshot(project) == before

    run = run_installer(scratch, NOWHERE, project, "-y", f"--ref={c1}", env=NO_URL)
    assert run.returncode == 0, run.output
    assert f"(installing {c1}, which is not HEAD)" in run.output
    assert manifest_of(project)["installed_commit"] == c1

    run = run_installer(scratch, NOWHERE, project, "-y", f"--ref={c2}", env=NO_URL)
    assert run.returncode == 0, run.output
    assert manifest_of(project)["installed_commit"] == c2
    assert manifest_of(project)["source"] == {"kind": "local", "commit": c2}


@pytest.mark.parametrize("record", ["none", "remote"])
def test_d1_ref_without_record_is_usage_error(
    project: Path, scratch: Path, upstream, record: str
) -> None:
    """D1: without a local-source record, --ref without --source stays the usage error."""
    if record == "remote":
        run = run_installer(scratch, upstream.url, project, "-y", "--ides=claude")
        assert run.returncode == 0, run.output
        assert manifest_of(project)["source"]["kind"] == "remote"
    before = snapshot(project)
    for extra in ([], ["--repo-url=https://example.invalid/hacf.git"], ["--branch=main"]):
        run = run_installer(scratch, upstream.url, project, "-y", "--ref=HEAD", *extra, env=NO_URL)
        assert run.returncode == 2, run.output
        assert "--ref needs --source. Nothing was written." in run.output
        assert "Usage:" in run.output
    assert snapshot(project) == before


@pytest.mark.parametrize("which", ["reinstall", "move"])
def test_d1_printed_advice_runs(project: Path, scratch: Path, recorded, which: str) -> None:
    """D1: each printed advice command, run exactly as printed, succeeds (the copy test)."""
    src, c1 = recorded
    c2 = add_commit(src, git_env(scratch), "c2")
    run = run_installer(scratch, NOWHERE, project, "-y", env=NO_URL)
    assert run.returncode == 1, run.output
    prefix = (
        "  to reinstall the installed commit:  "
        if which == "reinstall"
        else "  to move to the checkout's HEAD:     "
    )
    line = next(row for row in run.output.splitlines() if row.startswith(prefix))
    code, output = run_printed(scratch, project, line[len(prefix) :])
    assert code == 0, output
    assert manifest_of(project)["installed_commit"] == (c1 if which == "reinstall" else c2)


def test_d1_version_guard_advice(tmp_path: Path, project: Path, scratch: Path, recorded) -> None:
    """D1: --ref to a commit of another HACF_VERSION refuses (exit 1) with a runnable command."""
    src, c1 = recorded
    env = git_env(scratch)
    installer = src / "HACF-install.sh"
    text = installer.read_text(encoding="utf-8")
    version = text.split('HACF_VERSION="', 1)[1].split('"', 1)[0]
    installer.write_text(text.replace(f'HACF_VERSION="{version}"', 'HACF_VERSION="9.9.9"', 1))
    git(["-c", "commit.gpgsign=false", "commit", "-q", "-am", "another version"], src, env)
    other = git(["rev-parse", "HEAD"], src, env).strip()
    tmp_dir = tmp_path / "advice-tmp"
    tmp_dir.mkdir()

    before = snapshot(project)
    run = run_installer(
        scratch, NOWHERE, project, "-y", f"--ref={other}", env={**NO_URL, "TMPDIR": str(tmp_dir)}
    )
    assert run.returncode == 1, run.output
    assert snapshot(project) == before
    real = bash_quote(str(src.resolve()))
    tmp_file = bash_quote(str(tmp_dir / f"HACF-install-{other[:12]}.sh"))
    command = (
        f"git -C {real} show {other}:HACF-install.sh > {tmp_file}; "
        f"bash {tmp_file} --source={real} --ref={other}"
    )
    expected = (
        f"commit {other} carries HACF 9.9.9; this installer is {version}; "
        f"run it from that commit: {command}"
    )
    assert expected in run.output.splitlines()[-2], run.output
    assert run.output.splitlines()[-1] == WRITTEN

    code, output = run_printed(scratch, project, command)
    assert code == 0, output
    assert manifest_of(project)["installed_commit"] == other
    assert os.path.isfile(tmp_dir / f"HACF-install-{other[:12]}.sh")
