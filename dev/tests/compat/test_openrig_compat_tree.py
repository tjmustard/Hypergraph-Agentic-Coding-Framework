"""Tests for the compat harness's tree step: copy_tree() of dev/openrig_compat.sh (OPR.99.0.12.1).

A clean tree under test (nothing beyond HEAD) must commit with --allow-empty, and the clone must
get a ledger provisioned from its own lore anchors; any real failure stays return 2 (SETUP-ERROR).
copy_tree is extracted from the real script at test time and run in bash with a fake `uv`. No
network, npm, rig, OpenRig or real uv.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

from conftest import REPO_ROOT

SCRIPT = REPO_ROOT / "dev" / "openrig_compat.sh"
COMPAT = REPO_ROOT / "dev" / "openrig_compat.py"
SCRIPTS = REPO_ROOT / ".agents" / "scripts"
IDENT = ["-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false"]
GIT_ENV_KEYS = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_CONFIG_PARAMETERS",
    "GIT_CONFIG_COUNT",
)


def _git(cwd: Path, *args: str, env: dict | None = None) -> str:
    out = subprocess.run(
        ["git", *IDENT, *args],
        cwd=cwd,
        env=env or _clean_env(),
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    return out.stdout.strip()


def _clean_env(**extra: str) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in GIT_ENV_KEYS}
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env.update(extra)
    return env


def _fixture(path: Path, files: dict[str, str], *, ledger_tools: bool) -> Path:
    """A committed repo with `files`, optionally carrying the two scripts `init`/`validate` need."""
    path.mkdir(parents=True)
    _git(path, "init", "-q", "-b", "main")
    for rel, text in files.items():
        target = path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    if ledger_tools:
        (path / ".agents" / "scripts").mkdir(parents=True, exist_ok=True)
        for name in ("hyper_ledger.py", "_runtime.py"):
            shutil.copy2(SCRIPTS / name, path / ".agents" / "scripts" / name)
    _git(path, "add", "--force", ".")
    _git(path, "commit", "-q", "-m", "base")
    return path


def _copy_tree_text() -> str:
    text = SCRIPT.read_text(encoding="utf-8")
    start = text.index("copy_tree() {")
    end = text.index("\n}\n", start) + 3
    body = text[start:end]
    assert "commit -q --allow-empty" in body
    assert 'provision-ledger --root "$T/hacf"' in body
    return body


def _fake_uv(tmp: Path, rc: int = 0) -> Path:
    bindir = tmp / "bin"
    bindir.mkdir(exist_ok=True)
    uv = bindir / "uv"
    uv.write_text(f"#!/bin/sh\nexit {rc}\n", encoding="utf-8")
    uv.chmod(uv.stat().st_mode | stat.S_IXUSR)
    return bindir


def _run_copy_tree(src: Path, tmp: Path, *, uv_rc: int = 0, extra_env: dict | None = None) -> dict:
    """Run copy_tree against `src`; return rc, tree_sha, the T dir and the combined log."""
    T = tmp / "T"
    (T / "state").mkdir(parents=True)
    bindir = _fake_uv(tmp, uv_rc)
    script = (
        "set -u\n"
        + _copy_tree_text()
        + f'root={src!s}\nT={T!s}\ncompat={COMPAT!s}\ntree_sha=""\n'
        + 'copy_tree; rc=$?\nprintf "\\nRC=%s\\nTREE_SHA=%s\\n" "$rc" "$tree_sha"\n'
    )
    env = _clean_env(PATH=f"{bindir}{os.pathsep}{os.environ['PATH']}")
    env.update(extra_env or {})
    out = subprocess.run(
        ["bash", "-c", script],
        cwd=tmp,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=300,
    )
    log = out.stdout
    rc = int(log.split("\nRC=")[-1].split()[0])
    sha = log.split("\nTREE_SHA=")[-1].strip()
    return {"rc": rc, "tree_sha": sha, "T": T, "log": log, "clone": T / "hacf"}


# --- (a) US-001: a clean tree ---------------------------------------------------------------------


def test_a_clean_tree_commits_allow_empty_and_gets_an_empty_ledger(tmp_path: Path) -> None:
    """A clean tree commits --allow-empty on the source HEAD and gets a 0-node ledger."""
    src = _fixture(tmp_path / "src", {"a.txt": "a\n"}, ledger_tools=True)
    res = _run_copy_tree(src, tmp_path)
    assert res["rc"] == 0, res["log"]
    clone = res["clone"]
    assert _git(clone, "rev-parse", "HEAD^{tree}") == _git(src, "rev-parse", "HEAD^{tree}")
    assert res["tree_sha"] == _git(clone, "rev-parse", "HEAD")
    parents = _git(clone, "rev-list", "--parents", "-n1", "HEAD").split()
    assert parents[1:] == [_git(src, "rev-parse", "HEAD")]
    assert "ledger: provisioned 0 node(s) from lore anchors" in res["log"]


# --- (b) US-002: the dev-checkout case ------------------------------------------------------------


def test_b_ignored_ledger_and_untracked_file_are_carried(tmp_path: Path) -> None:
    """An ignored ledger and an untracked file are carried; the ledger is left as it is."""
    src = _fixture(tmp_path / "src", {".gitignore": "/spec/\n", "a.txt": "a\n"}, ledger_tools=True)
    (src / "extra.txt").write_text("extra\n", encoding="utf-8")
    (src / "spec/compiled").mkdir(parents=True)
    ledger = src / "spec/compiled/architecture.yml"
    ledger.write_bytes(b"arbitrary \x00 bytes\n")
    res = _run_copy_tree(src, tmp_path)
    assert res["rc"] == 0, res["log"]
    clone = res["clone"]
    src_head = _git(src, "rev-parse", "HEAD")
    assert _git(clone, "rev-list", "--parents", "-n1", "HEAD").split()[1:] == [src_head]
    diff = _git(
        clone, "diff-tree", "-r", "--no-commit-id", "--name-status", src_head, res["tree_sha"]
    )
    assert sorted(diff.splitlines()) == ["A\textra.txt", "A\tspec/compiled/architecture.yml"]
    for rel in ("extra.txt", "spec/compiled/architecture.yml"):
        assert (clone / rel).read_bytes() == (src / rel).read_bytes()
        mode = _git(clone, "ls-tree", res["tree_sha"], rel).split()[0]
        assert mode == "100644"
    assert (clone / "spec/compiled/architecture.yml").read_bytes() == ledger.read_bytes()
    assert "ledger: present" in res["log"]


# --- (c) US-003: faults stay SETUP-ERROR (return 2) -----------------------------------------------


def test_c1_a_plain_directory_is_a_setup_error(tmp_path: Path) -> None:
    """A non-git directory returns 2 with no tree_sha."""
    plain = tmp_path / "plain"
    plain.mkdir()
    res = _run_copy_tree(plain, tmp_path)
    assert res["rc"] == 2
    assert res["tree_sha"] == ""


def test_c2_a_ledger_directory_symlinked_outside_is_refused(tmp_path: Path) -> None:
    """A spec/compiled symlink that leaves the tree is refused (return 2)."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "architecture.yml").write_text("schema: x\n", encoding="utf-8")
    src = _fixture(tmp_path / "src", {"a.txt": "a\n"}, ledger_tools=True)
    (src / "spec").mkdir()
    (src / "spec/compiled").symlink_to(outside, target_is_directory=True)
    _git(src, "add", "--force", "spec/compiled")
    _git(src, "commit", "-q", "-m", "link")
    res = _run_copy_tree(src, tmp_path)
    assert res["rc"] == 2, res["log"]
    assert "resolves outside the tree" in res["log"]
    assert res["tree_sha"] == ""


def test_c3_a_failing_commit_hook_stops_at_the_commit(tmp_path: Path) -> None:
    """A failing pre-commit hook returns 2 at the commit, before tree_sha is set."""
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    hook = hooks / "pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    hook.chmod(hook.stat().st_mode | stat.S_IXUSR)
    src = _fixture(tmp_path / "src", {"a.txt": "a\n"}, ledger_tools=True)
    env = {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "core.hooksPath",
        "GIT_CONFIG_VALUE_0": str(hooks),
    }
    res = _run_copy_tree(src, tmp_path, extra_env=env)
    assert res["rc"] == 2, res["log"]
    assert res["tree_sha"] == ""
    assert res["clone"].is_dir()
    assert _git(res["clone"], "rev-parse", "HEAD") == _git(src, "rev-parse", "HEAD")


def test_c4_missing_ledger_script_is_a_setup_error_after_the_commit(tmp_path: Path) -> None:
    """A tree without hyper_ledger.py returns 2 from provision-ledger, never a pass."""
    src = _fixture(tmp_path / "src", {"a.txt": "a\n"}, ledger_tools=False)
    res = _run_copy_tree(src, tmp_path)
    assert res["rc"] == 2, res["log"]
    assert res["tree_sha"] == _git(res["clone"], "rev-parse", "HEAD")
    assert res["tree_sha"] != _git(src, "rev-parse", "HEAD")
    assert "openrig_compat.py provision-ledger:" in res["log"]


def test_c5_a_failing_uv_sync_is_a_setup_error(tmp_path: Path) -> None:
    """A failing uv sync still returns 2 once the ledger is present."""
    src = _fixture(tmp_path / "src", {".gitignore": "/spec/\n"}, ledger_tools=True)
    (src / "spec/compiled").mkdir(parents=True)
    (src / "spec/compiled/architecture.yml").write_bytes(b"x\n")
    res = _run_copy_tree(src, tmp_path, uv_rc=1)
    assert res["rc"] == 2, res["log"]
    assert res["tree_sha"] != ""
    assert "ledger: present" in res["log"]


# --- (e) unit: an existing ledger is left alone ---------------------------------------------------


def test_e_provision_ledger_leaves_an_existing_ledger_untouched(tmp_path: Path) -> None:
    """An existing ledger is reported present and left byte- and mtime-identical."""
    ledger = tmp_path / "spec/compiled/architecture.yml"
    ledger.parent.mkdir(parents=True)
    ledger.write_bytes(b"whatever\n")
    os.utime(ledger, (1_000_000_000, 1_000_000_000))
    before = (ledger.read_bytes(), ledger.stat().st_mtime_ns)
    done = subprocess.run(
        ["python3", str(COMPAT), "provision-ledger", "--root", str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "ledger: present"
    assert (ledger.read_bytes(), ledger.stat().st_mtime_ns) == before
