"""Tests of dev/release_export.py's `gate` (release-export slice 3).

Offline and synthetic. A small private repository with two releases is exported with the real
`build`; the gate then runs in-process with an injected runner in place of the heavy steps 6-10
(check.sh, ruff, the two installs, the live code-graph contract), which run for real only in slice
5. Steps 1-5 (scan, shape, links, messages and the public-only clone) are real.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from test_build import (
    BASE_EMAIL,
    NAME,
    SCAN,
    TOOL,
    changelog,
    clean_env,
    commit_all,
    created,
    g,
    write,
)
from test_scan import FAKE_ADDRESS, sha


def load_tool():
    """Import dev/release_export.py as a module."""
    if "release_export" not in sys.modules:
        spec = importlib.util.spec_from_file_location("release_export", TOOL)
        module = importlib.util.module_from_spec(spec)
        sys.modules["release_export"] = module
        spec.loader.exec_module(module)
    return sys.modules["release_export"]


rx = load_tool()

INSTALLER = 'NEVER_COPY=(\n  "lore/" "dev/" # a comment\n  "SPEC.md"\n)\n'
README = "# App\n\nSee [the guide](docs/guide.md), [home](#top) and [web](https://example.com).\n"
DATES = {
    "base": ("1690000000 -0700", "1690000100 -0700"),
    "0.1.0": ("1700000000 +0530", "1700000500 -0800"),
    "0.2.0": ("1710000000 +0100", "1710000600 +0900"),
}

# A pytest `-rs` summary recorded from a real run (pytest 8, xdist), one line per kind.
RS_SAMPLE = """\
=========================== short test summary info ============================
SKIPPED [1] dev/tests/contract/test_live.py:2: live contract test: set HACF_LIVE_CODEGRAPH=1 after consent
SKIPPED [1] dev/tests/migration/test_migrate_specs.py:3: HACF history commit 8e9464e is absent
SKIPPED [1] dev/tests/other/test_other.py:5: network unavailable
========================= 1 passed, 3 skipped in 0.01s =========================
"""  # noqa: E501


@dataclass
class GateWorld:
    """The private repository, its releases and the profile."""

    tmp: Path
    src: Path
    base: str
    sources: dict[str, str]
    side: str  # a private commit that is in no export

    def profile(self, **changes) -> Path:
        """A profile for this world with top-level `changes`; return its path."""
        doc = {
            "schema": "hacf.release-export/v2",
            "required_digest_labels": [],
            "base": self.base,
            "identity": {"name": NAME, "email_from": "base"},
            "releases": [
                {"version": "0.1.0", "source": self.sources["0.1.0"]},
                {"version": "0.2.0", "source": self.sources["0.2.0"]},
            ],
            "exclude": [
                {"path": "lore/", "reason": "private records", "versions": "all"},
                {"path": "dev/tests/test_private.py", "reason": "private", "versions": "all"},
                {"path": "SPEC.md", "reason": "private spec", "versions": "all"},
            ],
            "expected_skips": [
                {
                    "versions": "all",
                    "nodeid": "dev/tests/migration/test_migrate_specs.py::test_golden",
                    "reason_contains": "8e9464e",
                }
            ],
            "top_level": ["CHANGELOG.md", "HACF-install.sh", "README.md", "app.py", "dev", "docs"],
            "scan": SCAN,
            "allowlist": [],
        }
        doc.update(changes)
        path = self.tmp / f"profile-{len(list(self.tmp.glob('profile-*')))}.yml"
        path.write_text(yaml.safe_dump(doc, sort_keys=False))
        return path

    def build(self, version: str, profile: Path) -> tuple[str, str]:
        """Run the real `build`; return the export commit and tag object."""
        proc = subprocess.run(
            [sys.executable, str(TOOL), "--repo", str(self.src), "--profile", str(profile),
             "build", "--version", version],
            capture_output=True, text=True, check=False,
        )  # fmt: skip
        return created(proc)


@pytest.fixture
def gw(tmp_path: Path) -> GateWorld:
    """The gate world."""
    return make_gate_world(tmp_path)


def make_gate_world(tmp_path: Path) -> GateWorld:
    """Two releases over a base; a private side commit."""
    src = tmp_path / "src"
    g(tmp_path, "init", "-q", "-b", "main", str(src))
    write(src, "README.md", "base\n")
    write(src, "CHANGELOG.md", changelog([]))
    write(src, "app.py", "print('base')\n")
    write(src, "old.txt", "dropped by 0.1.0\n")
    base = commit_all(src, "base", DATES["base"], who="Owner")
    sources, released = {}, []
    g(src, "rm", "-q", "old.txt")
    for v in ("0.1.0", "0.2.0"):
        released.append(v)
        write(src, "CHANGELOG.md", changelog(released))
        write(src, "README.md", README)
        write(src, "docs/guide.md", f"# Guide {v}\n\nBack to the [readme](../README.md).\n")
        write(src, "app.py", f"print('{v}')\n")
        write(src, "dev/tool.py", f"VERSION = '{v}'\n")
        write(src, "dev/tests/test_private.py", "# private history\n")
        write(src, "dev/tests/test_public.py", "def test_ok():\n    assert True\n")
        write(src, "lore/MAP.md", "private\n")
        write(src, "SPEC.md", "private spec\n")
        write(src, "HACF-install.sh", INSTALLER)
        sources[v] = commit_all(src, f"release {v}", DATES[v])
    write(src, "app.py", "print('private work')\n")
    side = commit_all(src, "private work after the releases", DATES["0.2.0"])
    return GateWorld(tmp_path, src, base, sources, side)


@dataclass
class FakeRunner(rx.Runner):
    """Stands in for check.sh, ruff, the installs, doctor and the live contract."""

    check_code: int = 0
    check_output: str = RS_SAMPLE.replace(
        "dev/tests/other/test_other.py:5: network unavailable\n", ""
    )
    calls: list[tuple[list[str], Path, dict]] = field(default_factory=list)
    clone_commits: set[str] = field(default_factory=set)
    clone_state: dict[str, object] = field(default_factory=dict)
    install_extra: dict[str, str] = field(default_factory=dict)
    shipped_verdict: bool = False
    live_output: str = "1 passed in 0.50s"
    verdict_before_live: bool | None = None
    stopped_runtime: list[Path] = field(default_factory=list)
    spec_heading: bool = False  # SPEC.md heading with the dir basename, as the installer writes

    def run(self, argv, cwd, env):
        """Record the call and act like the real command."""
        self.calls.append((list(argv), Path(cwd), dict(env)))
        if argv[:2] == ["bash", "dev/scripts/check.sh"]:
            if self.shipped_verdict:  # a verdict file committed by an earlier live run
                write(Path(cwd), rx.LIVE_VERDICT, json.dumps({"contract_verdict": "pass"}))
            self.clone_commits = set(g(Path(cwd), "rev-list", "--all").split())
            self.clone_state = {
                "head": g(Path(cwd), "rev-parse", "HEAD"),
                "main": g(Path(cwd), "rev-parse", "refs/heads/main"),
                "tags": g(Path(cwd), "tag", "-l"),
                "files": sorted(p.name for p in Path(cwd).iterdir()),
            }
            return self.check_code, self.check_output
        if argv[:4] == ["uv", "run", "--locked", "ruff"]:
            return 0, ""
        if argv[0] == "bash" and argv[1].endswith("HACF-install.sh"):
            ref = next(a for a in argv if a.startswith("--ref="))[6:]
            source = next(a for a in argv if a.startswith("--source="))[9:]
            self.install(Path(cwd), ref, source, "--install-code-graph" in argv)
            return 0, "installed"
        if "doctor" in argv:
            report = {
                "backend": "codebase-memory-mcp",
                "prefix": ["x"],
                "backend_version": "0.12.1",
                "pinned_version": "0.12.1",
            }
            return 0, json.dumps(report)
        if argv[-1] == rx.LIVE_TEST:
            verdict = Path(cwd) / rx.LIVE_VERDICT
            self.verdict_before_live = verdict.exists()
            if self.live_output.startswith("1 passed"):
                verdict.parent.mkdir(parents=True, exist_ok=True)
                verdict.write_text(json.dumps({"contract_verdict": "pass"}))
            return 0, self.live_output
        raise AssertionError(f"unexpected command {argv}")

    def stop_isolated_daemons(self, runtime_dir: Path) -> list[str]:
        """Record which runtime dir step 10 cleaned up; no real process is touched."""
        self.stopped_runtime.append(Path(runtime_dir))
        return ["no daemon of the step's own runtime dir was left running"]

    def install(self, project: Path, ref: str, source: str, codegraph: bool) -> None:
        """Write what an install writes, record fields included."""
        write(project, ".agents/scripts/tool.py", f"# from {source}\n")
        manifest = {
            "version": "0.2.0",
            "installed_commit": ref,
            "installed_at": f"t{len(self.calls)}",
            "source": {"kind": "local", "commit": ref},
            "files": {"a": 1},
        }
        write(project, rx.MANIFEST_REL, json.dumps(manifest))
        record = {"schema": "s", "kind": "local", "path": source, "ref": ref, "commit": ref,
                  "written_at": f"w{len(self.calls)}"}  # fmt: skip
        write(project, rx.SOURCE_RECORD_REL, json.dumps(record))
        write(project, f".agents/.backup/2026112{len(self.calls) % 10}T000000Z/s.json", "{}")
        if self.spec_heading:
            heading = f"---\nhacf_type: SystemOfRecord\n---\n\n# Project - {project.name}\n"
            write(project, "SPEC.md", heading)
        for rel, text in self.install_extra.items():
            if ref in rel or project.parent.name == "export":
                write(project, rel.replace(ref, ""), text)
        if codegraph:
            for rel in rx.MCP_FILES:
                write(project, rel, json.dumps({"mcpServers": {"codebase-memory-mcp": {}}}))


def write_digests(tmp_path: Path) -> Path:
    """A digest file with one digest of a fake address."""
    path = tmp_path / "digests.sha256"
    path.write_text("e" * 64 + " fake\n")
    return path


@pytest.fixture
def digests(tmp_path: Path) -> Path:
    """The digest file."""
    return write_digests(tmp_path)


@pytest.fixture(autouse=True)
def py310(monkeypatch: pytest.MonkeyPatch) -> None:
    """The gate needs HACF_PY310; the injected runner never uses it."""
    monkeypatch.setenv("HACF_PY310", "/opt/fake-python3.10")


@pytest.fixture(autouse=True)
def heavy_lock(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """The gate's host lock under tmp_path (never the user's), no inherited token, floor met."""
    lock = tmp_path / "heavy-lock" / "heavy-job.lock"
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal: 33554432 kB\nMemAvailable: 16777216 kB\n")
    monkeypatch.setenv("HACF_HEAVY_LOCK_PATH", str(lock))
    monkeypatch.setenv("HACF_HOST_MEMINFO", str(meminfo))
    monkeypatch.setenv("HACF_HEAVY_POLL_S", "0.05")
    monkeypatch.delenv("HACF_HEAVY_LOCK_HELD", raising=False)
    return lock


def run_gate(gw: GateWorld, profile: Path, version: str, digests: Path, root: Path, runner=None):
    """Run the gate in-process; return (exit code, report dir)."""
    runner = runner or FakeRunner()
    code = rx.gate(gw.src, profile, version, str(digests), root, "origin", runner)
    export = g(gw.src, "rev-parse", f"v{version}^{{commit}}")
    return code, root / f"{version}-{export[:12]}"


def build_both(gw: GateWorld, profile: Path) -> None:
    """Export 0.1.0 and 0.2.0."""
    gw.build("0.1.0", profile)
    gw.build("0.2.0", profile)


# -------------------------------------------------------------------------------------- PASS


def test_gate_passes_and_writes_the_report(gw: GateWorld, digests: Path, tmp_path: Path) -> None:
    """A clean export passes all eleven steps; REPORT.md layout; the throwaway dirs are removed."""
    profile = gw.profile()
    build_both(gw, profile)
    runner = FakeRunner()
    code, report_dir = run_gate(gw, profile, "0.2.0", digests, tmp_path / "reports", runner)
    report = (report_dir / "REPORT.md").read_text()
    assert code == 0, report
    lines = report.splitlines()
    assert lines[0] == "VERDICT: PASS"
    export = g(gw.src, "rev-parse", "v0.2.0^{commit}")
    assert f"- export commit: {export}" in lines
    assert f"- tree: {g(gw.src, 'rev-parse', 'v0.2.0^{tree}')}" in lines
    assert f"- tag object: {g(gw.src, 'rev-parse', 'v0.2.0')}" in lines
    assert f"- source: {gw.sources['0.2.0']}" in lines
    steps = [ln.split(" | ")[1:3] for ln in lines if ln.startswith("| ") and ln[2].isdigit()]
    assert steps == [[name, "PASS"] for name, _ in rx.STEPS]
    assert [name for name, _ in rx.STEPS][9:] == ["codegraph-live", "openrig-contract"]
    assert "not checked (no openrig_accepted on this row)" in report
    for heading in ("Exclusions applied", "Advisory hits (never failing)", "Public deletions",
                    "Skips (check.sh, -rs)", "Messages in full"):  # fmt: skip
        assert f"## {heading}" in report
    assert "| dev/tests/test_private.py | private | 1 | 0 |" in report
    assert g(gw.src, "cat-file", "-p", export) in report  # the raw commit object, in full
    assert "HACF v0.2.0" in report  # the tag message
    assert "expected  SKIPPED [1] dev/tests/migration/test_migrate_specs.py:3" in report
    assert "live      SKIPPED [1] dev/tests/contract/test_live.py:2" in report
    logs = sorted(p.name for p in (report_dir / "logs").iterdir())
    assert logs == [f"{n:02d}-{name}.log" for n, (name, _) in enumerate(rx.STEPS, start=1)]
    assert not (report_dir / "work").exists()

    check = next(c for c in runner.calls if c[0][:2] == ["bash", "dev/scripts/check.sh"])
    env = check[2]
    assert (env["HACF_PY310"], env["HACF_TEST_WORKERS"], env["PYTEST_ADDOPTS"]) == (
        "/opt/fake-python3.10", "4", "-rsfE")  # fmt: skip
    assert env["TMPDIR"].startswith(str(report_dir / "work" / "06-check"))
    assert not any(k.startswith("GIT_") for k in env)
    ruff = [c[0] for c in runner.calls if c[0][:4] == ["uv", "run", "--locked", "ruff"]]
    # Every tracked .py of the clone (F2), sorted, after --force-exclude.
    assert ruff[0][ruff[0].index("--force-exclude") :] == [
        "--force-exclude", "--", "app.py", "dev/tests/test_public.py", "dev/tool.py"]  # fmt: skip
    tail = ruff[0][ruff[0].index("--force-exclude") :]
    assert ruff[1][ruff[1].index("--force-exclude") :] == tail
    assert "--check" in ruff[1]
    installs = [c for c in runner.calls if c[0][0] == "bash" and "HACF-install.sh" in c[0][1]]
    assert len(installs) == 4
    assert installs[0][0][2:] == [f"--source={installs[0][0][1][:-16]}", f"--ref={export}",
                                  "-y", "--ides=all"]  # fmt: skip
    assert installs[1][0][3] == f"--ref={gw.sources['0.2.0']}"
    assert "--code-graph=codebase-memory-mcp" in installs[2][0]
    cg_env = installs[2][2]
    for name in rx.CACHE_VARS:
        assert cg_env[name].startswith(str(report_dir / "work" / "09-install-codegraph"))
    live = next(c for c in runner.calls if c[0][-1] == rx.LIVE_TEST)
    assert live[2]["HACF_LIVE_CODEGRAPH"] == "1"
    assert live[1].name == "clone"


def test_clone_holds_main_at_the_export_and_no_private_object(
    gw: GateWorld, digests: Path, tmp_path: Path
) -> None:
    """The clone's commits are the base history and the exports; no private commit."""
    profile = gw.profile()
    build_both(gw, profile)
    runner = FakeRunner()
    run_gate(gw, profile, "0.2.0", digests, tmp_path / "r", runner)
    exports = {g(gw.src, "rev-parse", f"v{v}^{{commit}}") for v in ("0.1.0", "0.2.0")}
    assert runner.clone_commits == exports | {gw.base}
    for private in (gw.side, *gw.sources.values()):
        assert private not in runner.clone_commits
    export = g(gw.src, "rev-parse", "v0.2.0^{commit}")
    state = runner.clone_state  # recorded inside the clone while check.sh "ran"
    assert (state["head"], state["main"]) == (export, export)
    assert state["tags"] == "v0.1.0\nv0.2.0"
    assert "app.py" in state["files"] and "lore" not in state["files"]
    assert "SPEC.md" not in state["files"]


def test_install_difference_fails(gw: GateWorld, digests: Path, tmp_path: Path) -> None:
    """An install from the export that differs from the source install fails step 8."""
    profile = gw.profile()
    build_both(gw, profile)
    runner = FakeRunner(install_extra={"extra.txt": "only in the export install\n"})
    code, report_dir = run_gate(gw, profile, "0.2.0", digests, tmp_path / "r", runner)
    assert code == 1
    log = (report_dir / "logs" / "08-install-plain.log").read_text()
    assert "only in the export install: extra.txt" in log
    report = (report_dir / "REPORT.md").read_text()
    assert "Throwaway directories kept for inspection (FAIL):" in report
    assert (report_dir / "work").is_dir()


def test_install_basename_alone_differs_but_install_pair_shares_it(
    gw: GateWorld, digests: Path, tmp_path: Path
) -> None:
    """Mission R24: the scaffolded `# Project - <basename>` line made two installs differ.

    Two dirs whose only difference is the basename in SPEC.md compare unequal (the first 0.6.0
    gate's false `differs: SPEC.md`); `install_pair` installs both under one basename, so step 8
    passes and step 9's pair shares it too.
    """
    runner = FakeRunner(spec_heading=True)
    a, b = tmp_path / "pair" / "project-export", tmp_path / "pair" / "project-source"
    for project in (a, b):
        runner.install(project, "r", "/src", False)
    assert rx.compare_installs(a, b, {"/src": "<hacf>"}) == ["differs: SPEC.md"]

    profile = gw.profile()
    build_both(gw, profile)
    runner = FakeRunner(spec_heading=True)
    code, report_dir = run_gate(gw, profile, "0.2.0", digests, tmp_path / "r", runner)
    assert code == 0
    installs = [c[1] for c in runner.calls if c[0][0] == "bash" and "HACF-install.sh" in c[0][1]]
    assert len(installs) == 4
    assert {p.name for p in installs} == {"project"}
    assert {p.parent.name for p in installs} == {"export", "source"}
    log = (report_dir / "logs" / "08-install-plain.log").read_text()
    assert "equal except the named record fields" in log


def test_live_step_runs_isolated_from_the_account_daemon(
    gw: GateWorld, digests: Path, tmp_path: Path
) -> None:
    """Mission R24: step 10 has its own HOME, runtime dirs and CBM cache; cleanup is its own."""
    profile = gw.profile()
    build_both(gw, profile)
    runner = FakeRunner()
    code, report_dir = run_gate(gw, profile, "0.2.0", digests, tmp_path / "r", runner)
    assert code == 0
    step = report_dir / "work" / "10-codegraph-live"
    live = next(c for c in runner.calls if c[0][-1] == rx.LIVE_TEST)
    env = live[2]
    for name in ("HOME", "XDG_RUNTIME_DIR", "CBM_CACHE_DIR"):
        assert env[name].startswith(str(step) + os.sep), name
    live_tmp = Path(env["TMPDIR"])  # short and on disk (lead ruling P12)
    assert live_tmp.parent == rx.LIVE_TMP_BASE and live_tmp.name.startswith("hacf-live.")
    assert not live_tmp.exists()  # removed by its exact path after the run
    assert env["XDG_RUNTIME_DIR"] == str(step / "run")
    runtime = Path(env["CBM_RUNTIME_DIR"])  # short, for the AF_UNIX limit (lead ruling P11)
    assert runtime.parent == Path("/tmp") and runtime.name.startswith("hacf-cbm.")
    assert env["UV_PYTHON_INSTALL_DIR"]  # uv's own Python stays reachable under the new HOME
    assert runner.stopped_runtime == [runtime]
    assert not runtime.exists()  # removed by its exact path after the run
    log = (report_dir / "logs" / "10-codegraph-live.log").read_text()
    assert "isolated from the account code-graph daemon: CBM_RUNTIME_DIR=" in log
    assert "no daemon of the step's own runtime dir was left running" in log
    assert f"removed the step's CBM runtime dir {runtime}" in log


LEAKER = "leaker\x40corp-mail.zz"  # a synthetic leaking address (no contiguous address here)


# A deep report path under a home dir, built from parts (no home root or state dir is typed).
DEEP_RUN = Path("/" + "home", "u", ".hacf", "hacf", "release-export", "0.6.0-37696bc9c525")
DEEP_RUN = DEEP_RUN / "work" / "10-codegraph-live"


def test_cbm_socket_length_guard(tmp_path: Path) -> None:
    """A runtime dir whose socket path reaches 100 bytes is refused before CBM starts (P11)."""
    assert rx.cbm_socket_problem(Path("/tmp/hacf-cbm.abcdef")) is None
    assert len(rx.cbm_socket_path(Path("/tmp/hacf-cbm.abcdef")).encode()) < 100
    deep = DEEP_RUN / "run"
    problem = rx.cbm_socket_problem(deep)
    assert problem is not None and "AF_UNIX" in problem and str(deep) in problem
    assert len(rx.cbm_socket_path(deep).encode()) >= 100


def test_live_project_name_guard() -> None:
    """The live test's predicted project name over 128 chars is refused before the run (P12)."""
    short = Path.home() / ".cache" / "hacf-live.abcdef"
    assert rx.live_project_problem(short) is None
    name = rx.live_project_name(short)
    assert name.endswith("-pytest-0-test_codegraph_contract0-repo") and "/" not in name
    deep = DEEP_RUN / "tmp"
    problem = rx.live_project_problem(deep)
    assert problem is not None and "> 128" in problem
    assert len(rx.live_project_name(deep)) > 128


def test_deep_live_tmpdir_fails_step_10_without_running(
    gw: GateWorld, digests: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A TMPDIR whose project name would pass 128 chars FAILs step 10 as setup; dirs removed."""
    base = tmp_path / ("d" * 80)
    monkeypatch.setattr(rx, "LIVE_TMP_BASE", base)
    profile = gw.profile()
    build_both(gw, profile)
    runner = FakeRunner()
    code, report_dir = run_gate(gw, profile, "0.2.0", digests, tmp_path / "r", runner)
    assert code == 1
    report = (report_dir / "REPORT.md").read_text()
    assert "| codegraph-live | FAIL |" in report and "CBM project name" in report
    assert not any(c[0][-1] == rx.LIVE_TEST for c in runner.calls)
    assert list(base.iterdir()) == []


def test_long_cbm_runtime_dir_fails_step_10_without_running(
    gw: GateWorld, digests: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With a runtime base too deep for the socket, step 10 FAILs as setup and runs no test."""
    base = tmp_path / ("d" * 60)
    base.mkdir()
    monkeypatch.setattr(rx, "CBM_RUNTIME_BASE", str(base))
    monkeypatch.setattr(rx.live_isolation, "__defaults__", (str(base),))
    profile = gw.profile()
    build_both(gw, profile)
    runner = FakeRunner()
    code, report_dir = run_gate(gw, profile, "0.2.0", digests, tmp_path / "r", runner)
    assert code == 1
    report = (report_dir / "REPORT.md").read_text()
    assert "| codegraph-live | FAIL |" in report and "AF_UNIX limit" in report
    assert not any(c[0][-1] == rx.LIVE_TEST for c in runner.calls)
    assert list(base.iterdir()) == []  # the runtime dir it made is gone


def test_an_earlier_report_dir_is_kept_not_removed(
    gw: GateWorld, digests: Path, tmp_path: Path
) -> None:
    """A second gate of the same export renames the first report dir aside (P11)."""
    profile = gw.profile()
    build_both(gw, profile)
    root = tmp_path / "r"
    run_gate(gw, profile, "0.2.0", digests, root, FakeRunner(check_code=1))
    code, report_dir = run_gate(gw, profile, "0.2.0", digests, root, FakeRunner())
    assert code == 0
    kept = [p for p in root.iterdir() if p.name.startswith(f"{report_dir.name}.prev-")]
    assert len(kept) == 1
    assert (kept[0] / "REPORT.md").read_text().startswith("VERDICT: FAIL\n")
    assert (kept[0] / "work").is_dir()  # the FAIL evidence survives
    assert (report_dir / "REPORT.md").read_text().startswith("VERDICT: PASS\n")


def test_live_isolation_dirs_are_private(tmp_path: Path) -> None:
    """The isolated HOME and runtime dir are mode 700 inside the step dir."""
    env, runtime = rx.live_isolation(tmp_path / "10-codegraph-live")
    try:
        assert env["XDG_RUNTIME_DIR"] == str(tmp_path / "10-codegraph-live" / "run")
        for name in ("HOME", "XDG_RUNTIME_DIR", "CBM_RUNTIME_DIR"):
            assert (Path(env[name]).stat().st_mode & 0o777) == 0o700
    finally:
        assert rx.remove_runtime(runtime) == f"removed the step's CBM runtime dir {runtime}"
    assert not runtime.exists()


def test_live_isolation_hides_a_path_code_graph_but_keeps_uv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A codebase-memory-mcp on PATH would win over the pinned uvx prefix; its dir is left out."""
    shared, plain = tmp_path / "shared", tmp_path / "plain"
    for path, tools in ((shared, ("codebase-memory-mcp", "uv", "uvx")), (plain, ("other",))):
        path.mkdir()
        for tool in tools:
            (path / tool).write_text("#!/bin/sh\n")
            (path / tool).chmod(0o755)
    monkeypatch.setenv("PATH", os.pathsep.join([str(shared), str(plain)]))
    step = tmp_path / "10-codegraph-live"
    env, runtime = rx.live_isolation(step)
    rx.remove_runtime(runtime)
    assert env["PATH"].split(os.pathsep) == [str(step / "bin"), str(plain)]
    for tool in ("uv", "uvx"):
        assert (step / "bin" / tool).resolve() == shared / tool
    assert not (step / "bin" / "codebase-memory-mcp").exists()


def test_cleanup_stops_only_the_isolated_daemon(tmp_path: Path) -> None:
    """Only a codebase-memory-mcp whose CBM_RUNTIME_DIR is the step's is stopped (R24).

    Stand-ins: sleeping interpreters run through a symlink named codebase-memory-mcp (argv[0]
    keeps the name; the interpreter finds its prefix through the link), one per runtime dir and
    one with none (the account daemon's shape), plus one with another name and the step's dir.
    """
    mine, other = tmp_path / "step" / "run", tmp_path / "other" / "run"
    base = {k: v for k, v in os.environ.items() if k != "CBM_RUNTIME_DIR"}
    real = os.path.realpath(sys.executable)
    links = {}
    for name in ("codebase-memory-mcp", "python-stand-in"):
        links[name] = tmp_path / "bin" / name
        links[name].parent.mkdir(exist_ok=True)
        links[name].symlink_to(real)

    def sleeper(name: str, runtime: Path | None) -> subprocess.Popen:
        env = {**base, "CBM_RUNTIME_DIR": str(runtime)} if runtime else base
        return subprocess.Popen([str(links[name]), "-c", "import time; time.sleep(60)"], env=env)

    procs = {
        "mine": sleeper("codebase-memory-mcp", mine),
        "other": sleeper("codebase-memory-mcp", other),
        "account": sleeper("codebase-memory-mcp", None),
        "not_cbm": sleeper("python-stand-in", mine),
    }
    try:
        runner = rx.Runner()
        assert runner.isolated_daemons(mine) == [procs["mine"].pid]
        lines = runner.stop_isolated_daemons(mine)
        assert lines == [f"stopped the step's own daemon pid {procs['mine'].pid} (SIGTERM)"]
        assert procs["mine"].wait(timeout=10) is not None
        for name in ("other", "account", "not_cbm"):
            assert procs[name].poll() is None, name
        assert runner.stop_isolated_daemons(mine) == [
            "no daemon of the step's own runtime dir was left running"
        ]
    finally:
        for proc in procs.values():
            if proc.poll() is None:
                proc.kill()
            proc.wait()


# -------------------------------------------------------------------------------- early FAIL


def assert_stopped_before_clone(report_dir: Path, runner: FakeRunner, failing: str) -> None:
    """Steps 5-11 did not run and no clone dir exists; `failing` is a FAIL step."""
    report = (report_dir / "REPORT.md").read_text()
    assert report.startswith("VERDICT: FAIL\n")
    assert f"| {failing} | FAIL |" in report
    assert "| clone | n/a |" in report and "no clone, no download" in report
    assert "| openrig-contract | n/a |" in report
    assert runner.calls == []
    assert not (report_dir / "work" / "05-clone").exists()


def test_link_into_excluded_path_fails_before_any_clone(
    gw: GateWorld, digests: Path, tmp_path: Path
) -> None:
    """A Markdown link into an excluded path fails `links`; nothing is cloned."""
    write(gw.src, "README.md", README + "\nThe [map](lore/MAP.md) and [gone](nope.md).\n")
    write(gw.src, "CHANGELOG.md", changelog(["0.1.0", "0.2.0", "0.3.0"]))
    source = commit_all(gw.src, "0.3.0", DATES["0.2.0"])
    releases = yaml.safe_load(gw.profile().read_text())["releases"]
    releases.append({"version": "0.3.0", "source": source})
    profile = gw.profile(releases=releases)
    for v in ("0.1.0", "0.2.0", "0.3.0"):
        gw.build(v, profile)
    runner = FakeRunner()
    code, report_dir = run_gate(gw, profile, "0.3.0", digests, tmp_path / "r", runner)
    assert code == 1
    assert_stopped_before_clone(report_dir, runner, "links")
    log = (report_dir / "logs" / "03-links.log").read_text()
    assert "FAIL README.md:5 -> lore/MAP.md (inside excluded lore/)" in log
    assert "unresolved README.md:5 -> nope.md (reported, not failing)" in log
    assert "guide.md" not in log  # resolved links are not listed


def test_top_level_name_not_listed_fails(gw: GateWorld, digests: Path, tmp_path: Path) -> None:
    """A top-level name missing from `top_level` fails `shape` (mission R17)."""
    full = gw.profile()
    build_both(gw, full)
    top = [n for n in yaml.safe_load(full.read_text())["top_level"] if n != "docs"]
    runner = FakeRunner()
    code, report_dir = run_gate(
        gw, gw.profile(top_level=top), "0.2.0", digests, tmp_path / "r", runner
    )
    assert code == 1
    assert_stopped_before_clone(report_dir, runner, "shape")
    assert "top-level name 'docs' is not in top_level" in (report_dir / "REPORT.md").read_text()


def test_exclude_not_in_never_copy_fails(gw: GateWorld, digests: Path, tmp_path: Path) -> None:
    """An exclude row whose path the source's NEVER_COPY does not cover fails `shape`."""
    doc = yaml.safe_load(gw.profile().read_text())
    exclude = [*doc["exclude"], {"path": "docs/", "reason": "x", "versions": "all"}]
    top = [n for n in doc["top_level"] if n != "docs"]
    profile = gw.profile(exclude=exclude, top_level=top)
    build_both(gw, profile)
    runner = FakeRunner()
    code, report_dir = run_gate(gw, profile, "0.2.0", digests, tmp_path / "r", runner)
    assert code == 1
    assert_stopped_before_clone(report_dir, runner, "shape")
    log = (report_dir / "logs" / "02-shape.log").read_text()
    assert "FAIL exclude docs/ is not covered by the source's NEVER_COPY" in log
    assert "exclude lore/: covered by NEVER_COPY" in log
    assert "exclude dev/tests/test_private.py: covered by NEVER_COPY" in log  # dev/ covers it


def test_shape_lists_public_deletions_and_parent(
    gw: GateWorld, digests: Path, tmp_path: Path
) -> None:
    """The first export's deletions are listed against the base; its parent is the base."""
    profile = gw.profile()
    gw.build("0.1.0", profile)
    code, report_dir = run_gate(gw, profile, "0.1.0", digests, tmp_path / "r")
    assert code == 0, (report_dir / "REPORT.md").read_text()
    report = (report_dir / "REPORT.md").read_text()
    assert "- old.txt" in report.split("## Public deletions")[1]
    log = (report_dir / "logs" / "02-shape.log").read_text()
    assert f"expected [{gw.base}]" in log


def test_header_hit_fails_the_scan(gw: GateWorld, tmp_path: Path) -> None:
    """A planted committer email in the export commit's header fails step 1."""
    profile = gw.profile()
    gw.build("0.1.0", profile)
    good = g(gw.src, "rev-parse", "v0.1.0^{commit}")
    tree, parent = g(gw.src, "rev-parse", f"{good}^{{tree}}"), gw.base
    env = clean_env(GIT_AUTHOR_NAME=NAME, GIT_AUTHOR_EMAIL=BASE_EMAIL,
                    GIT_COMMITTER_NAME=NAME, GIT_COMMITTER_EMAIL=LEAKER)  # fmt: skip
    bad = g(gw.src, "commit-tree", tree, "-p", parent, "-m", "x", env=env)
    tag_text = f"object {bad}\ntype commit\ntag v0.1.0\ntagger {NAME} <{BASE_EMAIL}> 1 +0000\n\nx\n"
    tag = subprocess.run(
        ["git", "-C", str(gw.src), "mktag"],
        input=tag_text,
        text=True,
        capture_output=True,
        env=clean_env(),
        check=True,
    ).stdout.strip()
    g(gw.src, "update-ref", "refs/tags/v0.1.0", tag)
    g(gw.src, "update-ref", "refs/heads/public/main", bad)
    digests = tmp_path / "d"
    digests.write_text("f" * 64 + "\n")
    runner = FakeRunner()
    code, report_dir = run_gate(gw, profile, "0.1.0", digests, tmp_path / "r", runner)
    assert code == 1
    assert_stopped_before_clone(report_dir, runner, "scan")
    log = (report_dir / "logs" / "01-scan.log").read_text()
    assert "FAIL email:leaker\x40corp-mail.zz  <commit object>  x1" in log


# ------------------------------------------------------------------------------- host, skips


def test_host_busy_fails_after_the_locks_bounded_wait(
    gw: GateWorld, digests: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A host below the memory floor fails check.sh `host busy` after the lock's bounded wait.

    HACF_HEAVY_WAIT_S bounds it; later steps do not run and the gate exits 75.
    """
    profile = gw.profile()
    build_both(gw, profile)
    (tmp_path / "meminfo").write_text("MemAvailable: 102400 kB\n")
    monkeypatch.setenv("HACF_HEAVY_WAIT_S", "0.3")
    runner = FakeRunner()
    code, report_dir = run_gate(gw, profile, "0.2.0", digests, tmp_path / "r", runner)
    assert code == rx.EXIT_BUSY == 75
    report = (report_dir / "REPORT.md").read_text()
    assert "| check.sh | FAIL |" in report and "host busy (MemAvailable 100 MB < 6144 MB" in report
    assert "| install-plain | n/a |" in report
    assert runner.calls == []


def test_a_held_lock_is_the_host_busy_outcome(
    gw: GateWorld, digests: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Another job holding the lock + a short wait: check.sh `host busy` naming the holder, 75."""
    profile = gw.profile()
    build_both(gw, profile)
    monkeypatch.setenv("HACF_HEAVY_WAIT_S", "0.3")
    with rx.host_gate.acquire("other-job"):
        monkeypatch.delenv("HACF_HEAVY_LOCK_HELD", raising=False)
        runner = FakeRunner()
        code, report_dir = run_gate(gw, profile, "0.2.0", digests, tmp_path / "r", runner)
    assert code == 75
    report = (report_dir / "REPORT.md").read_text()
    assert "| check.sh | FAIL |" in report and "host busy (held by other-job" in report
    assert runner.calls == []


def test_the_gate_holds_one_lease_for_its_gated_steps(
    gw: GateWorld, digests: Path, tmp_path: Path, heavy_lock: Path
) -> None:
    """Steps 6-10 run inside one lease.

    One acquire and one release in the log, every heavy command carries the lease's token, and
    the lock is free after the run.
    """
    profile = gw.profile()
    build_both(gw, profile)
    runner = FakeRunner()
    code, _ = run_gate(gw, profile, "0.2.0", digests, tmp_path / "r", runner)
    assert code == 0
    events = [line.split()[1] for line in heavy_lock.with_suffix(".log").read_text().splitlines()]
    assert events == ["acquire", "release"]
    tokens = {env.get("HACF_HEAVY_LOCK_HELD") for _, _, env in runner.calls}
    assert len(tokens) == 1 and None not in tokens
    assert "HACF_HEAVY_LOCK_HELD" not in os.environ
    assert not heavy_lock.with_suffix(".json").exists()


def test_skip_classification_of_a_recorded_sample(gw: GateWorld) -> None:
    """Expected, live and unexpected skips in a recorded `-rs` summary."""
    profile = rx.load_profile(gw.profile())
    lines, unexpected = rx.classify_skips(RS_SAMPLE, profile, "0.2.0")
    assert unexpected == 1
    assert [ln.split()[0] for ln in lines] == ["live", "expected", "UNEXPECTED"]
    assert lines[2].endswith("dev/tests/other/test_other.py:5: network unavailable")
    other = rx.load_profile(gw.profile(expected_skips=[]))
    assert rx.classify_skips(RS_SAMPLE, other, "0.2.0")[1] == 2


def test_unexpected_skip_fails_check(gw: GateWorld, digests: Path, tmp_path: Path) -> None:
    """check.sh exit 0 with an unexpected skip still fails step 6."""
    profile = gw.profile()
    build_both(gw, profile)
    runner = FakeRunner(check_output=RS_SAMPLE)
    code, report_dir = run_gate(gw, profile, "0.2.0", digests, tmp_path / "r", runner)
    assert code == 1
    assert "| check.sh | FAIL | 0 |" in (report_dir / "REPORT.md").read_text()


CHECK_TWO_LEGS = """\
==> [1/4] preflight: /opt/fake-python3.10 can import pytest, yaml and xdist
==> [2/4] pytest (uv venv)
=========================== short test summary info ============================
SKIPPED [1] dev/tests/contract/test_live.py:2: live contract test: set HACF_LIVE_CODEGRAPH=1 after consent
SKIPPED [1] dev/tests/migration/test_migrate_specs.py:3: HACF history commit 8e9464e is absent
FAILED dev/tests/test_public.py::test_one - AssertionError: no
=================== 1 failed, 5 passed, 2 skipped in 3.10s ====================
==> [3/4] pytest (/opt/fake-python3.10)
========================= 7 passed, 1 skipped in 2.00s =========================
"""  # noqa: E501


def test_check_step_prints_the_per_leg_summary(
    gw: GateWorld, digests: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Step 6 runs check.sh verbose with its logs in the work dir; the console shows a summary."""
    profile = gw.profile()
    build_both(gw, profile)
    runner = FakeRunner(check_code=1, check_output=CHECK_TWO_LEGS)
    code, report_dir = run_gate(gw, profile, "0.2.0", digests, tmp_path / "r", runner)
    assert code == 1
    env = next(c[2] for c in runner.calls if c[0][:2] == ["bash", "dev/scripts/check.sh"])
    assert env["HACF_CHECK_VERBOSE"] == "1"
    assert env["HACF_LOG_DIR"] == str(report_dir / "work" / "06-check" / "check-logs")
    assert env["PYTEST_ADDOPTS"] == "-rsfE"
    summary = [
        "[1/4] preflight: /opt/fake-python3.10 can import pytest, yaml and xdist: "
        "(no pytest result line; the last 40 lines)",
        "[2/4] pytest (uv venv): FAIL 1 failed, 5 passed, 2 skipped in 3.10s",
        "  FAILED dev/tests/test_public.py::test_one",
        "[3/4] pytest (/opt/fake-python3.10): PASS 7 passed, 1 skipped in 2.00s",
        "log: logs/06-check.sh.log",
    ]
    log = (report_dir / "logs" / "06-check.sh.log").read_text()
    assert CHECK_TWO_LEGS.splitlines()[5] in log  # the full output, as before
    assert "\n".join(["summary:", *summary]) in log
    report = (report_dir / "REPORT.md").read_text()
    assert "expected  SKIPPED [1] dev/tests/migration/test_migrate_specs.py:3" in report
    assert "live      SKIPPED [1] dev/tests/contract/test_live.py:2" in report
    assert "| check.sh | FAIL | 1 |" in report
    assert "summary:" not in report and "FAIL 1 failed" not in report
    console = capsys.readouterr().out.splitlines()
    step6 = next(i for i, ln in enumerate(console) if ln.startswith(" 6 check.sh"))
    assert "exit 1; 2 skip line(s), 0 unexpected" in console[step6]
    assert console[step6 + 1 : step6 + 6] == [f"      {line}" for line in summary]
    assert console[step6 + 6].startswith(" 7 ")


# --------------------------------------------------------------------------------- refusals


def cli(gw: GateWorld, profile: Path, *args: str, env: dict | None = None):
    """Run the tool's command line."""
    return subprocess.run(
        [sys.executable, str(TOOL), "--repo", str(gw.src), "--profile", str(profile), *args],
        capture_output=True, text=True, env=env or os.environ.copy(), check=False,
    )  # fmt: skip


def test_gate_refusals(gw: GateWorld, digests: Path, tmp_path: Path) -> None:
    """Missing HACF_PY310, export, tag or digests exit 2 before any step."""
    profile = gw.profile()
    args = [
        "gate",
        "--version",
        "0.1.0",
        "--digests",
        str(digests),
        "--report-root",
        str(tmp_path / "r"),
    ]
    proc = cli(gw, profile, *args)
    assert proc.returncode == 2 and "v0.1.0 is missing" in proc.stderr
    gw.build("0.1.0", profile)
    env = {k: v for k, v in os.environ.items() if k != "HACF_PY310"}
    proc = cli(gw, profile, *args, env=env)
    assert proc.returncode == 2 and "HACF_PY310 is not set" in proc.stderr
    proc = cli(gw, profile, "gate", "--version", "0.1.0", "--digests", str(tmp_path / "none"))
    assert proc.returncode == 2 and "digest file is missing" in proc.stderr
    g(gw.src, "update-ref", "-d", "refs/heads/public/main")
    proc = cli(gw, profile, *args)
    assert proc.returncode == 2 and "public/main is missing" in proc.stderr
    assert not (tmp_path / "r").exists()


def remake_export(gw: GateWorld, parent: str) -> str:
    """Re-make the 0.2.0 export on `parent` (same tree); point its tag and public/main at it."""
    tree = g(gw.src, "rev-parse", "v0.2.0^{tree}")
    env = clean_env(
        GIT_AUTHOR_NAME=NAME,
        GIT_AUTHOR_EMAIL=BASE_EMAIL,
        GIT_COMMITTER_NAME=NAME,
        GIT_COMMITTER_EMAIL=BASE_EMAIL,
    )
    commit = g(gw.src, "commit-tree", tree, "-p", parent, "-m", "re-made", env=env)
    text = f"object {commit}\ntype commit\ntag v0.2.0\ntagger {NAME} <{BASE_EMAIL}> 1 +0000\n\nx\n"
    tag = subprocess.run(
        ["git", "-C", str(gw.src), "mktag"],
        input=text,
        text=True,
        capture_output=True,
        env=clean_env(),
        check=True,
    ).stdout.strip()
    g(gw.src, "update-ref", "refs/tags/v0.2.0", tag)
    g(gw.src, "update-ref", "refs/heads/public/main", commit)
    return commit


def test_export_on_a_private_parent_fails_shape(
    gw: GateWorld, digests: Path, tmp_path: Path
) -> None:
    """An export whose parent is not the previous export fails `shape`; nothing is cloned."""
    profile = gw.profile()
    build_both(gw, profile)
    remake_export(gw, gw.side)
    runner = FakeRunner()
    code, report_dir = run_gate(gw, profile, "0.2.0", digests, tmp_path / "r", runner)
    assert code == 1
    assert_stopped_before_clone(report_dir, runner, "shape")
    previous = g(gw.src, "rev-parse", "v0.1.0^{commit}")
    assert f"parents ['{gw.side}'] are not [{previous}]" in (report_dir / "REPORT.md").read_text()


def test_clone_step_fails_on_a_private_commit(gw: GateWorld, digests: Path, tmp_path: Path) -> None:
    """Step 5 alone: an export chained on a private commit brings it into the clone and fails."""
    profile_path = gw.profile()
    build_both(gw, profile_path)
    export = remake_export(gw, gw.side)
    profile = rx.load_profile(profile_path)
    exports = {"0.1.0": g(gw.src, "rev-parse", "v0.1.0^{commit}"), "0.2.0": export}
    run = rx.GateRun(
        repo=gw.src,
        profile=profile,
        index=1,
        release=profile.releases[1],
        export=export,
        tag=g(gw.src, "rev-parse", "v0.2.0"),
        tree=g(gw.src, "rev-parse", "v0.2.0^{tree}"),
        report_dir=tmp_path,
        work=tmp_path / "work",
        runner=FakeRunner(),
        py310="x",
        digests={},
        exports=exports,
    )
    out = rx.StepOutcome(5, "clone")
    rx.step_clone(run, out)
    assert out.verdict == "FAIL"
    assert "commit(s) outside the base history and the exports" in out.note
    assert f"  private: {gw.side}" in out.log
    assert run.clone is None


# ----------------------------------------------------------------- redaction, steps 7 and 10


def forge_export(gw: GateWorld, message: str, extra_path: str | None = None) -> None:
    """Replace v0.1.0 with an export whose commit and tag messages are `message`.

    With `extra_path`, the export's tree also holds a file at that path name.
    """
    good = g(gw.src, "rev-parse", "v0.1.0^{commit}")
    tree = g(gw.src, "rev-parse", f"{good}^{{tree}}")
    if extra_path:
        index = gw.tmp / "forge-index"
        env = clean_env(GIT_INDEX_FILE=str(index))
        g(gw.src, "read-tree", tree, env=env)
        blob = subprocess.run(
            ["git", "-C", str(gw.src), "hash-object", "-w", "--stdin"],
            input="clean\n", text=True, capture_output=True, env=clean_env(), check=True,
        ).stdout.strip()  # fmt: skip
        g(gw.src, "update-index", "--add", "--cacheinfo", f"100644,{blob},{extra_path}", env=env)
        tree = g(gw.src, "write-tree", env=env)
    env = clean_env(GIT_AUTHOR_NAME=NAME, GIT_AUTHOR_EMAIL=BASE_EMAIL,
                    GIT_COMMITTER_NAME=NAME, GIT_COMMITTER_EMAIL=BASE_EMAIL)  # fmt: skip
    commit = g(gw.src, "commit-tree", tree, "-p", gw.base, "-m", message, env=env)
    tag_text = (
        f"object {commit}\ntype commit\ntag v0.1.0\ntagger {NAME} <{BASE_EMAIL}> 1 +0000\n\n"
        f"{message}\n"
    )
    tag = subprocess.run(
        ["git", "-C", str(gw.src), "mktag"],
        input=tag_text, text=True, capture_output=True, env=clean_env(), check=True,
    ).stdout.strip()  # fmt: skip
    g(gw.src, "update-ref", "refs/tags/v0.1.0", tag)
    g(gw.src, "update-ref", "refs/heads/public/main", commit)


def fake_digests(tmp_path: Path) -> Path:
    """A digest file holding the fake address's real digest, labelled `fake`."""
    path = tmp_path / "fake-digests.sha256"
    path.write_text(f"{sha(FAKE_ADDRESS.lower())} fake\n")
    return path


def written_text(report_dir: Path, capsys: pytest.CaptureFixture[str]) -> dict[str, str]:
    """Every file the gate wrote under its report dir, plus its stdout and stderr."""
    out = {str(p): p.read_text(errors="replace") for p in report_dir.rglob("*") if p.is_file()}
    captured = capsys.readouterr()
    out["<stdout>"], out["<stderr>"] = captured.out, captured.err
    return out


@pytest.mark.parametrize("where", ["message", "path name"])
def test_private_address_reaches_no_output_report_or_log(
    gw: GateWorld, tmp_path: Path, capsys: pytest.CaptureFixture[str], where: str
) -> None:
    """A private address in the messages or a path name is written nowhere (R9).

    The scan fails; stdout, REPORT.md and every log carry the digest label in its place, and the
    address appears in none of them, whether it sits in the commit and tag messages or in a path.
    """
    profile = gw.profile()
    gw.build("0.1.0", profile)
    if where == "message":
        forge_export(gw, f"HACF v0.1.0\n\nThanks to {FAKE_ADDRESS} for the report.")
    else:
        forge_export(gw, "HACF v0.1.0", extra_path=f"docs/{FAKE_ADDRESS}.txt")
    runner = FakeRunner()
    code, report_dir = run_gate(
        gw, profile, "0.1.0", fake_digests(tmp_path), tmp_path / "r", runner
    )
    assert code == 1
    assert_stopped_before_clone(report_dir, runner, "scan")
    texts = written_text(report_dir, capsys)
    assert len(texts) == 2 + 1 + len(rx.STEPS)  # both streams, REPORT.md, one log per step
    for name, text in texts.items():
        assert FAKE_ADDRESS.lower() not in text.lower(), name
    placeholder = "<private address (digest fake)>"
    report = (report_dir / "REPORT.md").read_text()
    scan_log = (report_dir / "logs" / "01-scan.log").read_text()
    if where == "message":
        assert f"Thanks to {placeholder} for the report." in report  # commit and tag objects
        assert report.count(placeholder) == 2
        assert placeholder in (report_dir / "logs" / "04-messages.log").read_text()
        assert "private address (digest fake)  <commit object>" in scan_log
        assert "private address (digest fake)  <tag object>" in scan_log
    else:
        # the token runs on through `.txt` (an address-shaped domain), so it all goes
        assert f"docs/{placeholder} (path name)" in scan_log


def test_redact_replaces_only_private_addresses() -> None:
    """The one redaction pass: digest matches (any case, glued) go, other addresses stay."""
    saved = dict(rx._REDACT)
    try:
        rx._REDACT.clear()
        assert rx.redact(f"x {FAKE_ADDRESS}") == f"x {FAKE_ADDRESS}"  # no digests loaded
        rx._REDACT[sha(FAKE_ADDRESS.lower())] = "fake"
        text = f"<{FAKE_ADDRESS.upper()}>, a{FAKE_ADDRESS}b and dev@example.org"
        assert rx.redact(text) == (
            "<<private address (digest fake)>>, <private address (digest fake)> and dev@example.org"
        )
    finally:
        rx._REDACT.clear()
        rx._REDACT.update(saved)


def test_live_step_never_passes_on_the_shipped_verdict(
    gw: GateWorld, digests: Path, tmp_path: Path
) -> None:
    """Step 10 deletes the shipped verdict file first; a skipped live test is a FAIL."""
    profile = gw.profile()
    build_both(gw, profile)
    skipped = FakeRunner(shipped_verdict=True, live_output="1 skipped in 0.01s")
    code, report_dir = run_gate(gw, profile, "0.1.0", digests, tmp_path / "a", skipped)
    assert code == 1
    assert skipped.verdict_before_live is False  # removed before the run
    report = (report_dir / "REPORT.md").read_text()
    assert "| 10 | codegraph-live | FAIL | 0 |" in report
    assert "the live test was skipped" in report

    silent = FakeRunner(shipped_verdict=True, live_output="no tests ran in 0.01s")
    code, report_dir = run_gate(gw, profile, "0.1.0", digests, tmp_path / "b", silent)
    assert code == 1
    assert "no `1 passed` in the output" in (report_dir / "REPORT.md").read_text()

    passed = FakeRunner(shipped_verdict=True)
    code, report_dir = run_gate(gw, profile, "0.1.0", digests, tmp_path / "c", passed)
    assert code == 0, (report_dir / "REPORT.md").read_text()
    assert passed.verdict_before_live is False


def _ruff_run(tmp_path: Path, repo: Path, base: str, export: str, runner) -> SimpleNamespace:
    """The slice of GateRun that step_ruff reads."""
    return SimpleNamespace(
        clone=repo,
        export=export,
        profile=SimpleNamespace(base=base),
        work=tmp_path / "work",
        runner=runner,
        heavy_env=lambda step: {},
    )


def test_ruff_with_no_tracked_python_file_fails(tmp_path: Path) -> None:
    """Step 7 is non-vacuous (F1, F2): a clone with no tracked .py is a FAIL, ruff unrun."""
    repo = tmp_path / "clone"
    g(tmp_path, "init", "-q", "-b", "main", str(repo))
    write(repo, "README.md", "one\n")
    base = commit_all(repo, "base", DATES["base"])
    write(repo, "README.md", "two\n")
    export = commit_all(repo, "docs only", DATES["0.1.0"])
    runner = FakeRunner()
    out = rx.StepOutcome(7, "ruff")
    rx.step_ruff(_ruff_run(tmp_path, repo, base, export, runner), out)
    assert out.verdict == "FAIL"
    assert "vacuous: 0 tracked .py files" in out.note
    assert runner.calls == []


def test_ruff_lints_a_python_file_unchanged_since_the_base(tmp_path: Path) -> None:
    """Step 7 lints every tracked .py (F2): one unchanged since the base is still linted."""
    repo = tmp_path / "clone"
    g(tmp_path, "init", "-q", "-b", "main", str(repo))
    write(repo, "app.py", "X = 1\n")
    base = commit_all(repo, "base", DATES["base"])
    write(repo, "README.md", "docs\n")
    export = commit_all(repo, "docs only", DATES["0.1.0"])
    runner = FakeRunner()
    out = rx.StepOutcome(7, "ruff")
    rx.step_ruff(_ruff_run(tmp_path, repo, base, export, runner), out)
    ruff = [c[0] for c in runner.calls if c[0][:4] == ["uv", "run", "--locked", "ruff"]]
    assert len(ruff) == 2
    for argv in ruff:
        assert argv[-3:] == ["--force-exclude", "--", "app.py"]
    assert out.note == "1 tracked .py file(s)"


# ----------------------------------------------------- step 9: code-graph state by meaning (R66)

STORE_SCHEMA = {
    "projects": "CREATE TABLE projects (name TEXT PRIMARY KEY, indexed_at TEXT, root_path TEXT)",
    "file_hashes": "CREATE TABLE file_hashes (project TEXT, rel_path TEXT, sha256 TEXT)",
    "nodes": (
        "CREATE TABLE nodes (id INTEGER PRIMARY KEY, project TEXT, label TEXT, name TEXT, "
        "qualified_name TEXT, file_path TEXT, start_line INTEGER, end_line INTEGER)"
    ),
    "edges": (
        "CREATE TABLE edges (id INTEGER PRIMARY KEY AUTOINCREMENT, project TEXT, "
        "source_id INTEGER, target_id INTEGER, type TEXT)"
    ),
}
STORE_FILES = (("app.txt", "a" * 64), ("lib.py", "b" * 64))
STORE_NODES = (
    (1, "Project", "<P>", "", 0, 0),
    (2, "Function", "<P>.lib.f", "lib.py", 1, 3),
    (3, "Function", "<P>.lib.g", "lib.py", 5, 9),
)
STORE_EDGES = ((1, 2, "DEFINES"), (1, 3, "DEFINES"), (2, 3, "CALLS"))


def cg_state(project: Path, name: str, at: str, nodes: int = 3, app: str = "same\n") -> None:
    """An installed project with the code-graph state an indexed install writes."""
    state = rx.CODEGRAPH_STATE
    write(project, "app.txt", app)
    record = {"last_index": {"at": at, "head": None}, "project_name": name,
              "version": rx.CBM_STORE_VERSION}  # fmt: skip
    write(project, state + "project.json", json.dumps(record))
    index = {"project": name, "status": "indexed", "nodes": nodes, "edges": 3,
             "not_indexed_files_count": 0, "skipped_count": 0, "parse_partial_count": 0,
             "parse_unusable_count": 0}  # fmt: skip
    write(project, state + "raw/index.json", json.dumps(index))
    listing = {"projects": [{"name": name, "root_path": str(project)}], "total": 1}
    write(project, state + "raw/list_projects.json", json.dumps(listing))


def cbm_store(cache: Path, name: str, *, files=STORE_FILES, nodes=STORE_NODES,
              edges=STORE_EDGES, similar=(), tables=rx.CBM_STORE_TABLES) -> Path:  # fmt: skip
    """A small CBM store with the four tables MR 4 reads; `<P>` stands for the project name."""
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / f"{name}.db"
    con = sqlite3.connect(path)
    for table in tables:
        con.execute(STORE_SCHEMA[table])
    if "projects" in tables:
        con.execute("INSERT INTO projects VALUES (?, ?, ?)", (name, "t", f"/x/{name}"))
    if "file_hashes" in tables:
        con.executemany("INSERT INTO file_hashes VALUES (?, ?, ?)",
                        [(name, rel, digest) for rel, digest in files])  # fmt: skip
    if "nodes" in tables:
        rows = [(i, name, label, q.replace("<P>", name), q.replace("<P>", name), f, s, e)
                for i, label, q, f, s, e in nodes]  # fmt: skip
        con.executemany("INSERT INTO nodes VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
    if "edges" in tables:
        rows = [(name, s, t, kind) for s, t, kind in edges]
        rows += [(name, s, t, rx.CBM_SIMILARITY_EDGE) for s, t in similar]
        con.executemany(
            "INSERT INTO edges (project, source_id, target_id, type) VALUES (?, ?, ?, ?)", rows
        )
    con.commit()
    con.close()
    return path


def cg_pair(tmp_path: Path, export: dict | None = None, source: dict | None = None,
            state: dict | None = None):  # fmt: skip
    """Two installs (export, source) with their own state and stores; return (a, b, cache)."""
    a, b = tmp_path / "export" / "project", tmp_path / "source" / "project"
    cache = tmp_path / "cache" / "cbm_cache_dir"
    for project, side, store in ((a, "export", export or {}), (b, "source", source or {})):
        name = f"w-{side}-project"
        cg_state(project, name, f"2026-01-0{1 if side == 'export' else 2}T00:00:00+00:00",
                 **(state or {}).get(side, {}))  # fmt: skip
        cbm_store(cache, name, **store)
    return a, b, cache


def compare(a: Path, b: Path, cache: Path | None, tmp_path: Path) -> tuple[list[str], list[str]]:
    """The new comparison with step 9's arguments; return (differences, log)."""
    log: list[str] = []
    return rx.compare_installs(a, b, {}, cache, tmp_path / "step", log), log


def test_g1_codegraph_state_and_similarity_edges_alone_are_equal(tmp_path: Path) -> None:
    """(g1) Installs differ only in `.agents/state/codegraph/**`; stores only in similarity edges.

    No difference; the log names both semantic counts. The stores are read from copies, and
    the pin MR 4 depends on is the backend's pinned version.
    """
    a, b, cache = cg_pair(tmp_path, {"similar": ((2, 3), (3, 2))}, {"similar": ((2, 3), (1, 2))})
    stores = {p: p.read_bytes() for p in cache.iterdir()}
    diff, log = compare(a, b, cache, tmp_path)
    assert diff == []
    assert "SEMANTICALLY_RELATED edges: 1 only in the export, 1 only in the source " \
        "(path-derived; logged, never a difference)" in log  # fmt: skip
    assert "code-graph store (export): 2 file hashes, 3 nodes, 3 structural edges, " \
        "2 SEMANTICALLY_RELATED" in log  # fmt: skip
    assert {p: p.read_bytes() for p in cache.iterdir()} == stores
    copies = sorted(p.name for p in (tmp_path / "step" / "cbm-stores").rglob("*.db"))
    assert copies == ["w-export-project.db", "w-source-project.db"]
    pins = TOOL.parents[1] / ".agents/config/code_graph.defaults.yml"
    defaults = yaml.safe_load(pins.read_text())
    assert defaults["backends"]["codebase-memory-mcp"]["pinned_version"] == rx.CBM_STORE_VERSION


def test_g2_a_structural_edge_difference_is_a_difference(tmp_path: Path) -> None:
    """(g2) One CALLS edge differs: a difference."""
    a, b, cache = cg_pair(tmp_path, {"edges": ((1, 2, "DEFINES"), (1, 3, "DEFINES"),
                                               (3, 2, "CALLS"))})  # fmt: skip
    diff, _ = compare(a, b, cache, tmp_path)
    assert diff == [
        "code-graph index differs: structural edges (1 only in the export, 1 only in the source)"
    ]


def test_g3_a_file_hash_difference_is_a_difference(tmp_path: Path) -> None:
    """(g3) One file_hashes sha differs: a difference."""
    a, b, cache = cg_pair(tmp_path, {"files": (("app.txt", "a" * 64), ("lib.py", "c" * 64))})
    diff, _ = compare(a, b, cache, tmp_path)
    assert diff == [
        "code-graph index differs: file hashes (1 only in the export, 1 only in the source)"
    ]


def test_g4_a_node_count_difference_is_a_difference(tmp_path: Path) -> None:
    """(g4) The node count differs, in index.json and in the store: each is a difference."""
    more = (*STORE_NODES, (4, "Function", "<P>.lib.h", "lib.py", 11, 12))
    a, b, cache = cg_pair(tmp_path, {"nodes": more}, state={"export": {"nodes": 4}})
    diff, _ = compare(a, b, cache, tmp_path)
    assert diff == [
        "code-graph index differs: nodes 4 (export) != 3 (source)",
        "code-graph index differs: nodes (1 only in the export, 0 only in the source)",
    ]


@pytest.mark.parametrize("broken", ["missing", "no edges table"])
def test_g5_an_unreadable_store_cannot_be_verified(tmp_path: Path, broken: str) -> None:
    """(g5) A missing store, or one without `edges`: `cannot verify`, so the step fails closed."""
    a, b, cache = cg_pair(tmp_path)
    store = cache / "w-export-project.db"
    store.unlink()
    if broken == "no edges table":
        cbm_store(cache, "w-export-project", tables=("projects", "file_hashes", "nodes"))
    diff, _ = compare(a, b, cache, tmp_path)
    assert len(diff) == 1
    assert diff[0].startswith("cannot verify the code-graph index: the export store: ")
    want = "no store w-export-project.db" if broken == "missing" else "lacks table(s) edges"
    assert want in diff[0]


def test_g6_a_file_outside_the_codegraph_state_still_differs(tmp_path: Path) -> None:
    """(g6) The exclusion is narrow: a file outside `.agents/state/codegraph/` still differs."""
    a, b, cache = cg_pair(tmp_path, state={"export": {"app": "changed\n"}})
    diff, _ = compare(a, b, cache, tmp_path)
    assert diff == ["differs: app.txt"]


def test_g7_codegraph_state_in_one_install_only_is_a_difference(tmp_path: Path) -> None:
    """(g7) Only one install has `.agents/state/codegraph/`: a difference."""
    a, b, cache = cg_pair(tmp_path)
    shutil.rmtree(b / ".agents/state/codegraph")
    diff, _ = compare(a, b, cache, tmp_path)
    assert diff == ["only in the export install: .agents/state/codegraph/"]


class CodegraphRunner(FakeRunner):
    """A FakeRunner whose code-graph installs also index: state, and a store in CBM_CACHE_DIR."""

    def run(self, argv, cwd, env):
        """Act as FakeRunner, then write what the index writes for a code-graph install."""
        result = super().run(argv, cwd, env)
        if (
            argv[0] == "bash"
            and argv[1].endswith("HACF-install.sh")
            and ("--install-code-graph" in argv)
        ):
            side = Path(cwd).parent.name
            name = f"w-{side}-project"
            cg_state(Path(cwd), name, f"at-{side}")
            similar = ((2, 3),) if side == "export" else ((3, 2),)
            cbm_store(Path(env["CBM_CACHE_DIR"]), name, similar=similar)
        return result


def test_g8_step_8_is_unchanged_and_step_9_compares_the_stores(
    gw: GateWorld, digests: Path, tmp_path: Path
) -> None:
    """(g8) Step 8's plain pair is compared as before; step 9 passes its CBM_CACHE_DIR (MR 4-5).

    The existing install tests pass untouched; this one adds the step-9 wiring end to end.
    """
    profile = gw.profile()
    build_both(gw, profile)
    code, report_dir = run_gate(gw, profile, "0.2.0", digests, tmp_path / "r", CodegraphRunner())
    assert code == 0
    plain = (report_dir / "logs" / "08-install-plain.log").read_text()
    assert "equal except the named record fields" in plain
    assert "code-graph store" not in plain
    log = (report_dir / "logs" / "09-install-codegraph.log").read_text()
    assert "SEMANTICALLY_RELATED edges: 1 only in the export, 1 only in the source" in log
    assert ".agents/state/codegraph/** (per-machine, gitignored; compared by MR 3-4 instead)" in log
    report = (report_dir / "REPORT.md").read_text()
    assert "installs equal, code-graph indexes equal (structure), three MCP entries, doctor ok" \
        in report  # fmt: skip


# ----------------------------------------------------- step 11, openrig-contract (OPR.99.0.14.1)

DECLARED = ("0.6.13", "0.7.16", "0.7.21", "0.7.23")


def contract_text(*entries: tuple[str, str]) -> str:
    """A contract file holding `(version, accepted_by)` entries."""
    rows = [{"version": v, "accepted_by": by} for v, by in entries]
    return yaml.safe_dump({"schema": 1, "accepted": rows})


def contract_step(tmp_path: Path, text: str | None, declared: tuple[str, ...] | None = DECLARED):
    """Run step 11 on a repository whose export commit holds `text` as the contract."""
    repo = tmp_path / "contract-repo"
    g(tmp_path, "init", "-q", "-b", "main", str(repo))
    write(repo, "README.md", "export\n")
    if text is not None:
        write(repo, ".agents/config/openrig_contract.yml", text)
    export = commit_all(repo, "export", DATES["0.1.0"])
    run = SimpleNamespace(
        repo=repo, export=export, release=SimpleNamespace(openrig_accepted=declared)
    )
    out = rx.StepOutcome(11, "openrig-contract")
    dict(rx.STEPS)["openrig-contract"](run, out)
    return out


def people(*versions: str) -> list[tuple[str, str]]:
    """Person entries."""
    return [(v, "person") for v in versions]


def test_contract_step_passes_on_the_declared_set_in_any_order(tmp_path: Path) -> None:
    """The export's accepted versions equal the declared list, both sorted by version."""
    out = contract_step(tmp_path, contract_text(*people(*reversed(DECLARED))))
    assert (out.verdict, out.note) == (
        "PASS", "accepted = declared: 0.6.13, 0.7.16, 0.7.21, 0.7.23")  # fmt: skip


@pytest.mark.parametrize("versions", [(*DECLARED, "0.7.24"), DECLARED[:-1], (*DECLARED, "0.7.23")])
def test_contract_step_fails_on_another_or_duplicated_set(
    tmp_path: Path, versions: tuple[str, ...]
) -> None:
    """An extra, a missing or a duplicated version fails."""
    out = contract_step(tmp_path, contract_text(*people(*versions)))
    assert out.verdict == "FAIL"
    assert out.note.startswith("accepted ") and " != declared " in out.note


def test_contract_step_fails_on_a_watcher_entry(tmp_path: Path) -> None:
    """A watcher acceptance never ships, even when its version is declared."""
    text = contract_text(*people(*DECLARED[:-1]), ("0.7.23", "watcher"))
    out = contract_step(tmp_path, text)
    assert (out.verdict, out.note) == ("FAIL", "watcher entry 0.7.23 in the export")


@pytest.mark.parametrize(
    "text",
    [
        None,
        "- 0.7.23\n",
        "schema: 1\n",
        "accepted: 0.7.23\n",
        "accepted: [0.7.23]\n",
        "accepted: [{accepted_by: person}]\n",
        "accepted: [\n",
    ],
)
def test_contract_step_fails_without_a_readable_contract(tmp_path: Path, text: str | None) -> None:
    """No file, no mapping, no accepted list, or a broken entry."""
    out = contract_step(tmp_path, text)
    assert (out.verdict, out.note) == ("FAIL", "no readable contract in the export")


def test_contract_step_does_not_check_a_row_without_the_field(tmp_path: Path) -> None:
    """Rows below 0.6.7 declare nothing."""
    out = contract_step(tmp_path, None, None)
    assert (out.verdict, out.note) == ("PASS", "not checked (no openrig_accepted on this row)")
