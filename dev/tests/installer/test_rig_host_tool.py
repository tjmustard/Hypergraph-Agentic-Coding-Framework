"""--rig carries the host tool into the copied team spec (OPR.99.0.16.4 MR 1).

`HACF-install.sh --rig` copies `openrig/hacf/` whole, so `bin/hacf-host.py` and `host.example.yml`
reach every install. The host config itself is the person's: the installer writes none.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Iterator
from pathlib import Path

import pytest

from .conftest import make_local_source, run_installer


@pytest.fixture
def umask_022() -> Iterator[None]:
    """The installer keeps the mode its clone gets, which follows the umask: pin it."""
    old = os.umask(0o022)
    try:
        yield
    finally:
        os.umask(old)


def test_rig_install_carries_the_host_tool_and_writes_no_host_config(
    tmp_path: Path, project: Path, scratch: Path, umask_022: None
) -> None:
    """MR 1: both files byte-equal to the source, the script 0755, no host.yml written."""
    src, sha = make_local_source(tmp_path / "source", scratch)
    home, config = tmp_path / "home", tmp_path / "xdg-config"
    home.mkdir()
    config.mkdir()

    run = run_installer(
        scratch,
        f"file://{tmp_path / 'source' / 'upstream.git'}",
        project,
        f"--source={src}",
        f"--ref={sha}",
        "-y",
        "--ides=claude",
        "--rig",
        env={"HOME": str(home), "XDG_CONFIG_HOME": str(config)},
    )

    assert run.returncode == 0, run.output
    copy = project / "openrig" / "hacf"
    for rel in ("bin/hacf-host.py", "host.example.yml"):
        assert (copy / rel).is_file(), rel
        assert (copy / rel).read_bytes() == (src / "openrig" / "hacf" / rel).read_bytes(), rel
    assert stat.S_IMODE((copy / "bin" / "hacf-host.py").stat().st_mode) == 0o755
    for root in (config, home, project):
        assert list(root.rglob("host.yml")) == [], root
