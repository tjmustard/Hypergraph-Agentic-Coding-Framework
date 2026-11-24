"""Tests of dev/release_export.py's `plan` and `verify-push`, and of its no-push rule (slice 3).

Offline and synthetic: the gate world of test_gate.py, gated in-process with the injected runner,
and a local bare repository standing in for the remote. The tool never pushes; only these tests
push, and only to that local bare repository, to prove the plan's printed commands and expected
outputs against a real git.
"""

from __future__ import annotations

import ast
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from test_build import BASE_EMAIL, NAME, TOOL, clean_env, g
from test_gate import (
    FakeRunner,
    GateWorld,
    build_both,
    make_gate_world,
    run_gate,
    write_digests,
)

VERSIONS = ("0.1.0", "0.2.0")


@pytest.fixture
def gw(tmp_path: Path) -> GateWorld:
    """The gate world of test_gate.py."""
    return make_gate_world(tmp_path)


@pytest.fixture
def digests(tmp_path: Path) -> Path:
    """A digest file with one fake digest."""
    return write_digests(tmp_path)


@pytest.fixture(autouse=True)
def py310(monkeypatch: pytest.MonkeyPatch) -> None:
    """The gate needs HACF_PY310; the injected runner never uses it."""
    monkeypatch.setenv("HACF_PY310", "/opt/fake-python3.10")


@pytest.fixture(autouse=True)
def digests_env(monkeypatch: pytest.MonkeyPatch, digests: Path) -> None:
    """`plan` loads the digests (it prints every message in full): from the environment here."""
    monkeypatch.setenv("HACF_EXPORT_DIGESTS", str(digests))


@pytest.fixture
def gated(gw: GateWorld, digests: Path, tmp_path: Path) -> tuple[GateWorld, Path, Path]:
    """Both releases exported and gated PASS; a local bare remote with main at the base."""
    profile = gw.profile()
    build_both(gw, profile)
    root = tmp_path / "reports"
    for version in VERSIONS:
        assert run_gate(gw, profile, version, digests, root)[0] == 0
    remote = tmp_path / "remote.git"
    g(tmp_path, "init", "-q", "--bare", str(remote))
    g(gw.src, "remote", "add", "origin", str(remote))
    g(gw.src, "push", "-q", "origin", f"{gw.base}:refs/heads/main")
    return gw, profile, root


def cli(gw: GateWorld, profile: Path, *args: str, env: dict | None = None):
    """Run the tool's command line."""
    return subprocess.run(
        [sys.executable, str(TOOL), "--repo", str(gw.src), "--profile", str(profile), *args],
        capture_output=True, text=True, env=env or os.environ.copy(), check=False,
    )  # fmt: skip


def sha(gw: GateWorld, spec: str) -> str:
    """Resolve a revision in the private repository."""
    return g(gw.src, "rev-parse", spec)


def tag_object(gw: GateWorld, commit: str, version: str, message: str) -> str:
    """A new annotated tag object for `commit`."""
    text = (
        f"object {commit}\ntype commit\ntag v{version}\n"
        f"tagger {NAME} <{BASE_EMAIL}> 1 +0000\n\n{message}\n"
    )
    return subprocess.run(
        ["git", "-C", str(gw.src), "mktag"],
        input=text,
        text=True,
        capture_output=True,
        env=clean_env(),
        check=True,
    ).stdout.strip()


# -------------------------------------------------------------------------------------- plan


def test_plan_prints_every_message_and_the_atomic_commands(gated, tmp_path: Path) -> None:
    """The plan covers both versions, every object in full, and dry-run-first atomic pushes."""
    gw, profile, root = gated
    proc = cli(gw, profile, "plan", "--report-root", str(root))
    assert proc.returncode == 0, proc.stderr
    text = (root / "PUSH-PLAN.md").read_text()
    assert proc.stdout.startswith(text)
    tip = sha(gw, "v0.2.0^{commit}")
    for version in VERSIONS:
        assert g(gw.src, "cat-file", "-p", f"v{version}^{{commit}}") in text
        assert g(gw.src, "cat-file", "-p", f"v{version}") in text
        assert f"### v{version}" in text
    refs = f"origin {tip}:refs/heads/main refs/tags/v0.1.0 refs/tags/v0.2.0"
    dry = f"git push --dry-run --porcelain --atomic {refs}"
    real = f"git push --porcelain --atomic {refs}"
    assert dry in text and real in text
    assert text.index(dry) < text.index(real)
    assert (
        f"verify-push --through 0.2.0 --report-root {shlex.quote(str(root))} --remote origin"
        in (text)
    )
    assert "| dev/tests/test_private.py | private | 1 | 0 |" in text
    assert "never rebuild a published version" in text and "retry the push once" in text


def test_plan_verify_push_line_runs_as_printed(gated) -> None:
    """The printed verify-push line carries --report-root and runs, through a shell, as printed.

    Only its tool prefix is pointed at this world (the line names no --repo or --profile).
    """
    gw, profile, root = gated
    text = cli(gw, profile, "plan", "--report-root", str(root)).stdout
    (line,) = [ln.strip() for ln in text.splitlines() if "verify-push --through" in ln]
    prefix = "python3 dev/release_export.py "
    assert line.startswith(prefix) and f"--report-root {shlex.quote(str(root))}" in line
    tool = (
        f"{shlex.quote(sys.executable)} {shlex.quote(str(TOOL))} --repo {shlex.quote(str(gw.src))}"
    )
    tool += f" --profile {shlex.quote(str(profile))} "
    proc = subprocess.run(line.replace(prefix, tool, 1), shell=True, cwd=gw.src,
                          capture_output=True, text=True, check=False)  # fmt: skip
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert all(ln.startswith("ok   ") for ln in proc.stdout.splitlines()), proc.stdout


def test_plan_commands_match_a_real_git(gated, tmp_path: Path) -> None:
    """The plan's dry run and push, run here on the local bare remote, print what it expects."""
    gw, profile, root = gated
    text = cli(gw, profile, "plan", "--report-root", str(root)).stdout
    commands = [ln.strip() for ln in text.splitlines() if ln.strip().startswith("git push")]
    assert len(commands) == 2
    tip = sha(gw, "v0.2.0^{commit}")
    for command in commands:
        out = subprocess.run(command.split(), cwd=gw.src, capture_output=True, text=True,
                             env=clean_env(), check=True).stdout  # fmt: skip
        lines = out.splitlines()
        assert lines[-1] == "Done"
        assert lines[1].startswith(f" \t{tip}:refs/heads/main\t{gw.base[:7]}")
        assert lines[2:4] == [f"*\trefs/tags/v{v}:refs/tags/v{v}\t[new tag]" for v in VERSIONS]
    listing = g(gw.src, "ls-remote", "origin", "refs/heads/main", "refs/tags/v*")
    expected = text.split("5. Verify:")[1].split("## Recovery")[0]
    for line in listing.splitlines():
        assert line in expected


def test_plan_stops_at_a_failing_gate(gw: GateWorld, digests: Path, tmp_path: Path) -> None:
    """A FAIL report for 0.1.0 leaves no prefix (exit 2); for 0.2.0 the plan stops after 0.1.0."""
    profile = gw.profile()
    build_both(gw, profile)
    root = tmp_path / "r"
    run_gate(gw, profile, "0.1.0", digests, root, FakeRunner(check_code=1))
    proc = cli(gw, profile, "plan", "--report-root", str(root))
    assert proc.returncode == 2 and "the gate report says FAIL" in proc.stderr
    run_gate(gw, profile, "0.1.0", digests, root)
    run_gate(gw, profile, "0.2.0", digests, root, FakeRunner(check_code=1))
    proc = cli(gw, profile, "plan", "--report-root", str(root))
    assert proc.returncode == 0, proc.stderr
    assert "stopped after v0.1.0: v0.2.0: the gate report says FAIL" in proc.stdout
    assert "refs/tags/v0.2.0" not in proc.stdout.split("## Commands")[1]


# ------------------------------------------------------------------------------ verify-push


def verify(gw: GateWorld, profile: Path, root: Path, *extra: str):
    """Run `verify-push --through 0.2.0`."""
    return cli(gw, profile, "verify-push", "--through", "0.2.0", "--report-root", str(root), *extra)


def test_verify_push_passes(gated) -> None:
    """Every check passes against the local remote; --no-remote skips the remote checks."""
    gw, profile, root = gated
    proc = verify(gw, profile, root)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert all(ln.startswith("ok   ") for ln in proc.stdout.splitlines()), proc.stdout
    assert "ok   origin's main is the base or a planned export" in proc.stdout
    proc = verify(gw, profile, root, "--no-remote")
    assert proc.returncode == 0
    assert "skipped (--no-remote): the remote's main and tags" in proc.stdout
    assert "origin's main" not in proc.stdout


def test_retagged_after_the_gate_fails(gated) -> None:
    """A tag re-pointed after the gate fails, though it peels to the same export."""
    gw, profile, root = gated
    export = sha(gw, "v0.2.0^{commit}")
    g(gw.src, "update-ref", "refs/tags/v0.2.0", tag_object(gw, export, "0.2.0", "retagged"))
    proc = verify(gw, profile, root, "--no-remote")
    assert proc.returncode == 1
    assert "FAIL v0.2.0: the tag object is the gated one: the tag was re-pointed" in proc.stdout
    assert "ok   v0.2.0: the tag peels to its export" in proc.stdout


def repoint(gw: GateWorld, *parents: str) -> str:
    """Re-make the 0.2.0 export with other parents; point its tag and public/main at it."""
    tree = sha(gw, "v0.2.0^{tree}")
    args = [a for p in parents for a in ("-p", p)]
    env = clean_env(GIT_AUTHOR_NAME=NAME, GIT_AUTHOR_EMAIL=BASE_EMAIL,
                    GIT_COMMITTER_NAME=NAME, GIT_COMMITTER_EMAIL=BASE_EMAIL)  # fmt: skip
    commit = g(gw.src, "commit-tree", tree, *args, "-m", "re-made", env=env)
    g(gw.src, "update-ref", "refs/tags/v0.2.0", tag_object(gw, commit, "0.2.0", "HACF v0.2.0"))
    g(gw.src, "update-ref", "refs/heads/public/main", commit)
    return commit


def test_export_parented_on_a_private_commit_fails(gated) -> None:
    """An export whose parent is a private commit is outside the chain."""
    gw, profile, root = gated
    repoint(gw, gw.side)
    proc = verify(gw, profile, root, "--no-remote")
    assert proc.returncode == 1
    assert "FAIL rev-list from the base lists exactly the exports" in proc.stdout
    assert "FAIL v0.2.0: a PASS report with the same tree: no report" in proc.stdout


def test_two_parent_export_fails(gated) -> None:
    """A merge in the chain fails the one-parent check."""
    gw, profile, root = gated
    repoint(gw, sha(gw, "v0.1.0^{commit}"), gw.side)
    proc = verify(gw, profile, root, "--no-remote")
    assert proc.returncode == 1
    assert "FAIL each export has exactly one parent" in proc.stdout


def test_remote_main_not_the_base_fails(gated) -> None:
    """A remote whose main is neither the base nor a planned export fails."""
    gw, profile, root = gated
    g(gw.src, "push", "-q", "--force", "origin", f"{gw.side}:refs/heads/main")
    proc = verify(gw, profile, root)
    assert proc.returncode == 1
    assert f"FAIL origin's main is the base or a planned export: origin's main is {gw.side}" in (
        proc.stdout
    )
    assert verify(gw, profile, root, "--no-remote").returncode == 0


def test_planned_tag_already_on_the_remote_fails(gated) -> None:
    """A planned tag that the remote already has fails; a published prefix passes."""
    gw, profile, root = gated
    g(gw.src, "push", "-q", "origin", "refs/tags/v0.2.0")
    proc = verify(gw, profile, root)
    assert proc.returncode == 1 and "FAIL v0.2.0 is not on origin yet" in proc.stdout
    g(gw.src, "push", "-q", "origin", ":refs/tags/v0.2.0")
    g(
        gw.src,
        "push",
        "-q",
        "origin",
        f"{sha(gw, 'v0.1.0^{commit}')}:refs/heads/main",
        "refs/tags/v0.1.0",
    )
    proc = verify(gw, profile, root)
    assert proc.returncode == 0, proc.stdout
    assert "ok   v0.1.0 on origin is the local tag" in proc.stdout


def test_public_main_moved_fails(gated) -> None:
    """public/main away from the planned tip fails."""
    gw, profile, root = gated
    g(gw.src, "update-ref", "refs/heads/public/main", sha(gw, "v0.1.0^{commit}"))
    proc = verify(gw, profile, root, "--no-remote")
    assert proc.returncode == 1 and "FAIL public/main is the v0.2.0 export" in proc.stdout


# --------------------------------------------------------------------------------- no push


def test_no_push_subcommand_in_any_argv() -> None:
    """`push` appears in no argv list or call of the tool; push commands are only printed text."""
    tree = ast.parse(TOOL.read_text())
    words, printed = set(), []
    for node in ast.walk(tree):
        if isinstance(node, (ast.List, ast.Tuple, ast.Call)):
            items = node.args if isinstance(node, ast.Call) else node.elts
            words |= {
                i.value for i in items if isinstance(i, ast.Constant) and isinstance(i.value, str)
            }
        if isinstance(node, ast.JoinedStr):
            text = "".join(v.value for v in node.values if isinstance(v, ast.Constant))
            if "git push" in text:
                printed.append(text)
    assert "push" not in words
    assert not any(w.startswith("push ") or w.endswith(" push") for w in words)
    assert any("git push --dry-run --porcelain" in t for t in printed)
    assert any(t.strip().startswith("git push --porcelain") for t in printed)


def test_no_git_push_is_run(gated, digests: Path, tmp_path: Path) -> None:
    """Every git the tool runs during gate, plan and verify-push is logged; none is a push."""
    gw, profile, root = gated
    real = shutil.which("git")
    shim_dir = tmp_path / "shim"
    shim_dir.mkdir()
    log = tmp_path / "git-argv.log"
    (shim_dir / "git").write_text(
        f'#!/bin/sh\nprintf "%s\\n" "$*" >> "{log}"\nexec "{real}" "$@"\n'
    )
    (shim_dir / "git").chmod(0o755)
    env = {**os.environ, "PATH": f"{shim_dir}{os.pathsep}{os.environ['PATH']}"}
    assert cli(gw, profile, "plan", "--report-root", str(root), env=env).returncode == 0
    assert verify(gw, profile, root).returncode == 0  # without the shim: a baseline
    proc = cli(
        gw, profile, "verify-push", "--through", "0.2.0", "--report-root", str(root), env=env
    )
    assert proc.returncode == 0, proc.stdout
    os.environ["PATH"], saved = env["PATH"], os.environ["PATH"]
    try:
        run_gate(gw, profile, "0.2.0", digests, tmp_path / "again")
    finally:
        os.environ["PATH"] = saved
    calls = log.read_text().splitlines()
    assert len(calls) > 20
    for call in calls:
        args = call.split()
        # skip -C <dir> and -c <k=v> pairs and the other leading options to reach the subcommand
        i = 0
        while i < len(args) and args[i].startswith("-"):
            i += 2 if args[i] in ("-C", "-c") else 1
        assert i < len(args) and args[i] != "push", call
    assert any(" ls-remote " in f" {c} " for c in calls)


def test_plan_refuses_without_digests(gated) -> None:
    """`plan` prints every message in full, so it needs the digests for the redaction (R9)."""
    gw, profile, root = gated
    env = {k: v for k, v in os.environ.items() if k != "HACF_EXPORT_DIGESTS"}
    proc = cli(gw, profile, "plan", "--report-root", str(root), env=env)
    assert proc.returncode == 2
    assert "no digest file" in proc.stderr
    assert not (root / "PUSH-PLAN.md").exists()
