"""Tests for .agents/scripts/commit-msg and the two-line footer (v064-process OPR.99.0.9.1, .2).

Each hook test writes a commit message to a temp file and runs the hook on it the way git does
(`bash .agents/scripts/commit-msg <file>`), from a temp directory with OPENRIG_SESSION_NAME unset
unless a test sets it. The append tests (OPR.99.0.9.2) run it inside a temp git repo holding its own
manifest or `HACF-install.sh`. The footer tests read the shipped files.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from conftest import REPO_ROOT

HOOK = REPO_ROOT / ".agents" / "scripts" / "commit-msg"
L1 = "Agentic-Assistant: OpenRig (Claude Code harness) · model <model-id>"
L2 = "Agentic-Framework: HACF v<version> (<mode>)"
FOOTER_FILES = [
    REPO_ROOT / "openrig" / "hacf" / "CULTURE.md",
    REPO_ROOT / "openrig" / "hacf" / "agents" / "hacf" / "guidance" / "execute.md",
    REPO_ROOT / "openrig" / "hacf" / "agents" / "hacf" / "guidance" / "audit.md",
    REPO_ROOT / "openrig" / "hacf" / "agents" / "hacf" / "guidance" / "scribe.md",
]
PUBLISH = [
    REPO_ROOT / ".agents" / "skills" / "hyper-publish" / "examples" / "example_commit_messages.md",
    REPO_ROOT / ".agents" / "skills" / "hyper-publish" / "resources" / "commit_template.md",
]

AGENT = (
    "Add the widget\n\nWhy it exists.\n\n"
    "Claude-Session: https://claude.ai/code/session_abc\n"
    "Agentic-Assistant: OpenRig (Claude Code harness) · model claude-opus-5-5\n"
    "Agentic-Framework: HACF v0.6.3 (OpenRig)\n"
)
HUMAN = "Fix the parser\n\nDetails.\n\nClaude-Session: https://claude.ai/code/session_xyz\n"


def run_hook(
    *args: str, cwd: Path | None = None, session: str | None = None
) -> subprocess.CompletedProcess:
    """Run the hook with these arguments.

    Without `cwd` it runs from the message file's directory with git's search stopped above it, so
    no repository (and no version) is found. OPENRIG_SESSION_NAME is `session`, else unset; no GIT_*
    variable of the caller leaks in.
    """
    env = run_env()
    env.pop("OPENRIG_SESSION_NAME", None)
    if session is not None:
        env["OPENRIG_SESSION_NAME"] = session
    if cwd is None and args and Path(args[0]).parent.is_dir():
        cwd = Path(args[0]).parent
        env["GIT_CEILING_DIRECTORIES"] = str(cwd.parent)
    return subprocess.run(
        ["bash", str(HOOK), *args], capture_output=True, text=True, timeout=30, cwd=cwd, env=env
    )


def message(tmp_path, text: str):
    """Write a commit message file."""
    path = tmp_path / "COMMIT_EDITMSG"
    path.write_bytes(text.encode("utf-8"))
    return path


def test_agent_commit_drops_claude_session(tmp_path) -> None:
    """An agent message loses its Claude-Session line; every other byte is kept."""
    path = message(tmp_path, AGENT)
    assert run_hook(str(path)).returncode == 0
    want = AGENT.replace("Claude-Session: https://claude.ai/code/session_abc\n", "")
    assert path.read_bytes() == want.encode("utf-8")
    assert "Agentic Coding Assisted" not in path.read_text()
    assert not list(tmp_path.glob(".commit-msg.*"))


def test_human_commit_rewrite_as_before(tmp_path) -> None:
    """Without the standard footer, Claude-Session is rewritten as before."""
    path = message(tmp_path, HUMAN)
    assert run_hook(str(path)).returncode == 0
    want = HUMAN.replace(
        "Claude-Session: https://claude.ai/code/session_xyz", "Agentic Coding Assisted: Claude Code"
    )
    assert path.read_text() == want


def test_unchanged_message_is_not_rewritten(tmp_path) -> None:
    """No Claude-Session line: bytes and mtime unchanged."""
    path = message(tmp_path, "Plain message\n\nAgentic-Assistant: x\n")
    os.utime(path, (1_000_000_000, 1_000_000_000))
    before = path.read_bytes(), path.stat().st_mtime_ns
    assert run_hook(str(path)).returncode == 0
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    assert not list(tmp_path.glob(".commit-msg.*"))


@pytest.mark.parametrize("args", [[], ["/nonexistent/COMMIT_EDITMSG"]])
def test_missing_file_exits_0(args: list[str]) -> None:
    """No file argument, or a missing file: exit 0, nothing written."""
    assert run_hook(*args).returncode == 0


def test_hook_is_portable() -> None:
    """No GNU-only sed -i or mv -T in the hook source."""
    code = HOOK.read_text()
    lines = [ln for ln in code.splitlines() if not ln.lstrip().startswith("#")]
    body = "\n".join(lines)
    assert "sed -i" not in body and "mv -T" not in body
    assert os.access(HOOK, os.X_OK)


@pytest.mark.parametrize("path", FOOTER_FILES, ids=lambda p: p.name)
def test_footer_files_show_both_lines(path) -> None:
    """CULTURE.md and the three guidance files show the two-line footer block."""
    text = path.read_text(encoding="utf-8")
    assert f"{L1}\n{L2}\n" in text
    assert "`.agents/hacf-manifest.json`" in text and "`manual`" in text


@pytest.mark.parametrize("path", PUBLISH, ids=lambda p: p.name)
def test_hyper_publish_examples(path) -> None:
    """hyper-publish shows the two lines and no Claude co-author or Claude-Session line."""
    text = path.read_text(encoding="utf-8")
    assert "Co-Authored-By: Claude" not in text and "Co-authored-by: Claude" not in text
    assert "Claude-Session:" not in text
    assert "Agentic-Assistant: " in text and "Agentic-Framework: HACF v" in text


# --- OPR.99.0.9.2: the hook appends the Agentic-Framework line ---------------------------------

ASSIST = "Agentic-Assistant: OpenRig (Claude Code harness) · model claude-opus-5-5"
AGENT_NO_FW = f"Add the widget\n\nWhy it exists.\n\n{ASSIST}\n"


def repo(tmp_path, *, manifest: str | None = None, install: str | None = None) -> Path:
    """A temp git repo with an optional raw manifest text and HACF-install.sh text."""
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True, timeout=30, env=run_env())
    if manifest is not None:
        (root / ".agents").mkdir()
        (root / ".agents" / "hacf-manifest.json").write_text(manifest, encoding="utf-8")
    if install is not None:
        (root / "HACF-install.sh").write_text(install, encoding="utf-8")
    return root


def run_env() -> dict[str, str]:
    """The caller's environment without GIT_* variables."""
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def hook_in(root: Path, text: str, session: str | None = None) -> tuple[int, bytes]:
    """Run the hook on `text` inside `root`; return its exit code and the message bytes after."""
    path = root / ".git" / "COMMIT_EDITMSG"
    path.write_bytes(text.encode("utf-8"))
    rc = run_hook(str(path), cwd=root, session=session).returncode
    assert not list(path.parent.glob(".commit-msg.*"))
    return rc, path.read_bytes()


MANIFEST_064 = json.dumps({"version": "0.6.4"})
INSTALL_063 = '#!/usr/bin/env bash\nX=1\nHACF_VERSION="0.6.3"\nHACF_VERSION="9.9.9"\n'


def test_append_openrig_mode_from_manifest(tmp_path) -> None:
    """Agent message, manifest 0.6.4, OPENRIG_SESSION_NAME set: the two footer lines end it."""
    root = repo(tmp_path, manifest=MANIFEST_064, install=INSTALL_063)
    rc, out = hook_in(root, AGENT_NO_FW, session="x")
    assert rc == 0
    assert out.decode().splitlines()[-2:] == [ASSIST, "Agentic-Framework: HACF v0.6.4 (OpenRig)"]
    assert out == (AGENT_NO_FW + "Agentic-Framework: HACF v0.6.4 (OpenRig)\n").encode("utf-8")


@pytest.mark.parametrize("session", [None, ""], ids=["unset", "empty"])
def test_append_manual_mode(tmp_path, session) -> None:
    """Without a (non-empty) OPENRIG_SESSION_NAME the mode is manual."""
    root = repo(tmp_path, manifest=MANIFEST_064)
    rc, out = hook_in(root, AGENT_NO_FW, session=session)
    assert rc == 0
    assert out.decode().splitlines()[-1] == "Agentic-Framework: HACF v0.6.4 (manual)"


def test_append_version_from_install_script(tmp_path) -> None:
    """No manifest: the first HACF_VERSION line of HACF-install.sh gives the version."""
    root = repo(tmp_path, install=INSTALL_063)
    rc, out = hook_in(root, AGENT_NO_FW, session="x")
    assert rc == 0
    assert out.decode().splitlines()[-1] == "Agentic-Framework: HACF v0.6.3 (OpenRig)"


@pytest.mark.parametrize(
    "manifest",
    ["{not json", "[]", json.dumps({"version": ""}), json.dumps({"version": 64}), json.dumps({})],
    ids=["malformed", "list", "empty", "number", "absent"],
)
def test_bad_manifest_appends_nothing(tmp_path, manifest) -> None:
    """A malformed or versionless manifest and no install script: no line, exit 0."""
    root = repo(tmp_path, manifest=manifest)
    assert hook_in(root, AGENT_NO_FW, session="x") == (0, AGENT_NO_FW.encode("utf-8"))


@pytest.mark.parametrize(
    "install",
    [None, 'HACF_VERSION="latest"\n', "HACF_VERSION=0.6.3\n"],
    ids=["none", "word", "unquoted"],
)
def test_unknown_version_appends_nothing(tmp_path, install) -> None:
    """Neither file gives a version: no line, no guess, exit 0."""
    root = repo(tmp_path, install=install)
    assert hook_in(root, AGENT_NO_FW, session="x") == (0, AGENT_NO_FW.encode("utf-8"))


def test_existing_framework_line_unchanged(tmp_path) -> None:
    """A message already holding Agentic-Framework: keeps its bytes."""
    root = repo(tmp_path, manifest=MANIFEST_064)
    text = AGENT_NO_FW + "Agentic-Framework: HACF v0.6.1 (manual)\n"
    assert hook_in(root, text, session="x") == (0, text.encode("utf-8"))


HUMANS = [
    "Fix the parser\n\nDetails.\n",
    "Fix the parser\n\nAgentic-Assistant:no-space\n",
    "Fix the parser\n\n# Agentic-Assistant: commented\n",
    "Subject only",
]


@pytest.mark.parametrize("text", HUMANS, ids=["plain", "no-space", "comment", "no-newline"])
@pytest.mark.parametrize("session", [None, "x"], ids=["manual", "openrig"])
@pytest.mark.parametrize("source", ["manifest", "install", "neither"])
def test_human_message_unchanged(tmp_path, text, session, source) -> None:
    """No `Agentic-Assistant: ` line: byte-identical in every version and mode case."""
    root = repo(
        tmp_path,
        manifest=MANIFEST_064 if source == "manifest" else None,
        install=INSTALL_063 if source == "install" else None,
    )
    assert hook_in(root, text, session=session) == (0, text.encode("utf-8"))


def test_trailers_parse_in_order(tmp_path) -> None:
    """HACF trailers before the footer: git parses all four trailers, in order."""
    root = repo(tmp_path, manifest=MANIFEST_064)
    text = (
        "v064-process OPR.99.0.9.2: Subject\n\nBody line.\n\n"
        f"HACF-Slice: OPR.99.0.9.2\nHACF-Repin: abc123\n{ASSIST}\n"
    )
    rc, out = hook_in(root, text, session="x")
    assert rc == 0
    parsed = subprocess.run(
        ["git", "interpret-trailers", "--parse"],
        input=out.decode("utf-8"),
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
        cwd=root,
        env=run_env(),
    ).stdout
    keys = [ln.split(":", 1)[0] for ln in parsed.splitlines()]
    assert keys == ["HACF-Slice", "HACF-Repin", "Agentic-Assistant", "Agentic-Framework"]
    assert parsed.splitlines()[-1] == "Agentic-Framework: HACF v0.6.4 (OpenRig)"


def test_claude_session_dropped_and_framework_added(tmp_path) -> None:
    """Slice 01's deletion still runs, and the framework line is added."""
    root = repo(tmp_path, install=INSTALL_063)
    text = f"Add the widget\n\nClaude-Session: https://claude.ai/code/session_abc\n{ASSIST}\n"
    rc, out = hook_in(root, text, session="x")
    assert rc == 0
    assert out.decode() == f"Add the widget\n\n{ASSIST}\nAgentic-Framework: HACF v0.6.3 (OpenRig)\n"


def test_text_after_assistant_line_kept_after(tmp_path) -> None:
    """The line goes right after the last Agentic-Assistant line; what followed stays after it."""
    root = repo(tmp_path, manifest=MANIFEST_064)
    text = f"S\n\n{ASSIST}\n# Please enter the commit message\n#\n"
    rc, out = hook_in(root, text)
    assert rc == 0
    fw = "Agentic-Framework: HACF v0.6.4 (manual)"
    assert out.decode() == f"S\n\n{ASSIST}\n{fw}\n# Please enter the commit message\n#\n"
