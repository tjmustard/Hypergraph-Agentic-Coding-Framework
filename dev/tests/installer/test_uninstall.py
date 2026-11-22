"""Tests for the manifest-driven HACF-uninstall.sh with its legacy-hash fallback (S12c).

Each test installs with S12b's HACF-install.sh into a temp project (or builds the v0.5.9 fixture)
from a temp upstream built from this working tree (see conftest.py), then runs HACF-uninstall.sh
there. None writes inside the HACF checkout.
"""

from __future__ import annotations

import json
import os
import pty
import select
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from .conftest import (
    DEFAULT_URL,
    INSTALLER,
    REPO_ROOT,
    Run,
    git,
    install_env,
    network_wrapper,
    sha256,
    snapshot,
)

sys.path.insert(0, str(REPO_ROOT / ".agents" / "scripts"))
import hyper_blocks  # noqa: E402

UNINSTALLER = REPO_ROOT / "HACF-uninstall.sh"
GENERATOR = REPO_ROOT / "dev" / "scripts" / "build_legacy_hashes.py"
LEGACY_REL = ".agents/config/legacy_file_hashes.json"
MANIFEST_REL = ".agents/hacf-manifest.json"
LFS = "*.psd filter=lfs diff=lfs merge=lfs -text\n*.zip filter=lfs diff=lfs merge=lfs -text\n"


def run_uninstaller(
    scratch: Path,
    url: str,
    cwd: Path,
    *args: str,
    env: Optional[dict[str, Optional[str]]] = None,
    script: Path = UNINSTALLER,
) -> Run:
    """Run HACF-uninstall.sh in cwd with no terminal and, when possible, no network."""
    proc = subprocess.run(
        [*network_wrapper(), "bash", str(script), *args],
        cwd=cwd,
        env=install_env(scratch, url, env),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        timeout=300,
        check=False,
    )
    return Run(proc.returncode, proc.stdout.decode("utf-8", "replace"), [])


def run_in_pty(argv: list[str], cwd: Path, env: dict[str, str], answer) -> tuple[int, str]:
    """Run argv on a pseudo-terminal; answer(output) returns the bytes to type when it goes idle."""
    pid, fd = pty.fork()
    if pid == 0:  # the child: a new session whose controlling terminal is the pty
        try:
            os.chdir(cwd)
            os.execvpe(argv[0], argv, env)
        finally:
            os._exit(127)
    output = b""
    answered_at = 0
    deadline = time.monotonic() + 280
    try:
        while time.monotonic() < deadline:
            ready, _, _ = select.select([fd], [], [], 1.5)
            if not ready:
                if len(output) > answered_at:  # idle after new output: a question is waiting
                    os.write(fd, answer(output.decode("utf-8", "replace")))
                    answered_at = len(output)
                continue
            try:
                chunk = os.read(fd, 65536)
            except OSError:  # EIO: the child closed the terminal
                break
            if not chunk:
                break
            output += chunk
    finally:
        _, status = os.waitpid(pid, 0)
        os.close(fd)
    return os.waitstatus_to_exitcode(status), output.decode("utf-8", "replace")


def attributes_file(project: Path) -> Path:
    """Return the git common dir's info/attributes of a project."""
    common = subprocess.run(
        ["git", "rev-parse", "--git-common-dir"],
        cwd=project,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return (project / common / "info" / "attributes").resolve()


def without_block(path: Path, block_id: str) -> bytes:
    """Return a file's bytes with one HACF block removed (S12a's render_remove)."""
    doc = hyper_blocks.parse_doc(path.read_bytes())
    result = hyper_blocks.render_remove(doc, hyper_blocks.style_of(path), block_id)
    assert result is not None, f"{path} has no block {block_id}"
    return result.to_bytes()


def legacy_list() -> dict:
    """Return this checkout's legacy hash list."""
    return json.loads((REPO_ROOT / LEGACY_REL).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Test 1: with a manifest
# ---------------------------------------------------------------------------


def test_uninstall_preserves(project: Path, run_install, scratch: Path, upstream) -> None:
    """Test 1: every user byte survives; only HACF files, blocks, rules and hooks go (R10)."""
    user_files = {
        "pyproject.toml": '[project]\nname = "demo"\nversion = "0.1.0"\n',
        "uv.lock": "version = 1\n",
        "LICENSE": "MIT License\n",
        ".gitattributes": LFS,
        ".gitignore": "node_modules/\n*.log\n",
    }
    for rel, text in user_files.items():
        (project / rel).write_text(text, encoding="utf-8")
    run = run_install(project, "-y", "--ides=claude,cursor")
    assert run.returncode == 0, run.output
    manifest = json.loads((project / MANIFEST_REL).read_text(encoding="utf-8"))
    assert manifest["files"] and manifest["blocks"].get(".gitignore")

    # The user keeps working: lines after the HACF block, their own spec, lore and missions,
    # a skill and a command of their own, and an edit to one HACF file.
    with (project / ".gitignore").open("a", encoding="utf-8") as handle:
        handle.write("user-after/\n")
    with (project / "SPEC.md").open("a", encoding="utf-8") as handle:
        handle.write("\nOur own words.\n")
    extra = {
        "lore/ADRs/ADR-0001-ours.md": "# ADR-0001 ours\n",
        "missions/feature-x/SPEC.md": "# Mission\n",
        ".agents/skills/team-own/SKILL.md": "---\nname: team-own\n---\n",
        ".claude/commands/team-own.md": "# ours\n",
    }
    for rel, text in extra.items():
        (project / rel).parent.mkdir(parents=True, exist_ok=True)
        (project / rel).write_text(text, encoding="utf-8")
    edited = ".agents/rules/python.md"
    assert edited in manifest["files"]
    with (project / edited).open("a", encoding="utf-8") as handle:
        handle.write("\nTeam rule.\n")

    def files_under(top: str) -> list[str]:
        return [f"{top}/{rel}" for rel in snapshot(project / top) if not rel.endswith("/")]

    keep = [rel for rel in user_files if rel != ".gitignore"] + list(extra)
    keep += ["SPEC.md", "project.yaml", "workspace.yaml", edited]
    keep += files_under("lore") + files_under("missions")
    before = {rel: sha256(project / rel) for rel in sorted(set(keep))}
    gitignore = without_block(project / ".gitignore", "gitignore")
    attributes = attributes_file(project)
    assert b"# BEGIN HACF MANAGED BLOCK: merge-drivers" in attributes.read_bytes()
    hooks = manifest["hooks"]["git"]
    assert hooks

    run = run_uninstaller(scratch, upstream.url, project, "-y")
    assert run.returncode == 0, run.output
    assert f"upstream {upstream.url} @ {upstream.sha}" in run.output

    assert {rel: sha256(project / rel) for rel in before} == before
    assert (project / ".gitignore").read_bytes() == gitignore
    assert b"user-after/" in gitignore and b"HACF MANAGED BLOCK" not in gitignore
    assert not (project / MANIFEST_REL).exists()
    for rel in manifest["files"]:
        if rel != edited:
            assert not (project / rel).exists(), rel
    assert f"kept (changed since HACF installed it): {edited}" in run.output
    assert not attributes.exists()
    assert (project / ".gitattributes").read_bytes() == LFS.encode()
    for entry in hooks.values():
        assert not Path(project, entry["path"]).exists()
    # .claude/settings.json: every rule and hook HACF recorded is gone.
    settings = json.loads((project / ".claude/settings.json").read_text(encoding="utf-8"))
    text = json.dumps(settings)
    for rules in manifest["settings_rules"].values():
        for rule in rules:
            assert json.dumps(rule) not in text
    assert "hyper_gate_hook.py" not in text
    assert list((project / ".agents/.backup").rglob("settings.json"))
    # Instruction files and ignore files lost only the HACF block.
    assert b"HACF MANAGED BLOCK" not in (project / "CLAUDE.md").read_bytes()
    assert b"HACF MANAGED BLOCK" not in (project / ".cursorignore").read_bytes()
    assert (project / ".agents/skills/team-own/SKILL.md").is_file()


def test_uninstall_dry_run_changes_nothing(
    project: Path, run_install, scratch: Path, upstream
) -> None:
    """Task 21: --dry-run lists the deletions and block removals and changes nothing."""
    run = run_install(project, "-y", "--ides=claude")
    assert run.returncode == 0, run.output
    before = snapshot(project)
    run = run_uninstaller(scratch, upstream.url, project, "--dry-run")
    assert run.returncode == 0, run.output
    assert snapshot(project) == before
    assert "[DRY-RUN] would delete .agents/scripts/hyper_blocks.py" in run.output
    assert "[DRY-RUN] would remove block 'gitignore' from .gitignore" in run.output
    assert "[DRY-RUN] would remove" in run.output and ".claude/settings.json" in run.output
    assert f"[DRY-RUN] would delete {MANIFEST_REL} last" in run.output


def test_uninstall_without_terminal_or_yes_changes_nothing(
    project: Path, run_install, scratch: Path, upstream
) -> None:
    """No -y and no terminal: the uninstaller asks nobody and changes nothing."""
    assert run_install(project, "-y", "--ides=claude").returncode == 0
    before = snapshot(project)
    run = run_uninstaller(scratch, upstream.url, project)
    assert run.returncode != 0
    assert "no terminal to ask on" in run.output
    assert snapshot(project) == before


def test_uninstall_refuses_never_copy_manifest_entries(
    project: Path, run_install, scratch: Path, upstream
) -> None:
    """Task 13: a manifest entry naming a NEVER_COPY path or a user root file is refused."""
    assert run_install(project, "-y", "--ides=claude").returncode == 0
    path = project / MANIFEST_REL
    manifest = json.loads(path.read_text(encoding="utf-8"))
    targets = {"SPEC.md": None, "LICENSE": "MIT\n", "lore/ours.md": "x\n", "tests/keep.txt": "x\n"}
    for rel, text in targets.items():
        if text is not None:
            (project / rel).parent.mkdir(parents=True, exist_ok=True)
            (project / rel).write_text(text, encoding="utf-8")
        manifest["files"][rel] = {"sha256": sha256(project / rel), "owner": "hacf"}
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    before = {rel: sha256(project / rel) for rel in targets}
    run = run_uninstaller(scratch, upstream.url, project, "-y")
    assert run.returncode == 0, run.output
    assert {rel: sha256(project / rel) for rel in targets} == before
    for rel in targets:
        assert f"refused (NEVER_COPY or a user root file; never deleted): {rel}" in run.output


# ---------------------------------------------------------------------------
# Tests 2 and 3: without a manifest (v0.5.x)
# ---------------------------------------------------------------------------


def test_no_manifest_legacy_hash_fallback(v059_project, scratch: Path, upstream) -> None:
    """Test 2: only files whose sha a v0.5.x release shipped are deleted; an edited one is kept."""
    project = v059_project("default")
    shipped = legacy_list()
    unmodified = ".agents/scripts/hypergraph_updater.py"
    edited = ".agents/skills/hyper-execute/SKILL.md"
    assert sha256(project / unmodified) in shipped[unmodified]
    assert sha256(project / edited) in shipped[edited]
    with (project / edited).open("a", encoding="utf-8") as handle:
        handle.write("\nOur team's extra step.\n")
    assert not (project / MANIFEST_REL).exists()
    user = ["pyproject.toml", "uv.lock", "LICENSE", ".gitattributes", "AGENTS.md"]
    user += [f"spec/{rel}" for rel in snapshot(project / "spec") if not rel.endswith("/")]
    before = {rel: sha256(project / rel) for rel in user}

    run = run_uninstaller(scratch, upstream.url, project, "-y")
    assert run.returncode == 0, run.output
    assert not (project / unmodified).exists()
    assert (project / edited).is_file()
    assert f"kept (changed since HACF installed it): {edited}" in run.output
    assert {rel: sha256(project / rel) for rel in before} == before
    # AGENTS.md carries an OpenRig block, so its sha is unknown: kept. CLAUDE.md is the template.
    assert (project / "AGENTS.md").is_file()
    assert not (project / "CLAUDE.md").exists()


def test_legacy_hashes_read_from_clone(v059_project, scratch: Path, upstream) -> None:
    """Test 3: a project copy of legacy_file_hashes.json listing a user file is ignored (R24)."""
    project = v059_project("default")
    user_file = project / ".agents/rules/team.md"
    user_file.write_text("# Our team rules\n", encoding="utf-8")
    tampered = {"schema": "hacf.legacy-hashes/v1", ".agents/rules/team.md": [sha256(user_file)]}
    (project / LEGACY_REL).write_text(json.dumps(tampered), encoding="utf-8")
    shipped = ".agents/scripts/hypergraph_updater.py"
    assert (project / shipped).is_file()

    run = run_uninstaller(scratch, upstream.url, project, "-y")
    assert run.returncode == 0, run.output
    assert user_file.read_text(encoding="utf-8") == "# Our team rules\n"
    assert not (project / shipped).exists()  # the clone's list was used


# ---------------------------------------------------------------------------
# Test 4: code graph
# ---------------------------------------------------------------------------


def _codegraph_project(project: Path, run_install, log: Path) -> None:
    assert run_install(project, "-y", "--ides=claude").returncode == 0
    state = project / ".agents/state/codegraph/project.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps({"project_name": "demo-project"}), encoding="utf-8")
    stub = project / ".agents/scripts/hyper_codegraph.py"
    stub.write_text(
        "import sys\n"
        f"with open({str(log)!r}, 'a', encoding='utf-8') as handle:\n"
        "    handle.write(' '.join(sys.argv[1:]) + '\\n')\n",
        encoding="utf-8",
    )


def test_uninstall_offers_codegraph_delete(
    project: Path, run_install, scratch: Path, upstream, tmp_path: Path
) -> None:
    """Test 4: on a terminal, y runs `hyper_codegraph.py delete`; -y alone never does (R24)."""
    log = tmp_path / "codegraph.log"
    _codegraph_project(project, run_install, log)
    env = install_env(scratch, upstream.url)
    code, text = run_in_pty(["bash", str(UNINSTALLER)], project, env, lambda _output: b"y\n")
    assert code == 0, text
    assert "Delete the codebase-memory-mcp index of project 'demo-project'" in text
    assert log.read_text(encoding="utf-8").split() == ["delete"]

    other = tmp_path / "project-yes"
    other.mkdir()
    git(["init", "-q", "-b", "main"], other, install_env(scratch, upstream.url))
    log2 = tmp_path / "codegraph-yes.log"
    _codegraph_project(other, run_install, log2)
    run = run_uninstaller(scratch, upstream.url, other, "-y")
    assert run.returncode == 0, run.output
    assert "declined (-y never consents" in run.output
    assert not log2.exists()
    # The flag is the consent: a second run (now legacy mode) with it calls the stub.
    run = run_uninstaller(scratch, upstream.url, other, "-y", "--delete-code-graph")
    assert run.returncode == 0, run.output
    assert log2.read_text(encoding="utf-8").split() == ["delete"]


# ---------------------------------------------------------------------------
# Test 5: upstream overrides (R12)
# ---------------------------------------------------------------------------


def test_uninstall_repo_url_env_ignored_without_hacf_test(
    project: Path, scratch: Path, upstream
) -> None:
    """Test 5: HACF_REPO_URL alone is ignored; --repo-url file:// needs HACF_TEST=1."""
    (project / ".agents").mkdir()
    (project / ".agents/keep.txt").write_text("ours\n", encoding="utf-8")
    before = snapshot(project)
    run = run_uninstaller(
        scratch, upstream.url, project, "-y", env={"HACF_TEST": None, "GIT_ALLOW_PROTOCOL": "file"}
    )
    assert run.returncode != 0
    assert f"Fetching the framework: {DEFAULT_URL}" in run.output
    assert upstream.url not in run.output
    assert snapshot(project) == before

    run = run_uninstaller(
        scratch, upstream.url, project, "-y", "--repo-url", upstream.url, env={"HACF_TEST": None}
    )
    assert run.returncode != 0
    assert "only https:// URLs are accepted" in run.output
    assert snapshot(project) == before

    run = run_uninstaller(
        scratch,
        upstream.url,
        project,
        "-y",
        "--repo-url",
        upstream.url,
        env={"HACF_REPO_URL": "file:///nonexistent/hacf.git"},
    )
    assert run.returncode == 0, run.output
    assert f"upstream {upstream.url} @ {upstream.sha}" in run.output
    assert snapshot(project) == before


# ---------------------------------------------------------------------------
# S12b Task 36: the installer's replacement path, then the replacement runs
# ---------------------------------------------------------------------------


def test_saved_uninstaller_replaced_then_runs(v059_project, scratch: Path, upstream) -> None:
    """A saved v0.5.x uninstaller replaced by consent (S12b Task 36) works as the v0.6.0 one."""
    project = v059_project("default")
    old = sha256(project / "HACF-uninstall.sh")
    env = install_env(scratch, upstream.url)

    def answer(output: str) -> bytes:
        tail = output[-600:]
        return b"y\n" if "Replace ./HACF-uninstall.sh" in tail else b"\n"

    code, text = run_in_pty(["bash", str(INSTALLER), "--ides=claude"], project, env, answer)
    assert code == 0, text
    assert "replaced with the v0.6.0 uninstaller" in text
    assert sha256(project / "HACF-uninstall.sh") == sha256(UNINSTALLER)
    assert list((project / ".agents/.backup").rglob("HACF-uninstall.sh"))
    assert old != sha256(UNINSTALLER)

    before = {rel: sha256(project / rel) for rel in ("pyproject.toml", ".gitattributes", "LICENSE")}
    run = run_uninstaller(
        scratch, upstream.url, project, "-y", script=project / "HACF-uninstall.sh"
    )
    assert run.returncode == 0, run.output
    assert not (project / MANIFEST_REL).exists()
    assert not (project / ".agents/scripts/hyper_blocks.py").exists()
    assert {rel: sha256(project / rel) for rel in before} == before


# ---------------------------------------------------------------------------
# Tests 6 and 7: static checks
# ---------------------------------------------------------------------------


def test_bash_n() -> None:
    """Test 6: `bash -n HACF-uninstall.sh` exits 0."""
    proc = subprocess.run(["bash", "-n", str(UNINSTALLER)], capture_output=True, check=False)
    assert proc.returncode == 0, proc.stderr


def test_uninstaller_static_rules() -> None:
    """No bare `read -p`, no sha256sum, no shell=True/os.system/eval/exec in the inline Python."""
    text = UNINSTALLER.read_text(encoding="utf-8")
    assert "read -p" not in text and "read -r -p" not in text
    assert "sha256sum" not in text and "shasum" not in text
    from test_static_safety import scan_source  # dev/tests is on sys.path (rootdir conftest)

    inline = text[text.index("<<'PY'\n") + len("<<'PY'\n") : text.index("\nPY\n")]
    assert scan_source(inline, "HACF-uninstall.sh:ux") == []
    assert scan_source(GENERATOR.read_text(encoding="utf-8"), GENERATOR.name) == []


def test_generator_check(tmp_path: Path) -> None:
    """Test 7: --check exits 0 on the committed JSON and 1 after one sha is changed."""
    proc = subprocess.run(
        [sys.executable, str(GENERATOR), "--check"], capture_output=True, text=True, check=False
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    copy = tmp_path / "legacy_file_hashes.json"
    shutil.copy2(REPO_ROOT / LEGACY_REL, copy)
    data = json.loads(copy.read_text(encoding="utf-8"))
    data[".agentignore"][0] = "0" * 64
    copy.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(GENERATOR), "--check", "--output", str(copy)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 1
    assert "changed: .agentignore" in proc.stdout


def test_generator_output_rules() -> None:
    """The list holds no NEVER_COPY path and keys the instruction files by destination name."""
    data = legacy_list()
    assert data["schema"] == "hacf.legacy-hashes/v1"
    assert data["releases"] == ["626a28e", "cd8ae6a", "b3b1634", "329e8f7", "0b19f16", "984ca46"]
    paths = [key for key in data if key not in ("schema", "releases")]
    for rel in paths:
        assert not rel.startswith(("tests/", "spec/", ".agents/memory/", "lore/", "missions/"))
        assert not rel.startswith(".claude/settings")
    roots = sorted(rel for rel in paths if "/" not in rel)
    assert roots == [".agentignore", "AGENTS.md", "CLAUDE.md", "GEMINI.md"]
    known, _ = hyper_blocks.known_template_shas("AGENTS.md")
    assert set(data["AGENTS.md"]) == known
