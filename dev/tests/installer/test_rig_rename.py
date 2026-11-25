"""Tests for the `--rig` install's final rename (OPR.99.0.11.4, K10).

The copy used to end with GNU-only `mv -T`, which fails on macOS. It now ends with one
`rename(2)` through python3 (`rig_rename`), which refuses to move INTO a directory that appeared
after the `[ ! -e ]` pre-check. Every run happens in a temp project; nothing is written inside the
HACF checkout.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

from .conftest import INSTALLER

# A python3 stand-in that, just before the installer's rename, makes the target a non-empty
# directory (a late arrival), then runs the real python3 unchanged.
LATE_SHIM = """#!/bin/sh
case "$2" in
  *os.rename*)
    mkdir -p -- "$4" && echo late > "$4/theirs.txt"
    ;;
esac
exec {real} "$@"
"""


def rig_rename_function() -> str:
    """Return the installer's `rig_rename` function text (the extracted rename line)."""
    text = INSTALLER.read_text(encoding="utf-8")
    match = re.search(r"^rig_rename\(\) \{\n.*?^\}\n", text, re.S | re.M)
    assert match, "rig_rename is missing from HACF-install.sh"
    return match.group(0)


def test_a_new_target_installs_with_the_spec_directly_inside(project: Path, run_install) -> None:
    """(a) --rig into a new target: the spec sits in the target, not in a nested directory."""
    run = run_install(project, "-y", "--ides=claude", "--rig")
    assert run.returncode == 0, run.output
    target = project / "openrig" / "hacf"
    assert (target / "rig.yaml").is_file()
    assert not (target / "hacf").exists() and not list(target.glob(".hacf-rig.*"))
    assert not list((project / "openrig").glob(".hacf-rig.*"))


def test_b_a_late_non_empty_target_is_never_moved_into(tmp_path: Path) -> None:
    """(b) The rename step against a non-empty target: non-zero, contents kept, nothing nested."""
    staged = tmp_path / "staged"
    staged.mkdir()
    (staged / "rig.yaml").write_text("name: x\n", encoding="utf-8")
    target = tmp_path / "target"
    target.mkdir()
    (target / "theirs.txt").write_text("late\n", encoding="utf-8")
    script = f"{rig_rename_function()}\nrig_rename {staged} {target}\n"
    done = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=False)
    assert done.returncode != 0
    assert sorted(p.name for p in target.iterdir()) == ["theirs.txt"]  # no nested copy
    assert (target / "theirs.txt").read_text(encoding="utf-8") == "late\n"
    assert (staged / "rig.yaml").is_file()  # the installer's own cleanup removes the staging copy


def test_b_installer_cleans_up_and_says_so_on_a_late_target(
    tmp_path: Path, project: Path, run_install
) -> None:
    """(b) End to end: a target that appears during the install is kept, the staging copy goes."""
    bin_dir = tmp_path / "shim-bin"
    bin_dir.mkdir()
    real = shutil.which("python3") or sys.executable
    shim = bin_dir / "python3"
    shim.write_text(LATE_SHIM.format(real=real), encoding="utf-8")
    shim.chmod(0o755)
    run = run_install(project, "-y", "--ides=claude", "--rig", path_prefix=bin_dir)
    assert run.returncode == 1, run.output
    assert "--rig: openrig/hacf appeared during install; nothing written there" in run.output
    target = project / "openrig" / "hacf"
    assert sorted(p.name for p in target.iterdir()) == ["theirs.txt"]
    assert (target / "theirs.txt").read_text(encoding="utf-8").strip() == "late"
    assert not list((project / "openrig").glob(".hacf-rig.*"))  # the staging copy is removed


def test_c_an_existing_target_is_refused_by_the_pre_check(project: Path, run_install) -> None:
    """(c) A target that exists before the run is refused as before; nothing is written there."""
    target = project / "openrig" / "hacf"
    target.mkdir(parents=True)
    (target / "mine.txt").write_text("mine\n", encoding="utf-8")
    run = run_install(project, "-y", "--ides=claude", "--rig")
    assert run.returncode != 0, run.output
    assert sorted(p.name for p in target.iterdir()) == ["mine.txt"]
    assert not list((project / "openrig").glob(".hacf-rig.*"))


def test_d_no_gnu_only_flag_is_left_on_the_rig_path() -> None:
    """(d) Regression marker beside (a)-(c): the installer has no `mv -T`, `readlink -f`."""
    text = INSTALLER.read_text(encoding="utf-8")
    assert "mv -T" not in text and "readlink -f" not in text
    assert "rig_rename" in text
