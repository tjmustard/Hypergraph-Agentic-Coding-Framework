"""The kept pin (C3), the fetched version (C4) and the saved installer copy (C2).

v064-upgrade-path OPR.99.0.10.3, R42. Offline: local `--source` checkouts and the temp upstream
live in tmp_path, and every run goes through conftest's no-network installer runner.
"""

from __future__ import annotations

import json
import stat
import subprocess
from pathlib import Path

import pytest

from .conftest import (
    INSTALLER,
    add_commit,
    git_env,
    install_env,
    make_local_source,
    network_wrapper,
    run_installer,
    run_uninstall_script,
    sha256,
    snapshot,
)

NOTHING = "Nothing was written."
NOWHERE = "file:///nonexistent"
NO_URL = {"HACF_REPO_URL": None}  # HACF_TEST stays 1; without a URL the record is read
MANIFEST_REL = ".agents/hacf-manifest.json"
SOURCE_REL = ".agents/state/install/source.json"
SAVED_REL = ".agents/state/hacf/installer/HACF-install.sh"
MISMATCH = (
    "saved installer copy does not match the manifest; re-run the installer from the HACF source"
)


def read_json(project: Path, rel: str) -> dict:
    """Return one of the project's JSON records."""
    return json.loads((project / rel).read_text(encoding="utf-8"))


def write_json(project: Path, rel: str, data: dict) -> None:
    """Replace one of the project's JSON records."""
    (project / rel).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def run_saved(scratch: Path, url: str, project: Path, *args: str, env: dict | None = None):
    """Run the project's saved installer copy, as the README's re-run line does."""
    proc = subprocess.run(
        [*network_wrapper(), "bash", SAVED_REL, *args],
        cwd=project,
        env=install_env(scratch, url, env),
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
    """Return (checkout, c1): a project installed from a local checkout at its HEAD c1."""
    src, c1 = make_local_source(tmp_path / "hacf-source", tmp_path / "source-scratch")
    run = run_installer(scratch, NOWHERE, project, "-y", "--ides=claude", f"--source={src}")
    assert run.returncode == 0, run.output
    assert read_json(project, MANIFEST_REL)["installed_commit"] == c1
    assert read_json(project, SOURCE_REL)["ref"] == c1
    return src, c1


# ---------------------------------------------------------------------------
# C3: the pin table (R42, F15)
# ---------------------------------------------------------------------------


def test_pin_a_same_head_records_full_sha(project: Path, scratch: Path, recorded) -> None:
    """(a) HEAD == installed_commit: a plain re-run installs it; the ref is the full sha."""
    src, c1 = recorded
    run = run_installer(scratch, NOWHERE, project, "-y", env=NO_URL)
    assert run.returncode == 0, run.output
    record = read_json(project, SOURCE_REL)
    assert record["ref"] == c1 and record["ref"] != "HEAD"
    assert read_json(project, MANIFEST_REL)["installed_commit"] == c1


def test_pin_b_moved_head_refuses(project: Path, scratch: Path, recorded) -> None:
    """(b) HEAD moved: D1's refusal, exit 1, nothing written, both --ref commands printed."""
    src, c1 = recorded
    c2 = add_commit(src, git_env(scratch), "c2")
    before = snapshot(project)
    run = run_installer(scratch, NOWHERE, project, "-y", env=NO_URL)
    assert run.returncode == 1, run.output
    assert "refusing a plain re-run" in run.output
    assert f"--ref={c1}" in run.output and f"--ref={c2}" in run.output
    assert NOTHING in run.output
    assert snapshot(project) == before


def test_pin_c_missing_commit_refuses(project: Path, scratch: Path, recorded) -> None:
    """(c) The recorded commit is not in the checkout: exit 1, nothing written, --ref named."""
    src, c1 = recorded
    gone = "f" * 40
    manifest = read_json(project, MANIFEST_REL)
    manifest["installed_commit"] = gone
    manifest["source"]["commit"] = gone
    write_json(project, MANIFEST_REL, manifest)
    record = read_json(project, SOURCE_REL)
    record["commit"] = gone
    write_json(project, SOURCE_REL, record)
    before = snapshot(project)
    run = run_installer(scratch, NOWHERE, project, "-y", env=NO_URL)
    assert run.returncode == 1, run.output
    assert "its installed_commit is not a commit in that checkout" in run.output
    assert "--ref=" in run.output
    assert NOTHING in run.output
    assert snapshot(project) == before


def test_old_head_record(project: Path, scratch: Path, recorded) -> None:
    """A 0.6.3/0.6.4 `"ref": "HEAD"` record keeps today's behavior, then records the full sha."""
    src, c1 = recorded
    record = read_json(project, SOURCE_REL)
    record["ref"] = "HEAD"
    write_json(project, SOURCE_REL, record)
    run = run_installer(scratch, NOWHERE, project, "-y", env=NO_URL)
    assert run.returncode == 0, run.output
    assert read_json(project, SOURCE_REL)["ref"] == c1
    # today's behavior on a moved HEAD is D1's refusal, unchanged by the old record
    record = read_json(project, SOURCE_REL)
    record["ref"] = "HEAD"
    write_json(project, SOURCE_REL, record)
    add_commit(src, git_env(scratch), "c2")
    run = run_installer(scratch, NOWHERE, project, "-y", env=NO_URL)
    assert run.returncode == 1, run.output
    assert "refusing a plain re-run" in run.output


def test_ref_moves_and_records_full_sha(project: Path, scratch: Path, recorded) -> None:
    """--ref X moves to X and records X's resolved full sha (here X is `HEAD`)."""
    src, c1 = recorded
    c2 = add_commit(src, git_env(scratch), "c2")
    run = run_installer(scratch, NOWHERE, project, "-y", "--ref=HEAD", env=NO_URL)
    assert run.returncode == 0, run.output
    assert read_json(project, MANIFEST_REL)["installed_commit"] == c2
    assert read_json(project, SOURCE_REL)["ref"] == c2


# ---------------------------------------------------------------------------
# C4: a remote install records the fetched installer's version
# ---------------------------------------------------------------------------


def installer_with_version(version: str | None) -> str:
    """This repo's installer text with its HACF_VERSION line set to version (None: removed)."""
    lines = INSTALLER.read_text(encoding="utf-8").splitlines(keepends=True)
    out = []
    for line in lines:
        if line.startswith("HACF_VERSION="):
            if version is None:
                continue
            line = f'HACF_VERSION="{version}"\n'
        out.append(line)
    return "".join(out)


def test_remote_install_records_clone_version(project: Path, run_install, make_upstream) -> None:
    """C4: the manifest's version is the cloned installer's, not this script's."""
    up = make_upstream({"HACF-install.sh": installer_with_version("9.9.9")})
    run = run_install(project, "-y", "--ides=claude", source=up)
    assert run.returncode == 0, run.output
    manifest = read_json(project, MANIFEST_REL)
    assert manifest["version"] == "9.9.9"
    assert manifest["installed_commit"] == up.sha


def test_remote_install_unreadable_version_refuses(
    project: Path, run_install, make_upstream
) -> None:
    """C4: a clone whose installer has no readable HACF_VERSION refuses before any write."""
    up = make_upstream({"HACF-install.sh": installer_with_version(None)})
    before = snapshot(project)
    run = run_install(project, "-y", "--ides=claude", source=up)
    assert run.returncode == 1, run.output
    assert "no readable HACF_VERSION" in run.output and NOTHING in run.output
    assert snapshot(project) == before


# ---------------------------------------------------------------------------
# C2: the saved installer copy (R42, F16)
# ---------------------------------------------------------------------------


def test_saved_copy_lifecycle(project: Path, scratch: Path, run_install, upstream) -> None:
    """Created and recorded; a re-run passes; one edited byte refuses; uninstall removes it."""
    run = run_install(project, "-y", "--ides=claude")
    assert run.returncode == 0, run.output
    saved = project / SAVED_REL
    assert saved.read_bytes() == INSTALLER.read_bytes()
    assert stat.S_IMODE(saved.stat().st_mode) == 0o755
    assert read_json(project, MANIFEST_REL)["installer_sha256"] == sha256(saved)
    ignored = subprocess.run(
        ["git", "check-ignore", "-q", SAVED_REL], cwd=project, env=git_env(scratch), check=False
    )
    assert ignored.returncode == 0, ".agents/state/ is not git-ignored in the installed project"
    assert not [p for p in saved.parent.iterdir() if p.name != "HACF-install.sh"]

    code, out = run_saved(scratch, upstream.url, project, "-y", "--ides=claude")
    assert code == 0, out
    assert read_json(project, MANIFEST_REL)["installer_sha256"] == sha256(saved)

    data = bytearray(saved.read_bytes())
    at = data.index(b"\n# ") + 3  # a letter of a comment: the script still parses, its sha differs
    data[at] ^= 0x20  # flip its case
    saved.write_bytes(bytes(data))
    before = snapshot(project)
    code, out = run_saved(scratch, upstream.url, project, "-y", "--ides=claude")
    assert code == 1, out
    assert MISMATCH in out
    assert snapshot(project) == before

    run = run_uninstall_script(scratch, upstream.url, project, "-y")
    assert run.returncode == 0, run.output
    assert not saved.exists()
    assert not (project / ".agents/state/hacf").exists()


def test_saved_copy_without_manifest_record_refuses(
    project: Path, scratch: Path, run_install, upstream
) -> None:
    """A manifest with no installer_sha256 makes the saved copy refuse."""
    assert run_install(project, "-y", "--ides=claude").returncode == 0
    manifest = read_json(project, MANIFEST_REL)
    del manifest["installer_sha256"]
    write_json(project, MANIFEST_REL, manifest)
    code, out = run_saved(scratch, upstream.url, project, "-y", "--ides=claude")
    assert code == 1, out
    assert MISMATCH in out


def test_saved_copy_from_a_pipe(project: Path, run_install) -> None:
    """Under `bash -s` (curl | bash) the copy is the fetched commit's installer."""
    run = run_install(project, "-y", "--ides=claude", script_on_stdin=True)
    assert run.returncode == 0, run.output
    saved = project / SAVED_REL
    assert saved.read_bytes() == INSTALLER.read_bytes()  # the temp upstream is this tree
    assert read_json(project, MANIFEST_REL)["installer_sha256"] == sha256(saved)


def test_saved_copy_from_a_local_source(project: Path, scratch: Path, recorded) -> None:
    """A --source install saves the running script; the copy re-runs with the recorded pin."""
    src, c1 = recorded
    saved = project / SAVED_REL
    assert saved.read_bytes() == INSTALLER.read_bytes()
    code, out = run_saved(scratch, NOWHERE, project, "-y", env=NO_URL)
    assert code == 0, out
    assert read_json(project, MANIFEST_REL)["installed_commit"] == c1
    assert read_json(project, SOURCE_REL)["ref"] == c1
