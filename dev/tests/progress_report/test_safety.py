"""Offline, zero-token, read-only, private output, exit codes and the opt-in collection."""

from __future__ import annotations

import ast
import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from . import progress_fixture as fx

ALLOWED_IMPORTS = {
    "argparse",
    "dataclasses",
    "datetime",
    "fnmatch",
    "hashlib",
    "html",
    "io",
    "json",
    "os",
    "pathlib",
    "re",
    "shutil",
    "subprocess",
    "sys",
    "tarfile",
    "tempfile",
    "xml.etree.ElementTree",
    "tomllib",
}
FORBIDDEN_BINS = ("claude", "codex", "curl", "wget", "rig", "npm", "uv", "pip")
GIT_VERBS = {
    "rev-parse",
    "rev-list",
    "log",
    "show",
    "ls-tree",
    "diff",
    "grep",
    "for-each-ref",
    "cat-file",
    "archive",
    "hash-object",
}


def script(path: Path, body: str) -> None:
    """Write an executable Python script that runs under this interpreter."""
    path.write_text(f"#!{sys.executable}\n{body}", encoding="utf-8")
    path.chmod(0o755)


@pytest.fixture
def fake_bin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Put recording, failing stand-ins for model and network tools first on PATH."""
    bin_dir = tmp_path / "fake-bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls.log"
    for name in FORBIDDEN_BINS:
        script(
            bin_dir / name,
            f"import sys\nopen({str(calls)!r}, 'a').write({name!r} + '\\n')\nsys.exit(1)\n",
        )
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return bin_dir


def fake_bwrap(bin_dir: Path, log: Path, body: str = "print('3 tests collected in 0.1s')") -> None:
    """Install a bwrap stand-in that records its argv and prints a collection summary."""
    script(
        bin_dir / "bwrap",
        "import json, sys\n"
        f"open({str(log)!r}, 'a').write(json.dumps(sys.argv[1:]) + '\\n')\n{body}\n",
    )


def test_offline_and_zero_token(run, fake_bin: Path, tmp_path: Path, monkeypatch) -> None:
    """With sockets blocked and fake tools on PATH the run succeeds and calls none of them."""

    def refuse(*args, **kwargs):
        raise AssertionError("network use")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    run()
    assert not (tmp_path / "calls.log").exists()


def test_imports_are_allowlisted() -> None:
    """Every import in the tool is on MR 2's allowlist; tomllib is imported inside a try."""
    tree = ast.parse(fx.TOOL.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            names.add(node.module)
        elif isinstance(node, ast.Name):
            assert node.id not in ("__import__", "eval", "exec")
    assert names <= ALLOWED_IMPORTS, names - ALLOWED_IMPORTS
    guarded = [
        a.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Try)
        for stmt in node.body
        if isinstance(stmt, ast.Import)
        for a in stmt.names
    ]
    assert guarded == ["tomllib"]


def test_executable_with_shebang() -> None:
    """The tool is executable and starts with the env shebang."""
    assert os.access(fx.TOOL, os.X_OK)
    assert fx.TOOL.read_text(encoding="utf-8").startswith("#!/usr/bin/env python3\n")


def test_project_is_unchanged_and_git_is_read_only(
    tool, fake_bin: Path, tmp_path: Path, monkeypatch
) -> None:
    """A run, with collection, leaves every project byte; git sees only the allowed verbs."""
    root = fx.build(tmp_path)
    log = tmp_path / "bwrap.log"
    fake_bwrap(fake_bin, log)
    out = tmp_path / "out"
    out.mkdir()
    calls = []
    real_run = subprocess.run

    def spy(argv, *args, **kwargs):
        calls.append((list(argv), kwargs.get("env")))
        return real_run(argv, *args, **kwargs)

    monkeypatch.setattr(tool.subprocess, "run", spy)
    before = fx.tree_hash(root)
    argv = ["--project", str(root), "--out-dir", str(out)]
    assert tool.main(argv) == 0
    assert fx.tree_hash(root) == before
    assert sorted(p.name for p in out.iterdir()) == [
        "progress.html",
        "progress.json",
        "progress.md",
    ]
    assert tool.main([*argv, "--collect-cases", "--collect-python", sys.executable]) == 0
    assert fx.tree_hash(root) == before
    git_calls = [(a, e) for a, e in calls if a[0] == "git"]
    assert git_calls and {a[0] for a, _ in calls} <= {"git", shutil.which("bwrap"), "bwrap"}
    for command, env in git_calls:
        assert command[1:4] == ["--no-optional-locks", "-C", str(root)], command
        assert command[4] in GIT_VERBS, command
        assert "-w" not in command
        assert env["GIT_OPTIONAL_LOCKS"] == "0" and env["GIT_TERMINAL_PROMPT"] == "0"
        assert env["LC_ALL"] == "C"
    for line in log.read_text(encoding="utf-8").splitlines():
        sandbox = json.loads(line)
        assert sandbox[:3] == ["--ro-bind", "/", "/"]
        assert "--unshare-net" in sandbox and "--die-with-parent" in sandbox
        # the one writable bind is the temp copy, mounted after the /tmp tmpfs
        tmp = sandbox[sandbox.index("--bind") + 1]
        assert not tmp.startswith(str(root)) and not Path(tmp).exists()
        assert sandbox.index("--tmpfs") < sandbox.index("--bind")
    report = fx.Report(out)
    unit = report.metric("per_release", "unit", "v0.1.0")["value"]
    assert unit == {"functions": 2, "cases": 3, "by_marker": {}, "deselected": 0}
    assert report.project["headline"]["tests"]["value"] == {
        "functions": 4,
        "cases": 3,
        "deselected": 0,
    }
    # no integration dir at v0.1.0: nothing is collected for it
    assert report.metric("per_release", "integration", "v0.1.0")["value"] is None


def test_git_environment_cannot_redirect_the_tool(tool, tmp_path: Path, monkeypatch) -> None:
    """An inherited GIT_DIR does not point the tool at another repository."""
    root = fx.build(tmp_path)
    other = fx.build(tmp_path / "b", name="other", omit="tag")
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    out = tmp_path / "out"
    out.mkdir()
    assert tool.main(["--project", str(root), "--out-dir", str(out)]) == 0
    assert fx.Report(out).project["headline"]["releases"]["value"] == 2


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        ("sys.exit(2)", "collection failed at v0.1.0: exit 2"),
        ("print('nothing useful')", "collection failed at v0.1.0: no summary line"),
    ],
)
def test_collection_failure_is_not_recorded(
    run, fake_bin: Path, tmp_path: Path, body: str, reason: str
) -> None:
    """A failed collection leaves the cases unknown and says why."""
    fake_bwrap(fake_bin, tmp_path / "bwrap.log", body)
    report = run(None, "--collect-cases", "--collect-python", sys.executable)
    metric = report.metric("per_release", "unit", "v0.1.0")
    assert metric["value"]["cases"] is None and reason in metric["source"]


def test_collection_timeout(tool, run, fake_bin: Path, tmp_path: Path, monkeypatch) -> None:
    """A collection that runs past the limit is 'not recorded' as a timeout."""
    fake_bwrap(fake_bin, tmp_path / "bwrap.log", "import time\ntime.sleep(30)")
    monkeypatch.setattr(tool, "COLLECT_TIMEOUT", 0.2)
    report = run(None, "--collect-cases", "--collect-python", sys.executable)
    source = report.metric("per_release", "unit", "v0.1.0")["source"]
    assert "collection failed at v0.1.0: timeout" in source


def test_collection_without_bwrap(tool, run, monkeypatch) -> None:
    """Without bwrap on PATH nothing is collected."""
    monkeypatch.setattr(tool.shutil, "which", lambda name: None)
    report = run(None, "--collect-cases", "--collect-python", sys.executable)
    metric = report.metric("per_release", "unit", "v0.1.0")
    assert metric["value"]["cases"] is None
    assert "--collect-cases needs bwrap" in metric["source"]


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("a\n3 tests collected in 0.01s\n", (3, 0)),
        ("1 test collected in 0.01s\n", (1, 0)),
        ("2/5 tests collected (3 deselected) in 0.01s\n", (2, 3)),
        ("no tests collected (5 deselected) in 0.01s\n", (0, 5)),
        ("no tests collected in 0.00s\n", (0, 0)),
        ("garbage\n", None),
    ],
)
def test_collection_summary_line(tool, text: str, want: tuple | None) -> None:
    """The counts come from pytest's summary line, deselected forms included."""
    assert tool.parse_collected(text) == want


def real_bwrap_works() -> bool:
    """Tell whether this host can start a bwrap sandbox."""
    if shutil.which("bwrap") is None:
        return False
    argv = ["bwrap", "--ro-bind", "/", "/", "--unshare-net", "--dev", "/dev", "true"]
    return subprocess.run(argv, capture_output=True, timeout=30).returncode == 0


@pytest.mark.skipif(not real_bwrap_works(), reason="bwrap cannot sandbox on this host")
def test_real_collection_with_markers(tool, tmp_path: Path) -> None:
    """A real sandboxed collection counts parametrized cases, by directory and by marker."""
    repo = fx.Repo(tmp_path / "real")
    repo.commit(
        "tests",
        "2026-01-01T00:00:00+0000",
        {
            "pyproject.toml": '[tool.pytest.ini_options]\nmarkers = ["e2e: slow"]\n',
            "tests/unit/test_p.py": (
                "import pytest\n\n\n"
                "@pytest.mark.parametrize('n', [1, 2, 3])\n"
                "def test_n(n):\n"
                "    pass\n"
            ),
            "tests/e2e/test_e.py": "\n".join(
                ["import pytest", "", "", "@pytest.mark.e2e", "def test_e():", "    pass", ""]
            ),
        },
    )
    repo.git("tag", "v0.1.0")
    out = tmp_path / "out"
    out.mkdir()
    before = fx.tree_hash(repo.root)
    argv = ["--project", str(repo.root), "--out-dir", str(out)]
    assert tool.main([*argv, "--collect-cases", "--collect-python", sys.executable]) == 0
    assert fx.tree_hash(repo.root) == before
    report = fx.Report(out)
    unit = report.metric("per_release", "unit", "v0.1.0")
    e2e = report.metric("per_release", "e2e", "v0.1.0")
    assert unit["value"] == {"functions": 1, "cases": 3, "by_marker": {}, "deselected": 0}, unit
    # Python 3.10 has no tomllib, so no marker can be read there and the source says why
    markers = {"e2e": 1} if sys.version_info >= (3, 11) else {}
    assert e2e["value"] == {"functions": 1, "cases": 1, "by_marker": markers, "deselected": 0}, e2e
    if not markers:
        assert "markers need Python 3.11" in e2e["source"]
    assert report.project["headline"]["tests"]["value"] == {
        "functions": 2,
        "cases": 4,
        "deselected": 0,
    }


def test_output_names_no_local_path(tool, tmp_path: Path) -> None:
    """No output holds the project's path, the out dir or HOME."""
    root = fx.build(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    assert tool.main(["--project", str(root), "--out-dir", str(out), "--host"]) == 0
    for path in out.iterdir():
        text = path.read_text(encoding="utf-8")
        assert str(tmp_path) not in text and os.environ["HOME"] not in text


def exit_code(tool, argv: list[str]) -> int:
    """Return the tool's exit code, argparse's included."""
    try:
        return tool.main(argv)
    except SystemExit as stop:
        return stop.code


def test_not_a_git_repo_exits_1_and_writes_nothing(tool, tmp_path: Path, capsys) -> None:
    """A directory without git, a subdirectory of one and an unreadable tag all exit 1."""
    plain = tmp_path / "plain"
    plain.mkdir()
    out = tmp_path / "out"
    out.mkdir()
    assert exit_code(tool, ["--project", str(plain), "--out-dir", str(out)]) == 1
    err = capsys.readouterr().err
    assert err.startswith("progress-report: plain: ") and err.count("\n") == 1
    assert str(tmp_path) not in err
    root = fx.build(tmp_path)
    assert exit_code(tool, ["--project", str(root / "src"), "--out-dir", str(out)]) == 1
    (root / ".git" / "refs" / "tags" / "v0.3.0").write_text("1" * 40 + "\n", encoding="utf-8")
    capsys.readouterr()
    assert exit_code(tool, ["--project", str(root), "--out-dir", str(out)]) == 1
    assert capsys.readouterr().err.startswith("progress-report: demo: ")
    assert list(out.iterdir()) == []


def test_unreadable_out_dir_exits_1(tool, tmp_path: Path, capsys) -> None:
    """An out dir that does not exist, or is a file, exits 1."""
    root = fx.build(tmp_path)
    (tmp_path / "file").write_text("x", encoding="utf-8")
    for name in ("missing", "file"):
        argv = ["--project", str(root), "--out-dir", str(tmp_path / name)]
        assert exit_code(tool, argv) == 1
        assert capsys.readouterr().err.startswith("progress-report: demo: ")
    assert not (tmp_path / "missing").exists()


@pytest.mark.parametrize(
    "extra",
    [
        ["--collect-cases"],
        ["--collect-python", "python3"],
        ["--rig", "/nowhere=x"],
        ["--rig", "noequals"],
        ["--kind", "smoke=tests"],
        ["--kind", "unit"],
        ["--source-path", "/abs"],
        ["--no-such-option"],
    ],
)
def test_usage_errors_exit_2(tool, tmp_path: Path, extra: list[str]) -> None:
    """A bad option exits 2 and writes nothing."""
    root = fx.build(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    assert exit_code(tool, ["--project", str(root), "--out-dir", str(out), *extra]) == 2
    assert list(out.iterdir()) == []


def test_out_dir_inside_a_project_exits_2(tool, tmp_path: Path) -> None:
    """The out dir may be neither a project root nor inside one, through a symlink included."""
    root = fx.build(tmp_path)
    (root / "report").mkdir()
    link = tmp_path / "link"
    link.symlink_to(root / "report")
    before = fx.tree_hash(root)
    for out in (root, root / "report", link, root / "new"):
        assert exit_code(tool, ["--project", str(root), "--out-dir", str(out)]) == 2
    assert fx.tree_hash(root) == before


def test_no_project_exits_2(tool, tmp_path: Path) -> None:
    """Without --project, or without --out-dir, the usage is wrong."""
    assert exit_code(tool, ["--out-dir", str(tmp_path)]) == 2
    assert exit_code(tool, ["--project", str(tmp_path)]) == 2
    assert exit_code(tool, []) == 2


def test_command_line_runs(tmp_path: Path) -> None:
    """The file runs as a program under this interpreter and leaves no bytecode behind."""
    root = fx.build(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    done = subprocess.run(
        [sys.executable, str(fx.TOOL), "--project", str(root), "--out-dir", str(out)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert done.returncode == 0, done.stderr
    assert (out / "progress.md").is_file() and not list(root.rglob("*.pyc"))
