"""--rig[=DEST]: the OpenRig team spec is copied only on consent (OPR.0.6.1.7; R3-R5, R7, R10).

`HACF-install.sh --rig` copies the payload's `openrig/hacf/` tree to `<project>/openrig/hacf/`, and
`--rig=DEST` copies it there. It never writes over an existing path, renames the copy's rig to
`<slug>-hacf`, records it in the manifest's `rig_spec` list and leaves it to the user: an upgrade
never refreshes it and uninstall never removes it. Every run happens in a temp project; nothing is
written inside the HACF checkout.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest
from test_shipped_lore_refs import scan

from .conftest import (
    INSTALLER,
    REPO_ROOT,
    UNINSTALLER,
    Run,
    install_env,
    make_local_source,
    run_uninstall_script,
    snapshot,
)

MANIFEST_REL = ".agents/hacf-manifest.json"
PAYLOAD = REPO_ROOT / "openrig" / "hacf"
REFUSED = "Nothing was written."


def manifest_of(project: Path) -> dict:
    """Return the project's parsed manifest."""
    return json.loads((project / MANIFEST_REL).read_text(encoding="utf-8"))


def tree(root: Path) -> dict[str, bytes]:
    """Return {relative path: bytes} for every file under root."""
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def assert_is_payload_copy(copy: Path, rig_name: str) -> None:
    """The copy equals the upstream payload except its one `name:` line, which names rig_name."""
    expected = tree(PAYLOAD)
    actual = tree(copy)
    assert sorted(actual) == sorted(expected)
    for rel, data in expected.items():
        if rel != "rig.yaml":
            assert actual[rel] == data, rel
    old = expected["rig.yaml"].splitlines(keepends=True)
    new = actual["rig.yaml"].splitlines(keepends=True)
    assert len(old) == len(new)
    changed = [(a, b) for a, b in zip(old, new) if a != b]
    assert changed == [(b"name: hacf\n", f"name: {rig_name}\n".encode())]
    assert os.access(copy / "bin" / "brief.sh", os.X_OK)


def string_values(value) -> list[str]:
    """Return every string (keys included) in a parsed JSON value."""
    if isinstance(value, dict):
        return [s for k, v in value.items() for s in [k, *string_values(v)]]
    if isinstance(value, list):
        return [s for item in value for s in string_values(item)]
    return [value] if isinstance(value, str) else []


def refused(run: Run, *texts: str) -> None:
    """Assert a refusal: exit 1, the installer's refusal wording and each text."""
    assert run.returncode == 1, run.output
    assert REFUSED in run.output, run.output
    for text in texts:
        assert text in run.output, run.output


# ---------------------------------------------------------------------------
# (1)-(4): what is copied, where, and what is recorded
# ---------------------------------------------------------------------------


def test_default_install_copies_no_rig_spec(project: Path, run_install) -> None:
    """(1) -y copies no rig spec and writes no rig_spec key."""
    run = run_install(project, "-y", "--ides=claude")
    assert run.returncode == 0, run.output
    assert not (project / "openrig").exists()
    assert "rig_spec" not in manifest_of(project)


def test_rig_copies_to_project(project: Path, run_install, upstream) -> None:
    """(2) -y --rig copies the payload to openrig/hacf/, renamed, executable, recorded."""
    run = run_install(project, "-y", "--ides=claude", "--rig")
    assert run.returncode == 0, run.output
    assert_is_payload_copy(project / "openrig" / "hacf", "project-hacf")
    assert manifest_of(project)["rig_spec"] == [{"path": "openrig/hacf", "commit": upstream.sha}]
    assert "rig up --plan openrig/hacf/rig.yaml --name project-hacf --cwd " in run.output
    assert not list(project.glob("openrig/.hacf-rig.*"))


def test_rig_dest_outside_project(tmp_path: Path, project: Path, run_install, upstream) -> None:
    """(3) --rig=<outside dir> copies there; the entry is outside_project, with no path."""
    dest = tmp_path / "outside" / "specs" / "x"
    run = run_install(project, "-y", "--ides=claude", f"--rig={dest}")
    assert run.returncode == 0, run.output
    assert_is_payload_copy(dest, "project-hacf")
    manifest = manifest_of(project)
    assert manifest["rig_spec"] == [{"outside_project": True, "commit": upstream.sha}]
    text = (project / MANIFEST_REL).read_text(encoding="utf-8")
    assert str(tmp_path) not in text
    assert [s for s in string_values(manifest) if s.startswith("/")] == []
    assert f"--name project-hacf --cwd {project}" in run.output
    assert "--cwd is required" in run.output


def test_rig_dest_tilde_expands_to_home(tmp_path: Path, project: Path, run_install) -> None:
    """(4) --rig=~/specs/x lands under $HOME."""
    home = tmp_path / "home"
    home.mkdir()
    run = run_install(project, "-y", "--ides=claude", "--rig=~/specs/x", env={"HOME": str(home)})
    assert run.returncode == 0, run.output
    assert_is_payload_copy(home / "specs" / "x", "project-hacf")
    assert manifest_of(project)["rig_spec"][0]["outside_project"] is True


# ---------------------------------------------------------------------------
# (5)-(8): refusals leave every byte unchanged
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["dir", "file", "dangling-symlink"])
def test_existing_dest_is_refused(project: Path, run_install, kind: str) -> None:
    """(5) an existing DEST of any form: exit 1, nothing written in the project or DEST."""
    dest = project / "openrig" / "hacf"
    dest.parent.mkdir()
    if kind == "dir":
        dest.mkdir()
        (dest / "mine.txt").write_text("mine\n", encoding="utf-8")
    elif kind == "file":
        dest.write_text("mine\n", encoding="utf-8")
    else:
        dest.symlink_to(project / "missing")
    before = snapshot(project)
    run = run_install(project, "-y", "--ides=claude", "--rig")
    refused(run, "never writes over an existing path")
    assert snapshot(project) == before


def test_upgrade_rerun_with_rig_is_refused(project: Path, run_install) -> None:
    """(6) after (2), an upgrade with --rig is refused: it says to re-run without --rig."""
    assert run_install(project, "-y", "--ides=claude", "--rig").returncode == 0
    before = snapshot(project)
    run = run_install(project, "-y", "--rig")
    refused(run, "already copied at", "re-run without --rig")
    assert snapshot(project) == before


def test_dest_under_rig_yaml_folder_is_refused(tmp_path: Path, project: Path, run_install) -> None:
    """(7a) a DEST under a folder that holds a rig.yaml is refused."""
    holder = tmp_path / "holder"
    holder.mkdir()
    (holder / "rig.yaml").write_text("name: other\n", encoding="utf-8")
    before = (snapshot(project), snapshot(holder))
    run = run_install(project, "-y", "--ides=claude", f"--rig={holder}/sub/x")
    refused(run, "which holds a rig.yaml")
    assert (snapshot(project), snapshot(holder)) == before


def test_dest_inside_git_dir_is_refused(project: Path, run_install) -> None:
    """(7b) a DEST inside the project's git dir is refused."""
    before = snapshot(project)
    run = run_install(project, "-y", "--ides=claude", "--rig=.git/hacf-rig")
    refused(run, "lies inside")
    assert snapshot(project) == before


def test_dest_inside_source_checkout_is_refused(
    tmp_path: Path, project: Path, scratch: Path, run_install
) -> None:
    """(7c) a DEST inside the --source checkout is refused."""
    src, sha = make_local_source(tmp_path / "local", scratch)
    before = (snapshot(project), snapshot(src))
    run = run_install(
        project, f"--source={src}", f"--ref={sha}", "-y", "--ides=claude", f"--rig={src}/x"
    )
    refused(run, "lies inside")
    assert (snapshot(project), snapshot(src)) == before


def test_saved_v05x_uninstaller_refuses_rig(v059_project, run_install) -> None:
    """(8) a saved v0.5.x ./HACF-uninstall.sh refuses --rig before any write."""
    project = v059_project("default")
    before = snapshot(project)
    run = run_install(project, "-y", "--ides=claude", "--rig")
    refused(run, "a saved v0.5.x uninstaller deletes every path in HACF main")
    assert snapshot(project) == before


# ---------------------------------------------------------------------------
# (9)-(11): dry run, user ownership, usage errors
# ---------------------------------------------------------------------------


def test_dry_run_rig_writes_nothing(project: Path, run_install) -> None:
    """(9) --dry-run --rig prints the copy and writes nothing."""
    before = snapshot(project)
    run = run_install(project, "-y", "--ides=claude", "--dry-run", "--rig")
    assert run.returncode == 0, run.output
    n = len(tree(PAYLOAD))
    expected = f"[DRY-RUN] would copy openrig/hacf/ ({n} files) to openrig/hacf as rig project-hacf"
    assert expected in run.output
    assert snapshot(project) == before


def test_copy_is_the_users(project: Path, scratch: Path, run_install, upstream) -> None:
    """(10) an upgrade leaves an edited copy byte-identical and recorded; uninstall leaves it."""
    assert run_install(project, "-y", "--ides=claude", "--rig").returncode == 0
    culture = project / "openrig" / "hacf" / "CULTURE.md"
    culture.write_bytes(culture.read_bytes() + b"\nOur own culture line.\n")
    copy_before = snapshot(project / "openrig")
    entry = manifest_of(project)["rig_spec"]

    run = run_install(project, "-y")
    assert run.returncode == 0, run.output
    assert snapshot(project / "openrig") == copy_before
    assert manifest_of(project)["rig_spec"] == entry

    run = run_uninstall_script(scratch, upstream.url, project, "-y", script=UNINSTALLER)
    assert run.returncode == 0, run.output
    assert snapshot(project / "openrig") == copy_before
    line = f"rig spec copy at openrig/hacf (from {upstream.sha}) left in place; it is yours"
    assert line in run.output


@pytest.mark.parametrize(
    "args",
    [("--rig=",), ("--rig", "--files=pre-commit"), ("--rig", "--mode=skills")],
    ids=["empty-dest", "with-files", "with-mode-skills"],
)
def test_rig_usage_errors(project: Path, run_install, args: tuple[str, ...]) -> None:
    """(11) --rig= and --rig with --files= or --mode=skills|system|ide exit 2, writing nothing."""
    before = snapshot(project)
    run = run_install(project, "-y", *args)
    assert run.returncode == 2, run.output
    assert REFUSED in run.output and "Usage:" in run.output
    assert snapshot(project) == before


# ---------------------------------------------------------------------------
# (12), (13): content scan, all-or-nothing copy
# ---------------------------------------------------------------------------


def test_copied_tree_passes_the_lore_ref_scan(project: Path, run_install) -> None:
    """(12) R10: the copied tree passes test_shipped_lore_refs.py's lore-id/dev/design scan."""
    assert run_install(project, "-y", "--ides=claude", "--rig").returncode == 0
    copy = project / "openrig" / "hacf"
    rels = [f"openrig/hacf/{rel}" for rel in tree(copy)]
    assert rels
    assert scan(project, rels) == []


def run_unwrapped(scratch: Path, url: str, cwd: Path, *args: str, path_prefix=None) -> Run:
    """Run the installer without `unshare -rn`, whose mapped root ignores directory modes."""
    proc = subprocess.run(
        ["bash", str(INSTALLER), *args],
        cwd=cwd,
        env=install_env(scratch, url, None, path_prefix),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        timeout=300,
        check=False,
    )
    return Run(proc.returncode, proc.stdout.decode("utf-8", "replace"), [])


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory modes")
@pytest.mark.parametrize("failure", ["unwritable-parent", "rename-fails"])
def test_failed_copy_leaves_no_dest(
    tmp_path: Path, project: Path, scratch: Path, upstream, failure: str
) -> None:
    """(13) a failure during the copy leaves no DEST and no .hacf-rig.* temp."""
    parent = tmp_path / "parent"
    parent.mkdir()
    dest = parent / "x"
    prefix = None
    if failure == "unwritable-parent":
        parent.chmod(stat.S_IRUSR | stat.S_IXUSR)
    else:
        prefix = tmp_path / "failing-mv"
        prefix.mkdir()
        (prefix / "mv").write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        (prefix / "mv").chmod(0o755)
    try:
        run = run_unwrapped(
            scratch,
            upstream.url,
            project,
            "-y",
            "--ides=claude",
            f"--rig={dest}",
            path_prefix=prefix,
        )
    finally:
        parent.chmod(0o755)
    assert run.returncode == 1, run.output
    assert "could not" in run.output and "rig spec" in run.output
    assert not dest.exists() and not dest.is_symlink()
    assert [p.name for p in parent.iterdir()] == []
