"""Deterministic tests for hyper_codegraph.py (S04) and the hypergraph_updater.py enrichment.

No test runs codebase-memory-mcp. A stub backend, a Python script in the test's temp directory,
stands in for it through a recorded `exe_argv_prefix`: it logs each argv it receives and prints
the answer the test chose for that tool. The stub's payloads are test inputs shaped like the
0.12.1 captures; test_normalize_fixture checks the normalizers against the captures themselves
(dev/tests/fixtures/recorded/S04/, written by the consented contract test). Every repository
and state file lives under tmp_path.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import hyper_codegraph
import pytest
import yaml
from conftest import FIXTURES_DIR, REPO_ROOT, SCRIPTS_DIR, GitRepo
from fixtures.recorded_loader import load, paths

SCRIPT = SCRIPTS_DIR / "hyper_codegraph.py"
UPDATER = SCRIPTS_DIR / "hypergraph_updater.py"
DEFAULTS = REPO_ROOT / hyper_codegraph.DEFAULTS_REL
CONTRACT_DIR = FIXTURES_DIR / "codegraph-0.12.1"
SYNTHETIC = FIXTURES_DIR / "synthetic" / "S04"
CBM = hyper_codegraph.BACKEND_CBM
UVX = ["uvx", "codebase-memory-mcp@0.12.1"]
PROJECT = "proj-a"
LEDGER_REL = "spec/compiled/architecture.yml"
FALLBACK_TAIL = ": blast radius from git diff plus files: globs"

STUB = """\
import json
import sys
from pathlib import Path

here = Path(__file__).resolve().parent
args = sys.argv[1:]
with open(here / "argv.jsonl", "a", encoding="utf-8") as log:
    log.write(json.dumps(args) + "\\n")
key = args[1] if args[:1] == ["cli"] and len(args) > 1 else (args[0] if args else "")
answers = json.loads((here / "answers.json").read_text(encoding="utf-8"))
answer = answers.get(key, {"stderr": "stub: no answer for " + key + "\\n", "exit": 3})
sys.stdout.write(answer.get("stdout", ""))
sys.stderr.write(answer.get("stderr", ""))
sys.exit(answer.get("exit", 0))
"""

LEDGER = """\
schema: hacf.ledger/v2
nodes:
  - id: core_mod
    dimension: Module
    status: clean
    associated_file: pkg/core.py
    files: ['pkg/core.py']
    description: >-
      Defines helper().
    inputs: []
    outputs: []
    edges: {depends_on: [], implements: [], member_of: []}
  - id: caller_mod
    dimension: Module
    status: clean
    associated_file: pkg/caller.py
    files: ['pkg/caller.py']
    description: >-
      Calls helper(); no ledger edge links it to core_mod.
    inputs: []
    outputs: []
    edges: {depends_on: [], implements: [], member_of: []}
"""


def _detect_changes(**changes: object) -> dict:
    """Return a detect_changes payload shaped like capture 15: core.py changed, caller impacted."""
    payload: dict = {
        "base": "main",
        "merge_base": "0" * 40,
        "direction": "inbound",
        "changed_total": 1,
        "changed_has_more": False,
        "changed_files": ["pkg/core.py"],
        "impacted_total": 1,
        "impacted": [
            {
                "qn": f"{PROJECT}.pkg.caller.use",
                "label": "Function",
                "file": "pkg/caller.py",
                "hop": 1,
            }
        ],
        "impacted_has_more": False,
        "module_has_more": False,
        "impacted_modules": [{"module": "pkg/caller.py", "count": 1}],
        "truncated": False,
    }
    payload.update(changes)
    return payload


def _trace(**changes: object) -> dict:
    """Return a trace_path payload shaped like capture 13, plus a group with an empty prefix."""
    callers: dict = {
        "qn_rule": hyper_codegraph.QN_RULE,
        "cols": ["name", "hop"],
        "groups": [
            {"qn_prefix": f"{PROJECT}.pkg.caller", "rows": [["use", 1]]},
            {"qn_prefix": "", "rows": [["main", 2]]},
        ],
    }
    callers.update(changes)
    return {"function": "helper", "direction": "inbound", "callers_total": 2, "callers": callers}


def _search(**changes: object) -> dict:
    """Return a search_graph payload shaped like capture 14."""
    payload: dict = {
        "qn_rule": hyper_codegraph.QN_RULE,
        "cols": ["name", "label", "lines", "in", "out"],
        "groups": [
            {
                "qn_prefix": f"{PROJECT}.pkg.core",
                "file": "pkg/core.py",
                "rows": [["helper", "Function", "4-6", 1, 0]],
            }
        ],
        "total": 1,
        "returned": 1,
        "has_more": False,
        "truncated": False,
    }
    payload.update(changes)
    return payload


def _list_projects(root: Path, **changes: object) -> dict:
    """Return a list_projects payload shaped like capture 10, naming root as PROJECT."""
    payload: dict = {
        "projects": [{"name": PROJECT, "root_path": str(root), "branch": "main"}],
        "total": 1,
        "offset": 0,
        "limit": 500,
        "returned": 1,
        "has_more": False,
    }
    payload.update(changes)
    return payload


class Stub:
    """A fake codebase-memory-mcp: logs its argv and prints the answer chosen per tool."""

    def __init__(self, directory: Path) -> None:
        """Write the stub script and an answer for `--version` into directory."""
        directory.mkdir(parents=True, exist_ok=True)
        self.dir = directory
        (directory / "stub.py").write_text(STUB, encoding="utf-8")
        self.answers: dict = {}
        self.answer("--version", stdout="codebase-memory-mcp 0.12.1\n")

    @property
    def prefix(self) -> list[str]:
        """The exe_argv_prefix that runs the stub."""
        return [sys.executable, str(self.dir / "stub.py")]

    def answer(
        self, tool: str, payload: object = None, *, stdout: str = "", exit_code: int = 0
    ) -> None:
        """Make the stub print payload (as JSON) or stdout for tool, then exit with exit_code."""
        text = json.dumps(payload) if payload is not None else stdout
        self.answers[tool] = {"stdout": text, "exit": exit_code}
        (self.dir / "answers.json").write_text(json.dumps(self.answers), encoding="utf-8")

    def calls(self) -> list[list[str]]:
        """Return every argv the stub received, oldest first."""
        log = self.dir / "argv.jsonl"
        if not log.exists():
            return []
        return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]

    def tools(self) -> list[str]:
        """Return the tool each call named (`--version` for a version call)."""
        return [args[1] if args[:1] == ["cli"] else args[0] for args in self.calls()]


def _scaffold(repo: GitRepo, hacf_yml: str | None = None) -> Path:
    """Commit a two-module package, its ledger and the shipped defaults; return the work tree.

    `.agents/state/` and `.agents/local/` are ignored, so neither the adapter's state nor the
    test's hacf.yml variants change what `--from-diff` sees.
    """
    repo.write("pkg/__init__.py", "")
    repo.write("pkg/core.py", "def helper(value):\n    return value * 2\n")
    repo.write(
        "pkg/caller.py", "from pkg.core import helper\n\n\ndef use(v):\n    return helper(v)\n"
    )
    repo.write(LEDGER_REL, LEDGER)
    repo.write(hyper_codegraph.DEFAULTS_REL, DEFAULTS.read_text(encoding="utf-8"))
    repo.write(".gitignore", ".agents/state/\n.agents/local/\n")
    repo.commit("code-graph fixture")
    if hacf_yml is not None:
        repo.write(hyper_codegraph.LOCAL_REL, hacf_yml)
    return repo.path


def _hacf_yml(backend: str = CBM, install_method: str | None = None) -> str:
    lines = ["code_graph:", f"  backend: {backend}"]
    if install_method is not None:
        lines.append(f"  install_method: {install_method}")
    return "\n".join(lines) + "\n"


def _state(root: Path, **fields: object) -> None:
    hyper_codegraph.write_state(root, dict(fields))


def _indexed_state(root: Path, stub: Stub, head: str | None = None) -> None:
    """Record the stub as the executable and PROJECT as indexed at head (default: HEAD)."""
    _state(
        root,
        exe_argv_prefix=stub.prefix,
        project_name=PROJECT,
        version="0.12.1",
        last_index={
            "head": head or hyper_codegraph.head_commit(root),
            "at": "2026-10-27T00:00:00+00:00",
        },
    )


def _git_only_path(tmp_path: Path) -> str:
    """Return a PATH holding git only, so codebase-memory-mcp and uvx cannot resolve."""
    bindir = tmp_path / "git-only-bin"
    bindir.mkdir(exist_ok=True)
    git = shutil.which("git")
    assert git is not None
    if not (bindir / "git").exists():
        (bindir / "git").symlink_to(git)
    return str(bindir)


@pytest.fixture
def run_spy(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Record every argv hyper_codegraph._run starts, still running it."""
    calls: list[list[str]] = []
    real = hyper_codegraph._run

    def spy(argv: list[str], *, cwd: Path | None, timeout: float) -> hyper_codegraph.Completed:
        calls.append(list(argv))
        return real(argv, cwd=cwd, timeout=timeout)

    monkeypatch.setattr(hyper_codegraph, "_run", spy)
    return calls


def _cli(capsys: pytest.CaptureFixture, *argv: str) -> tuple[int, dict | None, str]:
    """Run hyper_codegraph.main(argv) in-process; return (exit code, JSON or None, stderr)."""
    capsys.readouterr()
    code = hyper_codegraph.main(list(argv))
    out = capsys.readouterr()
    try:
        result = json.loads(out.out)
    except ValueError:
        result = None
    return code, result, out.err


def _updater(run_script, root: Path, rev: str = "HEAD", **env: str) -> tuple[dict, str, str]:
    """Run hypergraph_updater.py --from-diff rev --json in root; return (report, stdout, stderr)."""
    proc = run_script(UPDATER, LEDGER_REL, "--from-diff", rev, "--json", cwd=root, env=env)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout), proc.stdout, proc.stderr


# ---------------------------------------------------------------------------
# Backend selection and executable resolution
# ---------------------------------------------------------------------------


def test_backend_none_unsupported(
    tmp_path: Path, run_spy: list, capsys: pytest.CaptureFixture
) -> None:
    """No hacf.yml: every capability returns unsupported with exit 0 and runs no subprocess."""
    root = tmp_path / "project"
    (root / ".agents" / "config").mkdir(parents=True)
    shutil.copyfile(DEFAULTS, root / hyper_codegraph.DEFAULTS_REL)
    extra = {"symbol_impact": ["--symbol", "helper"], "search": ["--pattern", "help.*"]}
    for capability in hyper_codegraph.CAPABILITIES:
        argv = [capability, "--repo-root", str(root), "--json", *extra.get(capability, [])]
        code, result, _ = _cli(capsys, *argv)
        assert code == 0, capability
        assert result["status"] == "unsupported", capability
        assert result["backend"] == "none"
        assert result["warnings"] == [
            "code-graph backend is none; code graph unsupported" + FALLBACK_TAIL
        ]
    assert run_spy == []


def test_missing_binary_unavailable(
    tmp_git_repo: GitRepo,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    run_spy: list,
    capsys: pytest.CaptureFixture,
) -> None:
    """Backend selected, nothing on PATH, no consent: unavailable with the fallback line."""
    root = _scaffold(tmp_git_repo, _hacf_yml())
    monkeypatch.setenv("PATH", _git_only_path(tmp_path))
    args = {"symbol_impact": {"symbol": "helper"}, "search": {"pattern": "help.*"}}
    for capability in (
        "index",
        "status",
        "list_projects",
        "diff_impact",
        "symbol_impact",
        "search",
    ):
        result = hyper_codegraph.run_capability(
            capability, repo_root=root, **args.get(capability, {})
        )
        assert result["status"] == "unavailable", capability
        assert hyper_codegraph.fallback_line("unavailable") in result["warnings"]
        assert "not installed" in result["error"]
    code, result, _ = _cli(capsys, "index", "--repo-root", str(root), "--json")
    assert (code, result["status"]) == (0, "unavailable")
    # Nothing but git ran: no download, no install, no uvx.
    assert run_spy and all(argv[0] == "git" for argv in run_spy)


def test_exe_prefix_uvx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, run_spy: list) -> None:
    """The uvx prefix needs recorded consent: install_method uvx, or a recorded prefix."""
    monkeypatch.setenv("PATH", _git_only_path(tmp_path))
    root = tmp_path / "project"
    local = root / hyper_codegraph.LOCAL_REL
    local.parent.mkdir(parents=True)
    (root / ".agents" / "config").mkdir()
    shutil.copyfile(DEFAULTS, root / hyper_codegraph.DEFAULTS_REL)

    def prefix(text: str) -> list[str] | None:
        local.write_text(text, encoding="utf-8")
        return hyper_codegraph.resolve_prefix(hyper_codegraph.load_config(root))

    assert prefix(_hacf_yml(install_method="uvx")) == UVX
    for method in (None, "path", "uv-tool"):
        assert prefix(_hacf_yml(install_method=method)) is None, method
    # A prefix the state file records (the installer wrote it after consent) is used as is.
    _state(root, exe_argv_prefix=UVX)
    assert prefix(_hacf_yml()) == UVX
    (root / hyper_codegraph.STATE_REL).unlink()
    # An executable on PATH wins over the uvx consent.
    fake = Path(_git_only_path(tmp_path)) / "codebase-memory-mcp"
    fake.write_text("#!/bin/sh\necho 'codebase-memory-mcp 0.12.1'\n", encoding="utf-8")
    fake.chmod(0o755)
    assert prefix(_hacf_yml(install_method="uvx")) == [str(fake)]
    # Resolution only probes versions; it never runs uvx, so it never downloads anything.
    assert run_spy and all(argv[-1:] == ["--version"] and argv[0] != "uvx" for argv in run_spy)


def test_doctor_reports_chain_and_version(
    tmp_git_repo: GitRepo,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    """Doctor prints the layers, the chosen prefix, the version against the pin and the index."""
    # A skipped stub falls through to PATH: keep a host codebase-memory-mcp out of reach.
    monkeypatch.setenv("PATH", _git_only_path(tmp_path))
    root = _scaffold(tmp_git_repo, _hacf_yml())
    stub = Stub(tmp_path / "stub")
    _indexed_state(root, stub)
    code, report, _ = _cli(capsys, "doctor", "--repo-root", str(root), "--json")
    assert code == 0
    assert report["layers"] == {
        "defaults": hyper_codegraph.DEFAULTS_REL,
        "local": hyper_codegraph.LOCAL_REL,
        "state": hyper_codegraph.STATE_REL,
    }
    assert report["prefix"] == stub.prefix
    assert report["backend_version"] == report["pinned_version"] == "0.12.1"
    assert report["verified"] is True
    assert report["index"]["status"] == "fresh"
    assert report["warnings"] == []
    stub.answer("--version", stdout="codebase-memory-mcp 0.13.0\n")
    _, report, _ = _cli(capsys, "doctor", "--repo-root", str(root), "--json")
    assert report["prefix"] is None  # a 0.13.0 binary is skipped, not chosen
    assert report["warnings"] == [
        f"{CBM} 0.12.1 is required; found: state exe_argv_prefix {json.dumps(stub.prefix)}: "
        f"0.13.0. Install it with: uv tool install {CBM}==0.12.1, or record consent for uvx "
        "(install_method: uvx).",
        hyper_codegraph.fallback_line("unavailable"),
    ]


# ---------------------------------------------------------------------------
# Placeholders (R24.6)
# ---------------------------------------------------------------------------


def test_placeholder_validation(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    """Each placeholder rule accepts and refuses as schemas.md says; values fill whole elements."""
    root = str(tmp_path)
    good = {
        "repo_root": [root],
        "project": [PROJECT, "a", "Tmp-pytest.repo_2", "x" * 128],
        "symbol": ["helper", "pkg.core:Klass.$method<lambda>", "a-b", "s" * 256],
        "pattern": [".*Handler.*", "^use$", "p" * 256],
        "base": ["main", "feature/x-1", "0123456789abcdef0123456789abcdef01234567"],
    }
    bad = {
        "repo_root": ["relative/dir", str(tmp_path / "missing"), str(tmp_path / "f.txt")],
        "project": ["", "_x", ".x", "a b", "a/b", "x" * 129],
        "symbol": ["a b", "a;b", "s" * 257],
        "pattern": ["(", "[a-", "p" * 257],
        "base": ["HEAD~1", "a..b", "a b", "x.lock"],
    }
    (tmp_path / "f.txt").write_text("", encoding="utf-8")
    for name, values in good.items():
        for value in values:
            assert hyper_codegraph.validate_placeholders({name: value}) == {name: value}
    for name, values in bad.items():
        for value in values:
            with pytest.raises(hyper_codegraph.PlaceholderRefused):
                hyper_codegraph.validate_placeholders({name: value})
    # Through the CLI a refused value exits 2 and says so.
    code, _, err = _cli(capsys, "search", "--repo-root", root, "--project", "a b", "--pattern", "x")
    assert code == 2 and "refused: project value 'a b'" in err
    code, _, err = _cli(capsys, "status", "--repo-root", "relative/dir")
    assert code == 2 and "refused: repo_root" in err
    # A placeholder replaces one whole argv element, spaces and all, and nothing else.
    template = ["cli", "search_graph", "--project", "{project}", "--name-pattern", "{pattern}"]
    argv = hyper_codegraph.build_argv(["exe"], template, {"project": "p", "pattern": "a b|c"})
    assert argv == ["exe", "cli", "search_graph", "--project", "p", "--name-pattern", "a b|c"]
    for partial in (["--project={project}"], ["x{project}"], ["{project}{base}"], ["{nope}"]):
        with pytest.raises(hyper_codegraph.CodeGraphError):
            hyper_codegraph.check_template(partial, "test")


def test_placeholder_leading_dash_refused(
    tmp_git_repo: GitRepo, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """Values starting with '-' exit 2 before any subprocess runs (the stub's log stays empty)."""
    root = _scaffold(tmp_git_repo, _hacf_yml())
    stub = Stub(tmp_path / "stub")
    _indexed_state(root, stub)
    base = ["--repo-root", str(root)]
    cases = [
        ["status", *base, "--project=--format"],
        ["status", *base, "--project=-x"],
        ["search", *base, "--project=-x", "--pattern=x"],
        ["symbol_impact", *base, "--symbol=-helper"],
        ["symbol_impact", *base, "--symbol=--direction"],
        ["search", *base, "--pattern=-x"],
        ["search", *base, "--pattern=--format"],
        ["diff_impact", *base, "--base=-main"],
    ]
    for argv in cases:
        code, _, err = _cli(capsys, *argv)
        assert code == 2, argv
        assert "starts with '-'" in err and "nothing was run" in err, argv
    assert stub.calls() == []


# ---------------------------------------------------------------------------
# Defaults, state and delete
# ---------------------------------------------------------------------------


def test_embedded_defaults_match_file() -> None:
    """The embedded defaults equal the shipped file, which carries the R31/R33 templates."""
    data = yaml.safe_load(DEFAULTS.read_text(encoding="utf-8"))
    assert data == hyper_codegraph.EMBEDDED_DEFAULTS
    caps = data["backends"][CBM]["capabilities"]
    assert "--format" not in caps["index"] and "--format" not in caps["delete"]
    assert caps["list_projects"][2:4] == ["--limit", "1000"]
    assert data["backends"]["none"] == {"capabilities": {}}


def test_missing_defaults_uses_embedded(tmp_path: Path, run_script) -> None:
    """Without the defaults file the embedded copy is used and stderr asks for the installer."""
    defaults, source = hyper_codegraph.load_defaults(tmp_path)
    assert source == "embedded" and defaults == hyper_codegraph.EMBEDDED_DEFAULTS
    proc = run_script(SCRIPT, "status", "--repo-root", str(tmp_path), cwd=tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert "run HACF-install.sh to complete the upgrade" in proc.stderr
    assert proc.stdout.startswith("status: unsupported\n")


def test_delete_uses_recorded_project(
    tmp_git_repo: GitRepo, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """Delete passes the state file's project_name, then forgets it; no state, nothing to do."""
    root = _scaffold(tmp_git_repo, _hacf_yml())
    stub = Stub(tmp_path / "stub")
    stub.answer("delete_project", {"project": PROJECT, "status": "deleted"})
    _indexed_state(root, stub)
    code, result, _ = _cli(capsys, "delete", "--repo-root", str(root), "--json")
    assert (code, result["status"], result["project"]) == (0, "ok", PROJECT)
    assert stub.calls() == [["--version"], ["cli", "delete_project", "--project", PROJECT]]
    state, _ = hyper_codegraph.load_state(root)
    assert state == {"exe_argv_prefix": stub.prefix, "version": "0.12.1"}
    # Nothing recorded: nothing to delete, exit 0, no backend call.
    code, result, _ = _cli(capsys, "delete", "--repo-root", str(root), "--json")
    assert (code, result["status"]) == (0, "ok")
    assert any("nothing to delete" in warning for warning in result["warnings"])
    assert len(stub.calls()) == 2


def test_index_records_project(tmp_git_repo: GitRepo, tmp_path: Path) -> None:
    """Index runs the backend, finds the repo's project via list_projects and records it."""
    root = _scaffold(tmp_git_repo, _hacf_yml())
    stub = Stub(tmp_path / "stub")
    stub.answer("index_repository", {"project": PROJECT, "status": "indexed"})
    other = {"name": "other", "root_path": str(tmp_path), "branch": "main"}
    listing = _list_projects(root)
    listing["projects"].append(other)
    stub.answer("list_projects", listing)
    _state(root, exe_argv_prefix=stub.prefix)
    result = hyper_codegraph.run_capability("index", repo_root=root)
    assert (result["status"], result["project"]) == ("ok", PROJECT)
    assert stub.calls()[:3] == [
        ["--version"],
        ["cli", "index_repository", "--repo-path", str(root)],
        ["cli", "list_projects", "--limit", "1000", "--format", "json"],
    ]
    state, _ = hyper_codegraph.load_state(root)
    assert state["project_name"] == PROJECT and state["version"] == "0.12.1"
    assert state["last_index"]["head"] == hyper_codegraph.head_commit(root)


def test_stale_index_after_commit(tmp_git_repo: GitRepo, tmp_path: Path) -> None:
    """An index recorded at an older commit makes queries stale_index without a backend call."""
    root = _scaffold(tmp_git_repo, _hacf_yml())
    stub = Stub(tmp_path / "stub")
    _indexed_state(root, stub)
    tmp_git_repo.write("pkg/core.py", "def helper(value):\n    return value\n")
    tmp_git_repo.commit("move HEAD")
    result = hyper_codegraph.run_capability("diff_impact", repo_root=root)
    assert result["status"] == "stale_index"
    assert hyper_codegraph.fallback_line("stale_index") in result["warnings"]
    assert stub.calls() == [["--version"]]  # the version probe only


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


def test_synthetic_fixtures_labeled() -> None:
    """Every synthetic S04 fixture starts with the `synthetic: true` header (R27)."""
    files = [p for p in SYNTHETIC.rglob("*") if p.is_file()] if SYNTHETIC.is_dir() else []
    for path in files:
        assert path.read_text(encoding="utf-8").startswith("synthetic: true"), path


def _fill(value: object, project: str, root: str) -> object:
    if isinstance(value, str):
        return value.replace("{project}", project).replace("{repo_root}", root)
    if isinstance(value, list):
        return [_fill(item, project, root) for item in value]
    if isinstance(value, dict):
        return {key: _fill(item, project, root) for key, item in value.items()}
    return value


def test_normalize_fixture() -> None:
    """Each recorded payload normalizes to its expected result in codegraph-0.12.1/.

    The user consented to the contract test, so the payload fixtures are the recorded
    captures; synthetic ones would be used only after a declined test.
    """
    verdict = load("S04", "verdict.json")
    project = verdict["observations"]["index_result"]["project"]
    expected = json.loads((CONTRACT_DIR / "expected_normalized.json").read_text(encoding="utf-8"))
    expected.pop("_comment")
    payload_captures = {
        p.name
        for p in paths("S04", "*-adapter-*.json")
        if not p.name.endswith("-adapter-version.json")
    }
    assert payload_captures <= set(expected), "a payload capture has no expected result"
    for name, spec in expected.items():
        capture = load("S04", name)
        assert capture["exit_code"] == 0, name
        payload = json.loads(capture["stdout"])
        root = capture["cwd"]
        want = _fill(spec, project, root)
        assert hyper_codegraph.truncation_keys(payload) == [] or spec["capability"] == "status"
        if spec["capability"] == "list_projects":
            got = hyper_codegraph.normalize_list_projects(payload, Path(root))
            assert got == want["projects"], name
        else:
            got = hyper_codegraph.normalize_payload(spec["capability"], payload, Path(root))
            assert got == want["normalized"], name


def test_contract_matches_defaults() -> None:
    """The transcribed contract lists each template's tool and flags, with source URLs."""
    contract = yaml.safe_load((CONTRACT_DIR / "contract.yml").read_text(encoding="utf-8"))
    assert contract["version"] == hyper_codegraph.PINNED_VERSION
    assert contract["sources"]["readme"]["url"].startswith(
        "https://raw.githubusercontent.com/DeusData/codebase-memory-mcp/v0.12.1/"
    )
    caps = hyper_codegraph.EMBEDDED_DEFAULTS["backends"][CBM]["capabilities"]
    assert set(contract["capabilities"]) == set(caps)
    for capability, template in caps.items():
        entry = contract["capabilities"][capability]
        assert entry["tool"] == template[1], capability
        assert entry["flags"] == [e for e in template if e.startswith("--")], capability
        assert entry["format_json"] == ("--format" in template), capability
        assert paths("S04", entry["help_capture"]), capability


def test_unknown_shape_is_error(tmp_git_repo: GitRepo, tmp_path: Path) -> None:
    """A payload the 0.12.1 contract does not pin gives status error with the reason."""
    root = _scaffold(tmp_git_repo, _hacf_yml())
    stub = Stub(tmp_path / "stub")
    _indexed_state(root, stub)
    stub.answer("detect_changes", {"changed": ["pkg/core.py"]})
    result = hyper_codegraph.run_capability("diff_impact", repo_root=root)
    assert result["status"] == "error" and "expected key 'changed_files'" in result["error"]
    stub.answer("search_graph", _search(qn_rule="qn = name"))
    result = hyper_codegraph.run_capability("search", repo_root=root, pattern="x")
    assert result["status"] == "error" and "qn_rule" in result["error"]
    stub.answer("detect_changes", stdout="not json")
    result = hyper_codegraph.run_capability("diff_impact", repo_root=root)
    assert result["status"] == "error" and "not JSON" in result["error"]


def test_symbol_impact_without_files(tmp_git_repo: GitRepo, tmp_path: Path) -> None:
    """trace_path rows give null files, distance from hop, no components and the R32 warning."""
    root = _scaffold(tmp_git_repo, _hacf_yml())
    stub = Stub(tmp_path / "stub")
    stub.answer("trace_path", _trace())
    _indexed_state(root, stub)
    result = hyper_codegraph.run_capability("symbol_impact", repo_root=root, symbol="helper")
    assert result["status"] == "ok"
    nulls = {"file": None, "line": None, "kind": None, "risk": None}
    assert result["impacted"] == [
        {"symbol": f"{PROJECT}.pkg.caller.use", **nulls, "distance": 1},
        {"symbol": "main", **nulls, "distance": 2},  # an empty qn_prefix leaves the name alone
    ]
    assert result["impacted_files"] == [] and result["components"] == []
    assert hyper_codegraph.SYMBOL_IMPACT_WARNING in result["warnings"]
    assert stub.calls()[-1] == [
        "cli",
        "trace_path",
        "--project",
        PROJECT,
        "--function-name",
        "helper",
        "--direction",
        "inbound",
        "--format",
        "json",
    ]


def test_truncated_results(tmp_git_repo: GitRepo, tmp_path: Path, run_script) -> None:
    """Truncation is an error for diff_impact and list_projects, a warning for the others."""
    root = _scaffold(tmp_git_repo, _hacf_yml())
    stub = Stub(tmp_path / "stub")
    _indexed_state(root, stub)

    stub.answer("detect_changes", _detect_changes(impacted_has_more=True))
    result = hyper_codegraph.run_capability("diff_impact", repo_root=root)
    assert (result["status"], result["error"]) == ("error", "truncated: impacted_has_more")
    assert result["components"] == []
    stub.answer("detect_changes", _detect_changes(truncated=True, changed_has_more=True))
    result = hyper_codegraph.run_capability("diff_impact", repo_root=root)
    assert result["error"] == "truncated: changed_has_more, truncated"
    tmp_git_repo.write("pkg/core.py", "def helper(value):\n    return value * 3\n")
    report, _, err = _updater(run_script, root)
    assert "code graph: truncated: changed_has_more, truncated" in err
    assert "code graph error" + FALLBACK_TAIL in err
    assert report["code_graph"] == {"status": "error", "components": []}
    assert report["blast_radius"] == ["core_mod"]

    stub.answer("list_projects", _list_projects(root, has_more=True))
    result = hyper_codegraph.run_capability("list_projects", repo_root=root)
    assert (result["status"], result["error"]) == ("error", "truncated: has_more")
    # index records no project from a truncated listing.
    _state(root, exe_argv_prefix=stub.prefix)
    stub.answer("index_repository", {"project": PROJECT, "status": "indexed"})
    result = hyper_codegraph.run_capability("index", repo_root=root)
    assert (result["status"], result["error"]) == ("error", "list_projects: truncated: has_more")
    assert "project_name" not in hyper_codegraph.load_state(root)[0]

    _indexed_state(root, stub)
    stub.answer("search_graph", _search(truncated=True))
    result = hyper_codegraph.run_capability("search", repo_root=root, pattern="help.*")
    assert result["status"] == "ok" and "truncated: truncated" in result["warnings"]
    assert result["impacted_files"] == ["pkg/core.py"] and result["components"] == ["core_mod"]
    stub.answer("trace_path", _trace(has_more=True))
    result = hyper_codegraph.run_capability("symbol_impact", repo_root=root, symbol="helper")
    assert result["status"] == "ok" and "truncated: callers.has_more" in result["warnings"]


# ---------------------------------------------------------------------------
# hypergraph_updater.py --from-diff enrichment
# ---------------------------------------------------------------------------


def test_from_diff_enrichment(tmp_git_repo: GitRepo, tmp_path: Path, run_script) -> None:
    """Ok adds the stub's components; unavailable prints the fallback; none changes nothing."""
    root = _scaffold(tmp_git_repo, _hacf_yml())
    stub = Stub(tmp_path / "stub")
    stub.answer("detect_changes", _detect_changes())
    _indexed_state(root, stub)
    tmp_git_repo.write("pkg/core.py", "def helper(value):\n    return value * 3\n")

    report, _, err = _updater(run_script, root)
    assert report["nodes"] == ["core_mod"]
    assert report["code_graph"] == {"status": "ok", "components": ["caller_mod", "core_mod"]}
    assert report["blast_radius"] == ["caller_mod", "core_mod"]
    assert report["unmapped"] == [] and err == ""

    # The executable is gone: the fallback line, and the blast radius from files: globs only.
    _state(root, project_name=PROJECT, last_index={"head": hyper_codegraph.head_commit(root)})
    report, _, err = _updater(run_script, root, PATH=_git_only_path(tmp_path))
    assert "code graph unavailable" + FALLBACK_TAIL in err
    assert report["code_graph"] == {"status": "unavailable", "components": []}
    assert report["blast_radius"] == ["core_mod"]

    # Backend none, an empty code_graph section and no hacf.yml print the same bytes,
    # without a code_graph entry, and run nothing.
    _indexed_state(root, stub)
    calls = len(stub.calls())
    local = root / hyper_codegraph.LOCAL_REL
    runs = []
    for text in (_hacf_yml("none"), "code_graph: {}\n", None):
        if text is None:
            local.unlink()
        else:
            local.write_text(text, encoding="utf-8")
        report, out, err = _updater(run_script, root)
        runs.append((out, err))
        assert "code_graph" not in report and report["blast_radius"] == ["core_mod"]
    assert runs[0] == runs[1] == runs[2]
    assert len(stub.calls()) == calls


def test_from_diff_base_commit(tmp_git_repo: GitRepo, tmp_path: Path, run_script) -> None:
    """--from-diff REV passes REV's full commit id as --base-branch; a rev-range calls nothing."""
    root = _scaffold(tmp_git_repo, _hacf_yml())
    stub = Stub(tmp_path / "stub")
    stub.answer("detect_changes", _detect_changes())
    _indexed_state(root, stub)
    tmp_git_repo.write("pkg/core.py", "def helper(value):\n    return value * 3\n")
    head = tmp_git_repo.git("rev-parse", "HEAD").strip()
    parent = tmp_git_repo.git("rev-parse", "HEAD~1").strip()

    for rev, sha in (("HEAD", head), ("HEAD~1", parent)):
        report, _, _ = _updater(run_script, root, rev)
        assert report["code_graph"]["status"] == "ok"
        argv = stub.calls()[-1]
        assert argv[:2] == ["cli", "detect_changes"]
        assert argv[argv.index("--base-branch") + 1] == sha

    calls = len(stub.calls())
    for rev in ("HEAD~1..HEAD", "HEAD~1...HEAD"):
        report, _, err = _updater(run_script, root, rev)
        assert "code graph unsupported for a revision range" + FALLBACK_TAIL in err
        assert report["code_graph"] == {"status": "unsupported", "components": []}
    assert len(stub.calls()) == calls


def test_from_diff_positional_form_untouched(tmp_git_repo: GitRepo, run_script) -> None:
    """The positional form never consults the code graph, even with a backend selected."""
    root = _scaffold(tmp_git_repo, _hacf_yml())
    proc = run_script(UPDATER, LEDGER_REL, "core_mod", cwd=root)
    assert proc.returncode == 0, proc.stderr
    assert "code graph" not in proc.stdout + proc.stderr
    assert "HYPERGRAPH UPDATER: SUCCESS" in proc.stdout
