"""The HACF_COMPAT_WATCHER_VERSIONS marker of dev/openrig_compat.sh (OPR.99.0.12.2 MR 1-2).

The marker names the watcher entries a compat run vouches for, so the exact contract check in
`test_contract_show` tolerates exactly those and nothing else. It is set in two places only: the
oracle step and the accept invocation. Every case runs under tmp_path with a fake `uv`.
"""

from __future__ import annotations

import os
import re
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT
from openrig_versions import unlisted_patches

sys.path.insert(0, str(REPO_ROOT / "dev"))
import openrig_compat as oc  # noqa: E402

SCRIPT = REPO_ROOT / "dev" / "openrig_compat.sh"
COMPAT = REPO_ROOT / "dev" / "openrig_compat.py"
MARK = "HACF_COMPAT_WATCHER_VERSIONS"
INTEGRITY = "sha512-" + "A" * 86 + "=="


def _contract_tree(root: Path, watchers: tuple[str, ...]) -> Path:
    """A tree whose contract is the shipped person entries plus the given watcher entries."""
    text = (REPO_ROOT / oc.CONTRACT_PATH).read_text(encoding="utf-8")
    lines = [
        ln for ln in text.splitlines(keepends=True)
        if not (oc._ENTRY_RE.match(ln) and "accepted_by: watcher" in ln)
    ]  # fmt: skip
    last = max(i for i, ln in enumerate(lines) if oc._ENTRY_RE.match(ln))
    extra = [
        f'  - {{version: "{v}", integrity: "{INTEGRITY}", verdict: "x", accepted_by: watcher}}\n'
        for v in watchers
    ]
    path = root / oc.CONTRACT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    (root / ".agents/scripts").mkdir(parents=True, exist_ok=True)
    for name in ("hyper_openrig.py", "_runtime.py"):
        if (REPO_ROOT / ".agents/scripts" / name).exists():
            (root / ".agents/scripts" / name).write_bytes(
                (REPO_ROOT / ".agents/scripts" / name).read_bytes()
            )
    path.write_text("".join([*lines[: last + 1], *extra, *lines[last + 1 :]]), encoding="utf-8")
    return path


def _versions(tmp_path: Path, n: int = 3) -> list[str]:
    base = _contract_tree(tmp_path / "base", ())
    return unlisted_patches(oc._shipped(base.parents[2])._parse_contract(base.read_bytes()), n)


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["python3", str(COMPAT), "watcher-versions", *args],
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_watcher_versions_prints_watchers_in_file_order_then_plus(tmp_path: Path) -> None:
    """The watcher entries in file order, then --plus when it is not already among them."""
    a, b, c = _versions(tmp_path)
    contract = _contract_tree(tmp_path / "t", (a, b))
    got = _run("--contract", str(contract))
    assert (got.returncode, got.stdout.strip()) == (0, f"{a},{b}")
    got = _run("--contract", str(contract), "--plus", c)
    assert got.stdout.strip() == f"{a},{b},{c}"
    got = _run("--contract", str(contract), "--plus", a)
    assert got.stdout.strip() == f"{a},{b}"
    empty = _contract_tree(tmp_path / "none", ())
    got = _run("--contract", str(empty))
    assert (got.returncode, got.stdout) == (0, "\n")


@pytest.mark.parametrize("plus", ["v0.7.22", "0.7", "0.7.22-rc.1", "", "0.7.022"])
def test_watcher_versions_refuses_a_bad_plus(tmp_path: Path, plus: str) -> None:
    """A --plus that is not a plain M.m.p exits 2 with one line."""
    contract = _contract_tree(tmp_path / "t", ())
    got = _run("--contract", str(contract), "--plus", plus)
    assert got.returncode == 2 and len(got.stderr.strip().splitlines()) == 1


def test_watcher_versions_refuses_an_unreadable_or_invalid_file(tmp_path: Path) -> None:
    """A missing file and a file the tree's parser rejects both exit 2."""
    tree = tmp_path / "t"
    contract = _contract_tree(tree, ())
    assert _run("--contract", str(tree / "nope" / "x" / "missing.yml")).returncode == 2
    contract.write_text("schema: 1\naccepted: nonsense\n", encoding="utf-8")
    got = _run("--contract", str(contract))
    assert got.returncode == 2 and got.stdout == ""


def _function(name: str) -> str:
    text = SCRIPT.read_text(encoding="utf-8")
    start = text.index(f"{name}() {{")
    return text[start : text.index("\n}\n", start) + 3]


def test_the_marker_appears_only_in_the_oracle_step_and_the_accept_block() -> None:
    """MR 2(c): nowhere else in the shell script, and not set by check.sh or the watcher."""
    text = SCRIPT.read_text(encoding="utf-8")
    spans = []
    oracle = _function("delegate_oracle")
    spans.append((text.index(oracle), text.index(oracle) + len(oracle)))
    accept_start = text.index("accept_env=(env")
    spans.append((accept_start, len(text)))
    for match in re.finditer(MARK, text):
        assert any(a <= match.start() < b for a, b in spans), "marker outside its two places"
    assert MARK in oracle and MARK in text[accept_start:]
    for other in ("dev/scripts/check.sh", "dev/openrig_watch.sh"):
        assert MARK not in (REPO_ROOT / other).read_text(encoding="utf-8")
    assert "export " + MARK not in text


def _run_oracle(tmp_path: Path, state: str, watchers: tuple[str, ...], version: str) -> str | None:
    """Run delegate_oracle from the script with a fake uv that records the marker it sees."""
    _contract_tree(tmp_path / "T" / "hacf", ())  # the tree under test: its scripts parse the copy
    (tmp_path / "T/state").mkdir()
    orig = _contract_tree(tmp_path / "orig", watchers)
    (tmp_path / "T/state/contract.orig").write_bytes(orig.read_bytes())
    seen = tmp_path / "seen"
    bindir = tmp_path / "bin"
    bindir.mkdir()
    uv = bindir / "uv"
    uv.write_text(
        f'#!/bin/sh\nif [ "${{{MARK}+set}}" = set ]; then printf "%s" "${MARK}" > {seen}; '
        f"else printf UNSET > {seen}; fi\n",
        encoding="utf-8",
    )
    uv.chmod(uv.stat().st_mode | stat.S_IXUSR)
    script = (
        _function("delegate_oracle")
        + f"T={tmp_path / 'T'}\ncompat={COMPAT}\nversion={version}\ncontract_state={state}\n"
        + "delegate_oracle\n"
    )
    env = {k: v for k, v in os.environ.items() if k != MARK}
    env["PATH"] = f"{bindir}{os.pathsep}{env['PATH']}"
    done = subprocess.run(
        ["bash", "-c", script], env=env, capture_output=True, text=True, timeout=120
    )
    assert done.returncode == 0, done.stderr
    return seen.read_text(encoding="utf-8")


def test_the_oracle_step_sets_the_marker(tmp_path: Path) -> None:
    """not-listed: the original watchers plus the added version; unlisted: the watchers only."""
    a, b, c = _versions(tmp_path)
    assert _run_oracle(tmp_path / "x1", "not-listed", (a,), b) == f"{a},{b}"
    assert _run_oracle(tmp_path / "x2", "not-listed", (), b) == b
    assert _run_oracle(tmp_path / "x3", "unlisted", (a, b), b) == f"{a},{b}"
    assert _run_oracle(tmp_path / "x4", "sole", (), c) == "UNSET"


def test_accept_refuse_records_an_error_and_commits_nothing(tmp_path: Path) -> None:
    """--refuse (the shell's answer to a failed watcher-versions) records `error`, no accept run."""
    evidence = tmp_path / "ev"
    evidence.mkdir()
    done = subprocess.run(
        ["python3", str(COMPAT), "accept", "--root", str(tmp_path / "nope"), "--version",
         "0.7.9999", "--evidence", str(evidence), "--refuse", "watcher-versions failed", "--",
         "true"],
        capture_output=True,
        text=True,
        timeout=60,
    )  # fmt: skip
    assert done.returncode == 1 and "accept error" in done.stdout
    recorded = (evidence / "accept.json").read_text(encoding="utf-8")
    assert '"result": "error"' in recorded and "watcher-versions failed" in recorded
    assert not (tmp_path / "nope").exists()
