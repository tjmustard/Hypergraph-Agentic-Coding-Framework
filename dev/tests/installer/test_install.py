"""Tests for the consent-first, merge-not-clobber, manifest-driven HACF-install.sh (S12b).

Each test runs the installer in a temp project against a temp upstream built from the working
tree (see conftest.py); none writes inside the HACF checkout.
"""

from __future__ import annotations

import json
import os
import pty
import re
import select
import signal
import subprocess
import sys
import time
from pathlib import Path

import yaml

from .conftest import DEFAULT_URL, INSTALLER, REPO_ROOT, git, git_env, install_env, sha256, snapshot

sys.path.insert(0, str(REPO_ROOT / ".agents" / "scripts"))
import hyper_blocks  # noqa: E402

CBM_ENTRY = {"type": "stdio", "command": "uvx", "args": ["codebase-memory-mcp@0.12.1"]}
FOREIGN_SERVER = {"command": "foreign-mcp", "args": ["--serve"]}
LFS = "*.psd filter=lfs diff=lfs merge=lfs -text\n*.zip filter=lfs diff=lfs merge=lfs -text\n"


def report_lines(output: str) -> list[str]:
    """Return the install report's lines (between its header and the banner)."""
    start = output.index("📝  Install report")
    end = output.index("╔", start)
    return output[start:end].splitlines()[1:]


def openrig_block(data: bytes) -> bytes:
    """Return the OpenRig managed block of a file, markers included."""
    match = re.search(
        rb"<!-- BEGIN OpenRig MANAGED BLOCK: role -->.*?<!-- END OpenRig MANAGED BLOCK: role -->\n",
        data,
        re.S,
    )
    assert match is not None
    return match.group(0)


def yaml_shim(tmp_path: Path, module: str) -> Path:
    """Return a bin dir whose python3 sees `module` as the yaml package (Tests 2 and 3)."""
    lib = tmp_path / "yaml-shim-lib"
    (lib / "yaml").mkdir(parents=True)
    (lib / "yaml" / "__init__.py").write_text(module, encoding="utf-8")
    bin_dir = tmp_path / "yaml-shim-bin"
    bin_dir.mkdir()
    shim = bin_dir / "python3"
    shim.write_text(
        f'#!/bin/sh\nPYTHONPATH="{lib}" exec "{sys.executable}" "$@"\n', encoding="utf-8"
    )
    shim.chmod(0o755)
    return bin_dir


# ---------------------------------------------------------------------------
# Test 1, Test 15 and the static checks
# ---------------------------------------------------------------------------


def test_fresh_install_noninteractive(project: Path, run_install) -> None:
    """-y with failing download shims: exit 0, zero downloads, only claude and cursor files."""
    run = run_install(project, "-y", "--ides=claude,cursor")
    assert run.returncode == 0, run.output
    assert run.shim_calls == []
    assert (project / ".claude" / "commands").is_dir()
    assert (project / "CLAUDE.md").is_file()
    assert (project / ".cursor").is_dir()
    assert (project / ".cursorignore").is_file()
    for absent in (
        ".windsurf", ".clinerules", ".roo", "GEMINI.md",
        ".rooignore", ".codeiumignore", ".geminiignore", ".mcp.json",
        ".claude/settings.local.json",
    ):  # fmt: skip
        assert not (project / absent).exists(), absent
    # K7 (R13): any IDE selection also installs AGENTS.md, which CLAUDE.md points at.
    assert b"<!-- BEGIN HACF MANAGED BLOCK: hacf -->" in (project / "AGENTS.md").read_bytes()
    settings = json.loads((project / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert "statusLine" not in settings
    hacf_yml = yaml.safe_load((project / ".agents/local/hacf.yml").read_text(encoding="utf-8"))
    assert hacf_yml["code_graph"]["backend"] == "none"
    assert "fresh install" in run.output


def _settings_backups(project: Path) -> list[Path]:
    root = project / ".agents" / ".backup"
    return sorted(root.rglob("settings.json")) if root.exists() else []


def test_settings_backup_exactly_when_needed(project: Path, run_install) -> None:
    """KI-6 must never lose the user's only settings backup (a, b, c, h of OPR.99.0.11.9)."""
    # (a) a fresh install writes settings.json up to three times and backs up nothing.
    run = run_install(project, "-y", "--ides=claude")
    assert run.returncode == 0, run.output
    assert _settings_backups(project) == []

    # (h) a later run carries no sha over: after a user edit that forces a write, it backs up first.
    path = project / ".claude" / "settings.json"
    edited = json.loads(path.read_text(encoding="utf-8"))
    edited["permissions"]["allow"].remove(edited["permissions"]["allow"][0])
    edited["env"] = {"MINE": "1"}
    path.write_text(json.dumps(edited, indent=2) + "\n", encoding="utf-8")
    before = path.read_bytes()
    run = run_install(project, "-y", "--ides=claude")
    assert run.returncode == 0, run.output
    backups = _settings_backups(project)
    assert len(backups) == 1 and backups[0].read_bytes() == before


def test_settings_backup_users_file_with_statusline(tmp_path: Path, run_install) -> None:
    """(b, c) A pre-existing user file, with and without --statusline, is backed up exactly once."""
    originals = {}
    for name, extra in (("plain", ()), ("status", ("--statusline",))):
        proj = tmp_path / name
        proj.mkdir()
        git(["init", "-q", "-b", "main"], proj, git_env(tmp_path / "scratch"))
        original = json.dumps({"env": {"MY_VAR": "1"}, "model": "sonnet"}).encode()
        (proj / ".claude").mkdir()
        (proj / ".claude" / "settings.json").write_bytes(original)
        run = run_install(proj, "-y", "--ides=claude", *extra)
        assert run.returncode == 0, run.output
        originals[name] = original
        backups = _settings_backups(proj)
        assert len(backups) == 1 and backups[0].read_bytes() == original, name
        merged = json.loads((proj / ".claude" / "settings.json").read_text(encoding="utf-8"))
        assert merged["model"] == "sonnet" and ("statusLine" in merged) == bool(extra)


def test_bash_n() -> None:
    """Test 15: `bash -n HACF-install.sh` exits 0."""
    proc = subprocess.run(["bash", "-n", str(INSTALLER)], capture_output=True, check=False)
    assert proc.returncode == 0, proc.stderr


def test_installer_static_rules() -> None:
    """No bare read -p, no sha256sum, no eval; the platform guard runs before any bash-4 code."""
    text = INSTALLER.read_text(encoding="utf-8")
    assert not re.search(r"\bread\b[^\n]*\s-p\b", text)
    assert "sha256sum" not in text
    assert not re.search(r"^\s*eval\b", text, re.M)
    for forbidden in ("shell=True", "os.system", "exec(", "eval("):
        assert forbidden not in text, forbidden
    lines = text.splitlines()
    assert lines[0] == "#!/usr/bin/env bash"
    first_command = next(line for line in lines[1:] if line.strip() and not line.startswith("#"))
    assert first_command.startswith("if [ ") and "HACF_TEST" in first_command
    assert text.index("BASH_VERSINFO") < text.index("declare -A")
    assert text.index("brew install bash") < text.index("set -euo pipefail")


def test_unknown_flag_values_write_nothing(project: Path, run_install) -> None:
    """Task 6: an unknown --code-graph or --firewall value exits with usage and writes nothing."""
    before = snapshot(project)
    for arg in ("--code-graph=sourcegraph", "--firewall=strict", "--no-such-flag"):
        run = run_install(project, "-y", arg)
        assert run.returncode == 2, run.output
        assert "Usage:" in run.output
    assert snapshot(project) == before


def test_dry_run_writes_nothing(project: Path, run_install) -> None:
    """Task 38: --dry-run prints the writes, merges and downloads and writes nothing."""
    before = snapshot(project)
    run = run_install(
        project, "-y", "--dry-run", "--ides=claude,cursor", "--code-graph=codebase-memory-mcp"
    )
    assert run.returncode == 0, run.output
    assert snapshot(project) == before
    assert run.shim_calls == []
    for expected in ("would install .agents/scripts/", "merge-settings", "would merge", "Dry-run"):
        assert expected in run.output, expected


# ---------------------------------------------------------------------------
# Preflight: Tests 2-4
# ---------------------------------------------------------------------------


def test_missing_pyyaml_aborts_before_writes(tmp_path: Path, project: Path, run_install) -> None:
    """Test 2: a python3 that cannot import yaml stops with guidance and writes nothing."""
    bin_dir = yaml_shim(tmp_path, 'raise ImportError("PyYAML is not installed (test shim)")\n')
    before = snapshot(project)
    run = run_install(project, "-y", "--ides=claude", path_prefix=bin_dir)
    assert run.returncode != 0
    assert "PyYAML is not installed" in run.output
    assert "uv python install" in run.output and "Nothing was written" in run.output
    assert "--install-deps" in run.output
    assert run.shim_calls == []  # no pip without consent
    assert snapshot(project) == before


def test_old_pyyaml_aborts_before_writes(tmp_path: Path, project: Path, run_install) -> None:
    """Test 3: PyYAML 5.3.1 stops with a message naming 5.4 and the per-OS guidance."""
    bin_dir = yaml_shim(tmp_path, '__version__ = "5.3.1"\n')
    before = snapshot(project)
    run = run_install(project, "-y", "--ides=claude", path_prefix=bin_dir)
    assert run.returncode != 0
    assert "5.3.1" in run.output and "5.4" in run.output
    for guidance in ("uv python install", "brew install", "deadsnakes"):
        assert guidance in run.output, guidance
    assert snapshot(project) == before


def test_bash3_refused(project: Path, run_install) -> None:
    """Test 4: HACF_TEST_BASH_MAJOR=3 refuses with the Homebrew hint before any write."""
    before = snapshot(project)
    run = run_install(project, "-y", env={"HACF_TEST_BASH_MAJOR": "3"})
    assert run.returncode != 0
    assert "brew install bash" in run.output
    assert snapshot(project) == before


# ---------------------------------------------------------------------------
# Consent and the upstream: Tests 5-7
# ---------------------------------------------------------------------------


def test_consent_reads_dev_tty(project: Path, run_install) -> None:
    """Test 5: `bash -s < HACF-install.sh` without a terminal: every consent is NO."""
    run = run_install(project, "--code-graph=codebase-memory-mcp", script_on_stdin=True)
    assert run.returncode == 0, run.output
    assert run.shim_calls == []
    assert "declined (no terminal to ask on)" in run.output
    lines = report_lines(run.output)
    assert any(
        line.strip().startswith("declined: codebase-memory-mcp install") for line in lines
    ), lines
    hacf_yml = yaml.safe_load((project / ".agents/local/hacf.yml").read_text(encoding="utf-8"))
    assert hacf_yml["code_graph"]["backend"] == "codebase-memory-mcp"
    assert "install_method" not in hacf_yml["code_graph"]
    assert not (project / ".agents/state/codegraph/project.json").exists()


def test_repo_url_env_ignored_without_hacf_test(project: Path, run_install, upstream) -> None:
    """Test 6: HACF_REPO_URL alone is ignored; --repo-url file:// needs HACF_TEST=1."""
    before = snapshot(project)
    # Without HACF_TEST the env URL is ignored: the run announces the default GitHub URL. Git
    # may use only file://, so that clone fails offline and nothing is fetched.
    run = run_install(
        project, "-y", "--ides=claude", env={"HACF_TEST": None, "GIT_ALLOW_PROTOCOL": "file"}
    )
    assert run.returncode != 0
    assert f"Fetching framework: {DEFAULT_URL}" in run.output
    assert upstream.url not in run.output
    assert snapshot(project) == before

    run = run_install(project, "-y", "--repo-url", upstream.url, env={"HACF_TEST": None})
    assert run.returncode != 0
    assert "only https:// URLs are accepted" in run.output
    assert snapshot(project) == before

    run = run_install(
        project,
        "-y",
        "--ides=claude",
        "--repo-url",
        upstream.url,
        env={"HACF_REPO_URL": "file:///nonexistent/hacf.git"},
    )
    assert run.returncode == 0, run.output
    assert f"upstream {upstream.url} @ {upstream.sha}" in run.output


def test_consent_prompt_shows_url_and_commit(project: Path, scratch: Path, upstream) -> None:
    """Test 7: a consent prompt read from a pseudo-terminal shows the URL and the commit."""
    env = install_env(scratch, upstream.url)
    argv = ["bash", str(INSTALLER), "--ides=claude", "--code-graph=codebase-memory-mcp"]
    pid, fd = pty.fork()
    if pid == 0:  # the child: a new session whose controlling terminal is the pty
        try:
            os.chdir(project)
            os.execvpe("bash", argv, env)
        finally:
            os._exit(127)
    output = b""
    answered = False
    rig_answered = False
    deadline = time.monotonic() + 240
    try:
        while time.monotonic() < deadline:
            ready, _, _ = select.select([fd], [], [], 1.0)
            if not ready:
                continue
            try:
                chunk = os.read(fd, 65536)
            except OSError:  # EIO: the child closed the terminal
                break
            if not chunk:
                break
            output += chunk
            if not rig_answered and b"for a per-project team? [y/N]" in output:
                os.write(fd, b"n\n")  # slice 7 Task 4: the --rig question comes first
                rig_answered = True
            if not answered and b"Proceed? [y/N]" in output:
                os.write(fd, b"n\n")
                answered = True
    finally:
        done, status = os.waitpid(pid, os.WNOHANG)
        if done == 0:  # still running past the deadline: kill it so the test fails, never hangs
            try:
                os.killpg(pid, signal.SIGKILL)  # a pty.fork child leads its session: pgid == pid
            except ProcessLookupError:
                pass
            _, status = os.waitpid(pid, 0)
        os.close(fd)
    text = output.decode("utf-8", "replace")
    assert answered, text
    assert os.waitstatus_to_exitcode(status) == 0, text
    prompt = text[text.index("install codebase-memory-mcp") : text.index("Proceed? [y/N]")]
    assert upstream.url in prompt and upstream.sha in prompt
    assert "— declined" in text


# ---------------------------------------------------------------------------
# Mode detection and upgrades: Tests 8-11
# ---------------------------------------------------------------------------


def test_openrig_only_repo_is_fresh_install(v059_project, run_install) -> None:
    """Test 8: OpenRig-projected .agents/skills only -> fresh install; that skill is untouched."""
    project = v059_project("openrig-only-skills")
    skill = project / ".agents" / "skills" / "openrig-review"
    before = snapshot(skill)
    run = run_install(project, "-y", "--ides=claude")
    assert run.returncode == 0, run.output
    assert "running a fresh install" in run.output
    assert "UPGRADE" not in run.output
    assert snapshot(skill) == before


def test_upgrade_preserves(v059_project, run_install) -> None:
    """Test 9: a -y upgrade of the v0.5.9 fixture keeps every user byte and backs up edits."""
    local = (
        "openrig:\n  id_prefix: XYZ\nides: [claude, cursor]\n"
        "code_graph:\n  backend: none\n  base_branch: develop\n"
    )
    own_skill = "---\nname: team-own\ndescription: our own skill\n---\n\n# Ours\n"
    project = v059_project(
        "default",
        extras={".agents/local/hacf.yml": local, ".agents/skills/team-own/SKILL.md": own_skill},
    )
    python_rules = project / ".agents" / "rules" / "python.md"
    modified = python_rules.read_bytes() + b"\n- Team rule: type hints everywhere.\n"
    python_rules.write_bytes(modified)
    agents_before = (project / "AGENTS.md").read_bytes()
    user_files = ("LICENSE", "pyproject.toml", "uv.lock", ".gitattributes", "CLAUDE.local.md")
    (project / "CLAUDE.local.md").write_text("# my local notes\n", encoding="utf-8")
    user_shas = {rel: sha256(project / rel) for rel in user_files}
    local_sha = sha256(project / ".agents/local/hacf.yml")

    run = run_install(project, "-y")
    assert run.returncode == 0, run.output
    assert run.shim_calls == []
    assert "UPGRADE mode" in run.output

    assert openrig_block((project / "AGENTS.md").read_bytes()) == openrig_block(agents_before)
    settings = json.loads((project / ".claude/settings.json").read_text(encoding="utf-8"))
    assert settings["model"] == "sonnet"
    assert "Bash(make lint)" in settings["permissions"]["allow"]
    assert "Read(./secrets/**)" in settings["permissions"]["deny"]
    assert sha256(project / ".agents/local/hacf.yml") == local_sha
    assert (project / ".agents/skills/team-own/SKILL.md").read_text(encoding="utf-8") == own_skill
    for rel, sha in user_shas.items():
        assert sha256(project / rel) == sha, rel
    backups = list((project / ".agents" / ".backup").glob("*/.agents/rules/python.md"))
    assert [b.read_bytes() for b in backups] == [modified]
    assert python_rules.read_bytes() == (REPO_ROOT / ".agents/rules/python.md").read_bytes()
    lines = report_lines(run.output)
    assert any("the upgrade is complete" in line for line in lines), lines
    assert any("saved v0.5.x uninstaller" in line for line in lines), lines
    assert (project / ".agents" / "hacf-manifest.json").is_file()


def test_upgrade_customized_agents_md_sidecar(v059_project, run_install) -> None:
    """Test 10: an unknown-sha whole-file AGENTS.md is untouched; the block goes to a sidecar."""
    project = v059_project("custom-instructions")
    before = (project / "AGENTS.md").read_bytes()
    run = run_install(project, "-y")
    assert run.returncode == 0, run.output
    assert (project / "AGENTS.md").read_bytes() == before
    sidecar = project / "AGENTS.hacf-v0.6.0.md"
    assert sidecar.is_file()
    assert b"<!-- BEGIN HACF MANAGED BLOCK: hacf -->" in sidecar.read_bytes()
    lines = [line for line in report_lines(run.output) if line.strip().startswith("- AGENTS.md")]
    assert len(lines) == 1, report_lines(run.output)
    assert "AGENTS.hacf-v0.6.0.md" in lines[0]


def test_install_leaves_user_gitattributes(project: Path, run_install, tmp_path: Path) -> None:
    """Test 11: a user .gitattributes stays byte-identical; drivers go to info/attributes."""
    (project / ".gitattributes").write_text(LFS, encoding="utf-8")
    run = run_install(project, "-y", "--ides=claude")
    assert run.returncode == 0, run.output
    assert (project / ".gitattributes").read_text(encoding="utf-8") == LFS
    common = git(["rev-parse", "--git-common-dir"], project, git_env(tmp_path / "g")).strip()
    attributes = (project / common / "info" / "attributes").read_text(encoding="utf-8")
    assert "# BEGIN HACF MANAGED BLOCK: merge-drivers" in attributes
    assert "lore_hypergraph.yml merge=hacf-lore" in attributes
    found = [
        p for p in project.rglob(".gitattributes") if ".git" not in p.relative_to(project).parts
    ]
    assert found == [project / ".gitattributes"]


# ---------------------------------------------------------------------------
# Manifest and MCP: Tests 12-13
# ---------------------------------------------------------------------------


def test_manifest_records_installed_commit(project: Path, run_install, upstream) -> None:
    """Test 12: the manifest parses, names the upstream HEAD and matches every file on disk."""
    run = run_install(project, "-y", "--ides=claude")
    assert run.returncode == 0, run.output
    manifest = json.loads((project / ".agents/hacf-manifest.json").read_text(encoding="utf-8"))
    assert set(manifest) == {
        "version", "installed_commit", "installed_at", "files",
        "settings_rules", "hooks", "blocks", "mcp_entries", "source", "ides",
        "installer_sha256",  # the saved installer copy (C2, OPR.99.0.10.3)
    }  # fmt: skip
    assert manifest["installed_commit"] == upstream.sha
    assert manifest["installed_at"].endswith("+00:00")
    assert len(manifest["files"]) > 100
    for rel, entry in manifest["files"].items():
        assert sha256(project / rel) == entry["sha256"], rel
        assert entry["owner"] == "hacf"
    assert ".agents/scripts/hyper_scaffold.py" in manifest["files"]
    for hook in ("pre-commit", "commit-msg", "pre-push"):
        entry = manifest["hooks"]["git"][hook]
        assert sha256(project / entry["path"]) == entry["sha256"]
    assert "Bash(gh *)" in manifest["settings_rules"]["allow"]
    assert any("hyper_gate_hook.py" in c for c in manifest["hooks"]["PreToolUse"])
    template = (REPO_ROOT / ".agents/install-templates/CLAUDE.md").read_bytes()
    expected = hyper_blocks.body_sha256(hyper_blocks.body_lines(template))
    assert manifest["blocks"]["CLAUDE.md"]["hacf"] == expected
    assert manifest["mcp_entries"] == {}


def test_mcp_merge_three_ides(project: Path, run_install) -> None:
    """Test 13: the stdio entry is merged into the three selected files; others are kept."""
    targets = (".mcp.json", ".cursor/mcp.json", ".roo/mcp.json")
    for rel in targets:
        path = project / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"mcpServers": {"foreign": FOREIGN_SERVER}}), encoding="utf-8")
    run = run_install(project, "-y", "--code-graph=codebase-memory-mcp", "--ides=claude,cursor,roo")
    assert run.returncode == 0, run.output
    assert run.shim_calls == []
    for rel in targets:
        servers = json.loads((project / rel).read_text(encoding="utf-8"))["mcpServers"]
        assert servers["foreign"] == FOREIGN_SERVER
        assert servers["codebase-memory-mcp"] == CBM_ENTRY
    manifest = json.loads((project / ".agents/hacf-manifest.json").read_text(encoding="utf-8"))
    assert manifest["mcp_entries"] == {rel: {"codebase-memory-mcp": CBM_ENTRY} for rel in targets}
    assert any("uvx codebase-memory-mcp@0.12.1" in line for line in report_lines(run.output))


def test_mcp_windsurf_steps_and_invalid_json(project: Path, run_install) -> None:
    """Test 13 (cont.): windsurf gets printed steps; an invalid .mcp.json is left unchanged."""
    (project / ".mcp.json").write_text('{"mcpServers": {', encoding="utf-8")
    run = run_install(project, "-y", "--code-graph=codebase-memory-mcp", "--ides=claude,windsurf")
    assert run.returncode == 0, run.output
    assert (project / ".mcp.json").read_text(encoding="utf-8") == '{"mcpServers": {'
    assert "windsurf: add this MCP server by hand" in run.output
    assert not (project / ".cursor").exists() and not (project / ".roo").exists()
    assert not list((project / ".windsurf").rglob("*mcp*"))
    assert any(".mcp.json: invalid JSON" in line for line in report_lines(run.output))


def test_saved_uninstaller_detected_by_sha(v059_project, run_install) -> None:
    """Task 36: the saved v0.5.x HACF-uninstall.sh is recognized and never run or deleted."""
    project = v059_project("default")
    before = sha256(project / "HACF-uninstall.sh")
    run = run_install(project, "-y", "--ides=claude")
    assert run.returncode == 0, run.output
    assert "is a saved v0.5.x uninstaller" in run.output
    assert sha256(project / "HACF-uninstall.sh") == before
