"""A clean install from a local source brings none of HACF's lore, missions, memory or history.

Slice 33 (OPR.0.6.0.33; mission SPEC R59, Red Team F6, F14, F20). TJ: "install into an empty repo
and show that none of HACF's own lore/history/memory arrives and that lore/missions start empty".

The source is a temp non-bare repo holding HACF's working payload (uncommitted edits included)
plus a sentinel in every NEVER_COPY path and in HACF-style lore, missions, memory and history. The
oracles are independent of NEVER_COPY: a path allowlist written out here (Test 1), a sentinel scan
(Test 2), a content scan for HACF lore ids (Test 3), the empty lore and missions (Test 4), no
source objects in the project's git (Test 5), an untouched source (Test 6) and a plain uninstall
round trip (Test 7). `test_real_checkout_round_trip` runs the same checks against this checkout
at HEAD; it is `live`, so check.sh skips it and the auditor runs it once after the commit.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
from test_shipped_lore_refs import PATTERN, allowed

from .conftest import (
    INSTALLER,
    REPO_ROOT,
    git,
    git_env,
    run_installer,
    run_uninstall_script,
    snapshot,
    working_tree_files,
)

SENTINEL = "HACF-CLEAN-INSTALL-SENTINEL-7f3a"
NO_URL = {"HACF_REPO_URL": None}  # only the --source checkout may be read
SOURCE_REL = ".agents/state/install/source.json"

# Test 1: the independent allowlist of installed paths (never derived from NEVER_COPY).
ALLOWED_PREFIXES = (
    ".agents/scripts/",
    ".agents/schemas/",
    ".agents/config/",
    ".agents/rules/",
    ".agents/install-templates/",
    ".agents/skills/",
    ".agents/state/",
    ".claude/commands/",
)
ALLOWED_FILES = {
    ".agents/hacf-manifest.json",
    ".agents/memory/activeContext.md",
    ".claude/settings.json",
    "CLAUDE.md",
    "AGENTS.md",
    "GEMINI.md",
    ".agentignore",
    "HACF-uninstall.sh",
    ".gitignore",
    ".cursorignore",
    ".rooignore",
    ".codeiumignore",
    ".geminiignore",
    "SPEC.md",
    "project.yaml",
    "workspace.yaml",
    "lore_hypergraph.yml",
    "lore/MAP.md",
    "lore/CANON.md",
    "lore/ADRs/.gitkeep",
    "lore/STORIES/.gitkeep",
    "lore/proposed/.gitkeep",
    "lore/canon/.gitkeep",
    # Created by hyper_scaffold.py's plan() (LOCAL_CONFIG_REL and TEST_DIRS), checked there.
    ".agents/local/hacf.yml",
    "tests/candidate_outputs/.gitkeep",
    "tests/fixtures/.gitkeep",
    "tests/integration/.gitkeep",
}
# R65 (known issue K1, removed by KI-6): a fresh install used to back up the settings.json it had
# just written. It no longer does, so Test 1 allows no such file; Tests 2 and 3 still scan
# .agents/.backup/**.
K1_BACKUP = re.compile(r"^\.agents/\.backup/[0-9]{8}T[0-9]{6}Z/\.claude/settings\.json$")
LORE_FILES = {
    "MAP.md",
    "CANON.md",
    "ADRs/.gitkeep",
    "STORIES/.gitkeep",
    "proposed/.gitkeep",
    "canon/.gitkeep",
}


def on_allowlist(rel: str) -> bool:
    """Return True when an installed path is on the Test 1 allowlist."""
    return rel in ALLOWED_FILES or rel.startswith(ALLOWED_PREFIXES)


def never_copy_items() -> list[str]:
    """Return HACF-install.sh's NEVER_COPY array, parsed as test_never_copy.py parses it."""
    text = INSTALLER.read_text(encoding="utf-8")
    body = re.search(r"^NEVER_COPY=\((.*?)^\)", text, re.S | re.M)
    assert body is not None
    items = re.findall(r'"([^"]+)"', body.group(1))
    assert items
    return items


def sentinel_paths() -> list[str]:
    """Return the source paths that get the sentinel: each NEVER_COPY path plus HACF-style lore."""
    paths = []
    for item in never_copy_items():
        if item.endswith("/"):
            paths.append(item + "sentinel.md")
        else:
            paths.append(item.replace("*", ""))  # .claude/settings*.json -> settings.json
    paths += [
        "lore/ADRs/ADR-0001-x.md",
        "lore/STORIES/STORY-0001-x.md",
        "missions/m/SPEC.md",
        "lore_hypergraph.yml",
        ".agents/memory/activeContext.md",
        "CHANGELOG.md",
    ]
    return sorted(set(paths))


def files_under(root: Path) -> list[str]:
    """Return every file and symlink under root, relative, excluding .git/."""
    return sorted(
        rel
        for rel in snapshot(root)
        if not rel.endswith("/") and not rel.startswith(".git/") and rel != ".git"
    )


def sha_bytes(path: Path) -> str:
    """Return the sha256 of a file's bytes."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_state(repo: Path, env: dict[str, str]) -> dict[str, object]:
    """Return the source state Test 6 compares: .git file hashes, hooks listing and refs."""
    dot_git = repo / ".git"
    return {
        "index": sha_bytes(dot_git / "index"),
        "HEAD": sha_bytes(dot_git / "HEAD"),
        "config": sha_bytes(dot_git / "config"),
        "hooks": sorted(p.name for p in (dot_git / "hooks").iterdir())
        if (dot_git / "hooks").is_dir()
        else [],
        "refs": git(["for-each-ref"], repo, env),
    }


@dataclass
class Installed:
    """One clean install: the target, its pre-install snapshot and the source's facts."""

    target: Path
    before: dict[str, str]
    source: Path
    head: str  # S
    roots: list[str]  # the source's first (parentless) commits
    source_before: dict[str, object]
    source_after: dict[str, object]
    output: str
    scratch: Path


def build_source(dest: Path, env: dict[str, str]) -> Path:
    """Return a temp non-bare source repo: HACF's working payload, then a sentinel commit."""
    src = dest / "source"
    src.mkdir(parents=True)
    for rel in working_tree_files():  # git ls-files -co --exclude-standard (the F14 payload)
        source = REPO_ROOT / rel
        if not source.is_file() or source.is_symlink() or "__pycache__" in rel:
            continue
        target = src / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    git(["init", "-q", "-b", "main"], src, env)
    git(["add", "-A", "-f", "--", "."], src, env)
    git(["-c", "commit.gpgsign=false", "commit", "-q", "-m", "payload"], src, env)
    for rel in sentinel_paths():
        path = src / rel
        if path.is_dir():
            path = path / "sentinel.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        old = path.read_text(encoding="utf-8") if path.is_file() else ""
        if old and not old.endswith("\n"):
            old += "\n"
        path.write_text(f"{old}# {SENTINEL} {rel}\n", encoding="utf-8")
    git(["add", "-A", "-f", "--", "."], src, env)
    git(["-c", "commit.gpgsign=false", "commit", "-q", "-m", "history and lore"], src, env)
    return src


def new_target(path: Path, env: dict[str, str]) -> Path:
    """Return an empty project with one commit (README.md)."""
    path.mkdir(parents=True)
    git(["init", "-q", "-b", "main"], path, env)
    (path / "README.md").write_text("# demo\n", encoding="utf-8")
    git(["add", "--", "README.md"], path, env)
    git(["-c", "commit.gpgsign=false", "commit", "-q", "-m", "project"], path, env)
    return path


def install(source: Path, target: Path, scratch: Path, env: dict[str, str]) -> Installed:
    """Install from source into target with -y --ides=claude and record what the tests need."""
    head = git(["rev-parse", "HEAD"], source, env).strip()
    roots = git(["rev-list", "--max-parents=0", "HEAD"], source, env).split()
    before = snapshot(target)
    source_before = git_state(source, env)
    run = run_installer(
        scratch, "", target, "-y", "--ides=claude", f"--source={source}", env=NO_URL
    )
    assert run.returncode == 0, run.output
    return Installed(
        target,
        before,
        source,
        head,
        roots,
        source_before,
        git_state(source, env),
        run.output,
        scratch,
    )


# ---------------------------------------------------------------------------
# The checks (shared by the fixture tests and the live real-checkout test)
# ---------------------------------------------------------------------------


def check_allowlist(done: Installed) -> None:
    """Test 1: every installed path is on the independent allowlist; there is no K1 backup."""
    rels = [rel for rel in files_under(done.target) if rel != "README.md"]
    backups = [rel for rel in rels if K1_BACKUP.match(rel)]
    assert len(backups) == 0, f"KI-6 removed the needless settings.json backup: {backups}"
    stray = [rel for rel in rels if not on_allowlist(rel) and rel not in backups]
    assert stray == [], f"installed paths outside the allowlist: {stray}"


def check_no_sentinel(done: Installed) -> None:
    """Test 2: the sentinel occurs in no file under the target."""
    leaked = [
        rel
        for rel in files_under(done.target)
        if SENTINEL.encode() in (done.target / rel).read_bytes()
    ]
    assert leaked == [], f"sentinel arrived in: {leaked}"


def check_content_scan(done: Installed) -> None:
    """Test 3: no installed file cites HACF lore ids or dev/design, beyond the Task 3 allowlist."""
    hits = []
    for rel in files_under(done.target):
        path = done.target / rel
        if path.is_symlink():
            continue
        for number, line in enumerate(path.read_bytes().decode("utf-8", "replace").splitlines(), 1):
            if PATTERN.search(line) and not allowed(rel, line):
                hits.append(f"{rel}:{number}: {line.strip()}")
    assert hits == []


def check_empty_lore(done: Installed) -> None:
    """Test 4: lore/ holds only the empty scaffold, missions/ is empty and the graph has no node."""
    lore = done.target / "lore"
    assert set(files_under(lore)) == LORE_FILES
    missions = done.target / "missions"
    assert not missions.exists() or not any(missions.iterdir())
    proc = subprocess.run(
        [sys.executable, ".agents/scripts/hyper_lore.py", "list", "--json"],
        cwd=done.target,
        env=git_env(done.scratch),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == []


def check_no_history(done: Installed) -> None:
    """Test 5: neither S nor the source's first commit is in the target; it has one commit."""
    env = git_env(done.scratch)
    for sha in [done.head, *done.roots]:
        proc = subprocess.run(
            ["git", "-C", str(done.target), "cat-file", "-e", sha],
            env=env,
            capture_output=True,
            timeout=60,
            check=False,
        )
        assert proc.returncode != 0, f"source object {sha} reached the project's git"
    assert git(["rev-list", "--all", "--count"], done.target, env).strip() == "1"


def check_source_untouched(done: Installed) -> None:
    """Test 6: the source's index, HEAD, config, hooks listing and refs are unchanged."""
    assert done.source_after == done.source_before


def leftovers(project: Path) -> dict[str, str]:
    """Return a project's snapshot without .git/ and the timestamped .agents/.backup/ dirs."""
    return {
        rel: value
        for rel, value in snapshot(project).items()
        if not rel.startswith((".git/", ".agents/.backup/"))
    }


def remote_leftovers(
    source: Path, commit: str, parent: Path, env: dict[str, str]
) -> dict[str, str]:
    """Return the leftovers of the same commit installed from a remote, then uninstalled.

    This is the uninstall tests' definition of "as before" (test_uninstall_local_source.py
    Test 1): the user data HACF scaffolds stays, and only HACF's own files go. The control's
    installer and uninstaller fetch `main`, so `main` in the bare control clone is pointed at
    `commit`, the commit under test (R66): a real checkout's `main` can be an older release.
    """
    parent.mkdir(parents=True)
    bare = parent / "control.git"
    git(["clone", "-q", "--bare", str(source), str(bare)], parent, env)
    git(["update-ref", "refs/heads/main", commit], bare, env)
    url = f"file://{bare}"
    control = new_target(parent / "target", env)  # the same directory name: SPEC.md names it
    scratch = parent / "run-scratch"
    scratch.mkdir()
    run = run_installer(scratch, url, control, "-y", "--ides=claude")
    assert run.returncode == 0, run.output
    run = run_uninstall_script(scratch, url, control, "-y", script=control / "HACF-uninstall.sh")
    assert run.returncode == 0, run.output
    return leftovers(control)


def check_round_trip(done: Installed, control_dir: Path) -> None:
    """Test 7: plain ./HACF-uninstall.sh -y leaves the uninstall tests' expected set.

    Every pre-install file is byte-identical, and the leftovers equal those of the same commit
    installed from a remote and uninstalled, plus the local-source record (and its directory).
    """
    env = git_env(done.scratch)
    run = run_uninstall_script(
        done.scratch, "", done.target, "-y", env=NO_URL, script=done.target / "HACF-uninstall.sh"
    )
    assert run.returncode == 0, run.output
    after = leftovers(done.target)
    before = {rel: value for rel, value in done.before.items() if not rel.startswith(".git/")}
    assert {rel: after.get(rel) for rel in before} == before
    assert not (done.target / ".agents/hacf-manifest.json").exists()
    assert not (done.target / "HACF-uninstall.sh").exists()
    remote = remote_leftovers(done.source, done.head, control_dir, env)
    # lead ruling P36: the uninstaller removes source.json itself (KI-3); only its directory stays
    extra = {".agents/state/install/"}
    assert set(after) - set(remote) == extra
    assert {rel: value for rel, value in after.items() if rel not in extra} == remote


# ---------------------------------------------------------------------------
# Tests 1-7 against the temp source (Task 5)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def clean(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, dict[str, str], Installed]:
    """Return (source, git env, the install) for one temp source and one empty target."""
    root = tmp_path_factory.mktemp("clean-install")
    env = git_env(root / "scratch")
    source = build_source(root, env)
    assert git(["rev-list", "--count", "HEAD"], source, env).strip() == "2"
    scratch = root / "run-scratch"
    scratch.mkdir()
    done = install(source, new_target(root / "target", env), scratch, env)
    return source, env, done


def test_source_holds_every_sentinel(clean) -> None:
    """The source really carries the sentinel in each NEVER_COPY path and the lore stand-ins."""
    source, _, _ = clean
    for rel in sentinel_paths():
        path = source / rel
        if path.is_dir():
            path = path / "sentinel.md"
        assert SENTINEL in path.read_text(encoding="utf-8"), rel
    assert (source / "lore_hypergraph.yml").read_text(encoding="utf-8").count(SENTINEL) == 1


def test_1_allowlist(clean) -> None:
    """Test 1: every installed path is on the independent allowlist."""
    check_allowlist(clean[2])


def test_2_no_sentinel(clean) -> None:
    """Test 2: no sentinel arrives."""
    check_no_sentinel(clean[2])


def test_3_content_scan(clean) -> None:
    """Test 3: no installed file matches the pattern beyond the grill-docs allowlist."""
    check_content_scan(clean[2])


def test_4_empty_lore_and_missions(clean) -> None:
    """Test 4: lore/ and missions/ start empty and the lore graph lists zero nodes."""
    check_empty_lore(clean[2])


def test_5_no_history(clean) -> None:
    """Test 5: no source object reaches the project's git."""
    check_no_history(clean[2])


def test_6_source_untouched(clean) -> None:
    """Test 6: the source repo is unchanged by the install."""
    check_source_untouched(clean[2])


def test_7_round_trip(tmp_path: Path, clean) -> None:
    """Test 7: install into a fresh target, then plain uninstall, restores it."""
    source, env, _ = clean
    scratch = tmp_path / "run-scratch"
    scratch.mkdir()
    done = install(source, new_target(tmp_path / "target", env), scratch, env)
    check_round_trip(done, tmp_path / "remote-flow")


# ---------------------------------------------------------------------------
# Task 6: the real checkout at HEAD (live; the auditor runs it once after the commit)
# ---------------------------------------------------------------------------


def _is_work_tree(path: Path) -> bool:
    proc = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--is-inside-work-tree"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    return proc.returncode == 0 and proc.stdout.strip() == "true"


def _live_selected(config: pytest.Config) -> bool:
    """Return True when the run selects live tests (`-m live`), not just collects them."""
    expr = config.getoption("markexpr") or ""
    return re.search(r"(?<!not )\blive\b", expr) is not None


@pytest.mark.live
def test_real_checkout_round_trip(tmp_path: Path, request: pytest.FixtureRequest) -> None:
    """Tests 1-7 against REPO_ROOT at HEAD (no payload copy), into a tmp_path repo.

    It installs HEAD, so before a slice's commit it tests the base, not the slice; it runs only
    when the run asks for live tests (`-m live`), as the auditor does once after the commit.
    """
    if not _live_selected(request.config):
        pytest.skip("live: run with -m live (the auditor runs it once after the commit)")
    if not _is_work_tree(REPO_ROOT):
        pytest.skip("REPO_ROOT is not a git work tree")
    env = git_env(tmp_path / "scratch")
    scratch = tmp_path / "run-scratch"
    scratch.mkdir()
    done = install(REPO_ROOT, new_target(tmp_path / "target", env), scratch, env)
    check_allowlist(done)
    check_no_sentinel(done)
    check_content_scan(done)
    check_empty_lore(done)
    check_no_history(done)
    check_source_untouched(done)
    check_round_trip(done, tmp_path / "remote-flow")
