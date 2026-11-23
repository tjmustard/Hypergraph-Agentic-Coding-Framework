"""Unit tests for dev/openrig_compat.py and the argument checks of dev/openrig_compat.sh.

No network and no rig: synthetic captures, help texts, watched files and baselines under tmp_path
(release-0.6.2 slice 3, mini-requirement 8). Every hostile input must end as a setup error or a
fail with a message, never a crash and never a pass.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "dev"))
import openrig_compat as oc  # noqa: E402

SCRIPT = REPO_ROOT / "dev" / "openrig_compat.sh"
MODULE = REPO_ROOT / "dev" / "openrig_compat.py"


# ---------------------------------------------------------------------------------------------
# Synthetic captures


def _capture(step: str, stdout: object = None, *, exit_code: int = 0, stderr: str = "",
             tmp: str = "/home/u/.cache/x/openrig-compat-AB12/tmp/pytest-of-u/pytest-3/"
             "test_openrig_cli_spike0", when: str = "2026-11-23T07:00:00Z") -> dict:  # fmt: skip
    out = stdout if isinstance(stdout, str) else json.dumps(stdout if stdout is not None else {})
    return {
        "step": step,
        "argv": ["/x/npm/node_modules/.bin/rig", "scope", "--workspace", f"{tmp}/repo", "audit"],
        "cwd": f"{tmp}/repo",
        "exit_code": exit_code,
        "stdout": out,
        "stderr": stderr,
        "rig_version": "0.7.21 (abcdef12)",
        "captured_at": when,
        "git_status_after": [],
    }


def _verdict(checks: dict | None = None) -> dict:
    return {
        "spike_verdict_cli": "pass",
        "spike_verdict_daemon": "pending-user-env",
        "npm_dist_integrity": "sha512-x",
        "rig_version": "0.7.21",
        "captured_at": "2026-11-23T07:00:00Z",
        "checks": checks or {"a": True},
        "rig_write_sets": {"step": ["missions/m/x.md"]},
        "captures": ["01-scope-audit.json"],
    }


def _write_dir(path: Path, captures: dict[str, dict], verdict: dict | None = None) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    for name, data in captures.items():
        (path / name).write_text(json.dumps(data), encoding="utf-8")
    (path / "verdict.json").write_text(json.dumps(verdict or _verdict()), encoding="utf-8")
    return path


AUDIT = {"ok": True, "mission": {"findings": []}, "slices": [{"name": "01-a", "findings": []}]}


# ---------------------------------------------------------------------------------------------
# Normalisation


def test_normalisation_is_idempotent_and_stable() -> None:
    """Two runs that differ only in temp paths, timestamps, dates and shas normalise equal."""
    a = _capture("scope-audit", {"at": "2026-11-23T07:00:00.123Z", "sha": "a" * 40})
    b = _capture(
        "scope-audit",
        {"at": "2027-01-02T03:04:05Z", "sha": "b" * 40},
        tmp="/other/place/pytest-of-v/pytest-17/test_openrig_cli_spike1",
        when="2027-01-02T03:04:05Z",
    )
    na = oc.normalise_capture(a, "a", ("/x",))
    nb = oc.normalise_capture(b, "b", ("/y",))
    assert na == nb
    assert oc.normalise_capture(na, "again") == na  # idempotent
    text = "at 2026-11-23 in /t/pytest-of-u/pytest-1/test_openrig_cli_spike0 @openrig/cli@0.7.21"
    once = oc.normalise_text(text, ("/t",))
    assert oc.normalise_text(once, ("/t",)) == once
    assert "<DATE>" in once and "@openrig/cli@<V>" in once and "0.7.21" not in once


def test_capture_dir_round_trip_is_stable(tmp_path: Path) -> None:
    """A dir of normalised captures loads to itself (the stored baseline form)."""
    raw = _write_dir(tmp_path / "raw", {"01-scope-audit.json": _capture("scope-audit", AUDIT)})
    loaded = oc.load_capture_dir(raw)
    stored = tmp_path / "stored"
    stored.mkdir()
    for name, data in loaded.items():
        oc.write_json(stored / name, data)
    assert oc.load_capture_dir(stored) == loaded


# ---------------------------------------------------------------------------------------------
# Capture comparisons


def _cmp(base_stdout: object, cand_stdout: object, **cand: object) -> dict:
    base = {"01-scope-audit.json": oc.normalise_capture(_capture("scope-audit", base_stdout), "b")}
    base["verdict.json"] = oc.normalise_verdict(_verdict(), "b")
    capture = _capture("scope-audit", cand_stdout, **cand)  # type: ignore[arg-type]
    got = {"01-scope-audit.json": oc.normalise_capture(capture, "c")}
    got["verdict.json"] = oc.normalise_verdict(_verdict(), "c")
    return oc.compare_captures(base, got)


def test_equal_captures_pass() -> None:
    """Identical normalised captures compare ok with nothing added."""
    got = _cmp(AUDIT, AUDIT)
    assert got == {"ok": True, "problems": [], "json_paths_added": {}}


def test_grown_key_paths_pass_and_are_reported() -> None:
    """New JSON keys are allowed (a superset) and listed in json_paths_added."""
    grown = {**AUDIT, "parentFindings": []}
    got = _cmp(AUDIT, grown)
    assert got["ok"] is True
    assert got["json_paths_added"] == {"01-scope-audit.json": ["/parentFindings"]}


def test_removed_key_path_fails() -> None:
    """A key path the baseline had is gone: fail, naming it."""
    shrunk = {"ok": True, "slices": []}
    got = _cmp(AUDIT, shrunk)
    assert got["ok"] is False
    assert any("/mission" in p for p in got["problems"])


def test_changed_exit_code_fails() -> None:
    """A different exit code is a fail."""
    got = _cmp(AUDIT, AUDIT, exit_code=1)
    assert got["ok"] is False and any("exit_code" in p for p in got["problems"])


def test_changed_stderr_and_missing_capture_fail() -> None:
    """The stderr must be equal after normalisation, and the capture set must be the same."""
    assert _cmp(AUDIT, AUDIT, stderr="warning: new")["ok"] is False
    base = {"01-a.json": oc.normalise_capture(_capture("a", AUDIT), "b"),
            "verdict.json": oc.normalise_verdict(_verdict(), "b")}  # fmt: skip
    got = oc.compare_captures(base, {"verdict.json": base["verdict.json"]})
    assert got["ok"] is False and "missing" in got["problems"][0]


def test_live_verdict_not_pass_fails() -> None:
    """The candidate's own live verdict must be pass, and its checks equal the baseline's."""
    base = {"verdict.json": oc.normalise_verdict(_verdict(), "b")}
    failing = {**_verdict({"a": False}), "spike_verdict_cli": "fail"}
    got = oc.compare_captures(base, {"verdict.json": oc.normalise_verdict(failing, "c")})
    assert got["ok"] is False
    assert any("not pass" in p for p in got["problems"])
    assert any("checks differs" in p for p in got["problems"])


def test_non_json_stdout_is_not_compared() -> None:
    """The version and integrity steps print plain text; only JSON stdout is path-compared."""
    assert _cmp("0.7.16 (d77a657e)\n", "0.7.21 (abcdef12)\n")["ok"] is True
    assert _cmp("plain", AUDIT)["ok"] is False  # JSON on one side only


# ---------------------------------------------------------------------------------------------
# Hostile inputs


@pytest.mark.parametrize(
    "text",
    [
        '{"a": NaN}',
        '{"a": Infinity}',
        '{"a": -Infinity}',
        '{"a": 1e999}',
        '{"a": ' + "9" * 400 + "}",
        "[" * 5000 + "]" * 5000,
        '{"a": [' * 70 + "]}" * 70,
        "{not json",
    ],
)
def test_hostile_json_is_an_input_error(text: str) -> None:
    """NaN, infinities, huge integers, deep nesting and garbage never parse."""
    with pytest.raises(oc.CompatInputError):
        oc.loads_json(text, "x")


@pytest.mark.parametrize(
    "record",
    [
        [],
        {},
        {**_capture("a"), "exit_code": "0"},
        {**_capture("a"), "exit_code": True},
        {**_capture("a"), "argv": []},
        {**_capture("a"), "argv": [1, 2]},
        {**_capture("a"), "stdout": None},
        {**_capture("a"), "git_status_after": "dirty"},
        {k: v for k, v in _capture("a").items() if k != "stderr"},
    ],
)
def test_malformed_capture_is_an_input_error(record: object) -> None:
    """Missing keys and wrong types are input errors, never a silent pass."""
    with pytest.raises(oc.CompatInputError):
        oc.normalise_capture(record, "x")


@pytest.mark.parametrize(
    "verdict",
    [
        [],
        {},
        {**_verdict(), "checks": {"a": "yes"}},
        {**_verdict(), "captures": [1]},
        {**_verdict(), "checks": {}},
        {**_verdict(), "captures": []},
        {**_verdict(), "rig_write_sets": {}},
    ],  # fmt: skip
)
def test_malformed_verdict_is_an_input_error(verdict: object) -> None:
    """The live suite's verdict.json is validated like a capture."""
    with pytest.raises(oc.CompatInputError):
        oc.normalise_verdict(verdict, "x")


def test_capture_set_must_match_the_verdict_list(tmp_path: Path) -> None:
    """A capture dir whose files differ from verdict.json's captures list is an input error."""
    caps = {"01-scope-audit.json": _capture("scope-audit", AUDIT)}
    assert oc.load_capture_dir(_write_dir(tmp_path / "ok", caps))
    with pytest.raises(oc.CompatInputError, match="lists"):
        oc.load_capture_dir(_write_dir(tmp_path / "none", {}))
    extra = {**caps, "02-more.json": _capture("more", AUDIT)}
    with pytest.raises(oc.CompatInputError, match="lists"):
        oc.load_capture_dir(_write_dir(tmp_path / "extra", extra))


def _baseline_root(tmp_path: Path, versions: dict[str, str]) -> Path:
    """A fake tree: the real hyper_openrig.py and a contract file listing versions."""
    root = tmp_path / "tree"
    (root / ".agents" / "scripts").mkdir(parents=True)
    (root / ".agents" / "config").mkdir(parents=True)
    shutil.copy(REPO_ROOT / ".agents/scripts/hyper_openrig.py", root / ".agents/scripts")
    for extra in ("_runtime.py",):
        if (REPO_ROOT / ".agents/scripts" / extra).exists():
            shutil.copy(REPO_ROOT / ".agents/scripts" / extra, root / ".agents/scripts")
    lines = ["schema: 1", "accepted:"]
    for version, by in versions.items():
        lines.append(f'  - {{version: "{version}", integrity: "sha512-x", verdict: "v", '
                     f"accepted_by: {by}}}")  # fmt: skip
    lines += ["range:", "  - {major: 0, minor: 7, min_patch: 16}", "probe: {schema: 1}", ""]
    (root / ".agents/config/openrig_contract.yml").write_text("\n".join(lines), encoding="utf-8")
    return root


def _baseline(root: Path, version: str, captures: Path | None = None) -> Path:
    base = oc.baseline_dir(root, version)
    (base / "captures").mkdir(parents=True)
    if captures is not None:
        for p in captures.iterdir():
            shutil.copy(p, base / "captures" / p.name)
    return base


def test_hostile_baseline_is_setup_and_hostile_candidate_is_fail(tmp_path: Path) -> None:
    """A broken committed baseline is the harness's problem; a broken candidate capture fails."""
    root = _baseline_root(tmp_path, {"0.7.16": "person", "0.7.21": "person"})
    good = _write_dir(tmp_path / "good", {"01-scope-audit.json": _capture("scope-audit", AUDIT)})
    base = _baseline(root, "0.7.16", good)
    (base / "captures" / "01-scope-audit.json").write_text('{"a": NaN}', encoding="utf-8")
    out = tmp_path / "out.json"
    assert oc.run_captures(root, good, ["0.7.16"], (), out) == oc.EXIT_SETUP
    assert "NaN" in json.loads(out.read_text())["setup"][0]

    _baseline(root, "0.7.21", good)
    bad = tmp_path / "bad"
    shutil.copytree(good, bad)
    (bad / "01-scope-audit.json").write_text("[" * 5000, encoding="utf-8")
    assert oc.run_captures(root, bad, ["0.7.21"], (), out) == oc.EXIT_FAIL
    assert oc.run_captures(root, good, ["0.7.21"], (), out) == oc.EXIT_OK
    deep = {**_capture("scope-audit"), "stdout": '{"a": ' * 80 + "1" + "}" * 80}
    (bad / "01-scope-audit.json").write_text(json.dumps(deep), encoding="utf-8")
    assert oc.run_captures(root, bad, ["0.7.21"], (), out) == oc.EXIT_FAIL


def test_baseline_verdict_not_pass_and_no_baseline_are_setup(tmp_path: Path) -> None:
    """Audit iter 1 items 1 and 3: a non-pass baseline verdict or no baseline is SETUP.

    A baseline whose live verdict is not pass is broken, and an empty baseline list is never a
    skipped comparison.
    """
    root = _baseline_root(tmp_path, {"0.7.21": "person"})
    good = _write_dir(tmp_path / "good", {"01-scope-audit.json": _capture("scope-audit", AUDIT)})
    base = _baseline(root, "0.7.21", good)
    out = tmp_path / "out.json"
    assert oc.run_captures(root, good, ["0.7.21"], (), out) == oc.EXIT_OK
    bad_verdict = {**_verdict(), "spike_verdict_cli": "fail"}
    oc.write_json(base / "captures" / "verdict.json", bad_verdict)
    assert oc.run_captures(root, good, ["0.7.21"], (), out) == oc.EXIT_SETUP
    assert "not pass" in json.loads(out.read_text())["setup"][0]
    assert oc.run_captures(root, good, [], (), out) == oc.EXIT_SETUP


@pytest.mark.parametrize(
    "text",
    [
        "",
        "[1]",
        "{}",
        '{"baseline": "0.7.21"}',
        '{"baseline": "", "person_baseline": ""}',
        '{"baseline": "0.7.21", "person_baseline": "x"}',
        "{not json",
    ],
)
def test_baseline_list_refuses_doubt(text: str, tmp_path: Path, capsys) -> None:
    """The shell's baselines parse (now `baseline-list`) exits 2 on any doubt, never prints ''."""
    path = tmp_path / "baselines.json"
    path.write_text(text, encoding="utf-8")
    assert oc.main(["baseline-list", "--file", str(path)]) == oc.EXIT_SETUP
    assert capsys.readouterr().out == ""
    path.write_text('{"baseline": "0.7.21", "person_baseline": "0.7.16"}', encoding="utf-8")
    assert oc.main(["baseline-list", "--file", str(path)]) == oc.EXIT_OK
    assert capsys.readouterr().out.strip() == "0.7.21,0.7.16"


def test_script_never_swallows_the_baselines_parse() -> None:
    """Audit iter 1 item 1: the shell's inline `except Exception: pass` parse is gone.

    The baselines step itself fails when the list cannot be read or is empty.
    """
    text = SCRIPT.read_text(encoding="utf-8")
    assert "except Exception" not in text
    assert "step baselines choose_baselines" in text
    body = text.split("choose_baselines() {", 1)[1].split("\n}\n", 1)[0]
    assert "baseline-list" in body and "return 2" in body and "2>/dev/null" not in body


def test_hostile_package_and_npm_output(tmp_path: Path) -> None:
    """package.json and `npm view` output are validated, never trusted."""
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    for text in ('{"name": "@openrig/cli", "version": NaN}', "[]", '{"name": "evil"}',
                 '{"name": "@openrig/cli", "version": "0.7.22"}'):  # fmt: skip
        (pkg / "package.json").write_text(text, encoding="utf-8")
        with pytest.raises(oc.CompatInputError):
            oc.package_meta(pkg, "0.7.21")
    (pkg / "package.json").write_text('{"name": "@openrig/cli", "version": "0.7.21"}', "utf-8")
    assert oc.package_meta(pkg, "0.7.21")["version"] == "0.7.21"
    for text in ("", "sha1-abc", "sha512-" + "A" * 86 + "== extra", "undefined"):
        with pytest.raises(oc.CompatInputError):
            oc.integrity_from_npm(text)
    assert oc.integrity_from_npm("sha512-" + "A" * 86 + "==\n").startswith("sha512-")


@pytest.mark.parametrize(
    "version", ["0.7", "v0.7.21", "0.7.021", "0.7.21-rc.1", "1" * 30 + ".0.0", "", "0.7.21\n"]
)
def test_versions_are_strict(version: str) -> None:
    """Only a plain M.m.p with small parts is a version."""
    with pytest.raises(oc.CompatInputError):
        oc.check_version(version)


def test_probe_result_judgement() -> None:
    """In range the probe must pass; outside it, outcome null is n/a; anything else fails."""
    assert oc.probe_result('{"outcome": "pass"}', True)[0] is True
    assert oc.probe_result('{"outcome": "fail"}', True)[0] is False
    assert oc.probe_result('{"outcome": null}', False)[0] is True
    assert oc.probe_result('{"outcome": "pass"}', False)[0] is False
    for text in ("", "[]", '{"outcome": NaN}', "{" * 10):
        assert oc.probe_result(text, True)[0] is False


# ---------------------------------------------------------------------------------------------
# Help texts


HELP = """Usage: rig scope slice close [options] <slice-path>

Close a slice

Arguments:
  slice-path         Slice path

Options:
  --reason <reason>  Closure reason
  --mission <name>   Hint mission
  --note <text>      A note
  --json             Machine-readable output
  -h, --help         display help for command
"""

PARENT = """Usage: rig scope [options] [command]

Options:
  --workspace <dir>  Workspace root
  -h, --help         display help for command

Commands:
  audit [options]    Audit
  slice              Slices
  help [command]     display help for command
"""


def test_parse_help_reads_flags_and_commands() -> None:
    """Flags come from the Options section and subcommands from Commands."""
    assert oc.parse_help(HELP)["flags"] == ["--help", "--json", "--mission", "--note", "--reason"]
    assert oc.parse_help(PARENT)["commands"] == ["audit", "help", "slice"]


def test_help_diff_classes() -> None:
    """Removed flag or subcommand fail; additions are reported; HACF's flags must stay."""
    same = oc.help_diff(HELP, HELP, ["--reason"])
    assert same["ok"] is True and not same["added_flags"]
    added = oc.help_diff(HELP, HELP.replace("  --json", "  --in-place  Keep\n  --json"), [])
    assert added["ok"] is True and added["added_flags"] == ["--in-place"]
    removed = oc.help_diff(HELP, HELP.replace("  --note <text>      A note\n", ""), [])
    assert removed["ok"] is False and removed["removed_flags"] == ["--note"]
    renamed = oc.help_diff(HELP, HELP.replace("--reason", "--why"), ["--reason"])
    assert renamed["ok"] is False and renamed["hacf_flags_missing"] == ["--reason"]
    gone = oc.help_diff(PARENT, PARENT.replace("  slice              Slices\n", ""), [])
    assert gone["ok"] is False and gone["removed_commands"] == ["slice"]
    more = oc.help_diff(PARENT, PARENT + "  extra              New\n", [])
    assert more["ok"] is True


@pytest.mark.parametrize(
    "broken",
    [
        "",
        "\n",
        "Usage: rig scope slice cl",
        HELP.split("Options:")[0],
        HELP.split("  -h, --help")[0],
        "Usage: rig x\n\nCommands:\n  a   A\n",
        PARENT.split("\nCommands:")[0] + "\n",
        PARENT.split("  help [command]")[0],
        "Options:\n  -h, --help  x\n",
    ],
    ids=[
        "empty",
        "blank",
        "mid-usage",
        "before-options",
        "before-help",
        "no-options",
        "cut-after-options",
        "cut-before-help-row",
        "no-usage",
    ],  # fmt: skip
)
def test_broken_baseline_help_text_is_a_setup_error(broken: str) -> None:
    """Audit iter 1 item 3: an empty or truncated baseline help text never compares as ok."""
    with pytest.raises(oc.CompatInputError):
        oc.help_diff(broken, HELP, [])
    assert oc.check_help_text(HELP, "x")["flags"]  # a whole text is accepted


FIXTURES = REPO_ROOT / "dev" / "tests" / "fixtures" / "openrig-compat"


def test_audit_iter2_cut_help_repro() -> None:
    """Audit iter 2 item 2: the committed scope.txt cut after Options hides a removed `mission`."""
    base = (FIXTURES / "0.7.21" / "help" / "scope.txt").read_text(encoding="utf-8")
    mission = next(line for line in base.splitlines(keepends=True) if line.startswith("  mission "))
    cand = base.replace(mission, "")
    assert oc.help_diff(base, cand, [])["removed_commands"] == ["mission"]
    with pytest.raises(oc.CompatInputError, match="Commands"):
        oc.help_diff(base.split("\nCommands:")[0] + "\n", cand, [])
    with pytest.raises(oc.CompatInputError):
        oc.help_diff("Options:\n  -h, --help  x\n", cand, [])


def test_committed_baseline_help_texts_are_whole() -> None:
    """Every committed help text passes check_help_text, and each [command] one lists help."""
    texts = sorted(FIXTURES.glob("*/help/*.txt"))
    assert len(texts) == 18
    parents = 0
    for path in texts:
        parsed = oc.check_help_text(path.read_text(encoding="utf-8"), str(path))
        parents += "help" in parsed["commands"]
    assert parents == 6


def test_run_help_seed_refuses_a_cut_parent_text(tmp_path: Path) -> None:
    """--seed: a captured `[command]` text cut after Options is a setup error, not a baseline."""
    root = _baseline_root(tmp_path, {"0.7.21": "person"})
    out, helps = tmp_path / "help.json", tmp_path / "h"
    cut = _fake_rig(tmp_path, PARENT.split("\nCommands:")[0] + "\n")
    assert oc.run_help(root, cut, helps, [], out, seed=True) == oc.EXIT_SETUP
    assert "Commands" in json.loads(out.read_text())["setup"][0]


def _fake_rig(tmp_path: Path, text: str) -> str:
    rig = tmp_path / "fake-rig"
    (tmp_path / "help.txt").write_text(text, encoding="utf-8")
    rig.write_text(f"#!/bin/sh\ncat '{tmp_path / 'help.txt'}'\n", encoding="utf-8")
    rig.chmod(0o755)
    return str(rig)


def test_run_help_fails_closed(tmp_path: Path) -> None:
    """No baseline outside --seed, or a broken baseline text, is SETUP; --seed checks its texts."""
    root = _baseline_root(tmp_path, {"0.7.21": "person"})
    rig, out, helps = _fake_rig(tmp_path, PARENT), tmp_path / "help.json", tmp_path / "h"
    assert oc.run_help(root, rig, helps, [], out) == oc.EXIT_SETUP
    assert json.loads(out.read_text())["setup"] == ["no baseline to compare with"]
    base = oc.baseline_dir(root, "0.7.21") / "help"
    base.mkdir(parents=True)
    for item in oc.help_commands(root):
        (base / oc.help_name(item["words"])).write_text(PARENT, encoding="utf-8")
    assert oc.run_help(root, rig, helps, ["0.7.21"], out) != oc.EXIT_SETUP
    (base / "scope.txt").write_text("", encoding="utf-8")
    assert oc.run_help(root, rig, helps, ["0.7.21"], out) == oc.EXIT_SETUP
    assert "scope.txt" in json.loads(out.read_text())["setup"][0]
    # --seed compares nothing, but an empty captured text would become a broken baseline.
    assert oc.run_help(root, rig, helps, [], out, seed=True) != oc.EXIT_SETUP
    assert oc.run_help(root, _fake_rig(tmp_path, ""), helps, [], out, seed=True) == oc.EXIT_SETUP


def test_help_commands_come_from_the_argv_builders() -> None:
    """The real tree's builders give the 9 commands HACF calls (and their parents)."""
    got = {" ".join(c["words"]): c["flags"] for c in oc.help_commands(REPO_ROOT)}
    assert sorted(got) == [
        "proof", "proof add", "scope", "scope audit", "scope slice", "scope slice close",
        "scope slice create", "scope slice ls", "scope slice progress",
    ]  # fmt: skip
    assert got["scope"] == ["--workspace"] and got["proof"] == ["--workspace"]
    assert "--name" in got["proof add"] and "--note" in got["scope slice close"]


# ---------------------------------------------------------------------------------------------
# Watched files


def _package(path: Path, mission_composition: bytes = b"export const a = 1;\n") -> Path:
    files = {
        "daemon/dist/domain/lifecycle-manifest.js": b"lifecycle\n",
        "dist/lib/scope/mission-composition.js": mission_composition,
        "dist/lib/scope/scope-audit.js": b"audit\n",
        "dist/lib/scope/scaffold-placeholder.js": b"placeholder\n",
        "dist/commands/scope.js": b"const other = 1;\n"
        b"const missionManifest = (name) => `schema: m\n  name: ${JSON.stringify(name)}\n`;\n"
        b"const SLICE_MANIFEST = `schema: s\n`;\nlet x = 2;\n",
        "daemon/dist/domain/workspace/default-workspace-scaffold.js": b"const PROJECT_MANIFEST = "
        b"`schema: p\n`;\nconst WORKSPACE_CATALOG = `schema: w\n`;\n",
    }
    for rel, data in files.items():
        (path / rel).parent.mkdir(parents=True, exist_ok=True)
        (path / rel).write_bytes(data)
    return path


def test_extract_template() -> None:
    """A template literal is cut out exactly, through ${...}, nested templates and escapes."""
    src = b"const A = `x ${f(`in ${y}`)} \\` z`;\nconst B = (n) => `b${n}`;\nconst C = 1;"
    assert oc.extract_template(src, "A") == b"`x ${f(`in ${y}`)} \\` z`"
    assert oc.extract_template(src, "B") == b"`b${n}`"
    assert oc.extract_template(src, "C") is None
    assert oc.extract_template(b"const A = `never closed", "A") is None


def _baseline_watched(root: Path, version: str, package: Path) -> None:
    dest = oc.baseline_dir(root, version) / "watched"
    dest.mkdir(parents=True, exist_ok=True)
    for key, data in oc.watched_bytes(package).items():
        (dest / oc.watched_file_name(key)).write_bytes(data or b"")


def test_watched_items_against_one_or_both_baselines(tmp_path: Path) -> None:
    """Equal to both baselines: no diff. Different from either one: REVIEW, with a diff each."""
    root = tmp_path / "tree"
    old = _package(tmp_path / "old")
    new = _package(tmp_path / "new", b"export const a = 2;\n")
    _baseline_watched(root, "0.7.16", old)
    _baseline_watched(root, "0.7.21", new)
    out, diffs = tmp_path / "w.json", tmp_path / "diffs"

    assert oc.run_watched(root, new, ["0.7.21"], out, diffs) == oc.EXIT_OK
    assert json.loads(out.read_text())["watched_diff"] == {}

    assert oc.run_watched(root, new, ["0.7.21", "0.7.16"], out, diffs) == oc.EXIT_OK
    differs = json.loads(out.read_text())["watched_diff"]
    assert differs == {"mission-composition.js": {"0.7.16": "differs"}}
    assert (diffs / "watched-mission-composition.js-vs-0.7.16.diff").is_file()
    verdict = oc.decide(_all_ok(), differs, seed=False)
    assert verdict[0] == "REVIEW"

    # A template change counts, a change elsewhere in the same file does not.
    scope = new / "dist/commands/scope.js"
    scope.write_bytes(scope.read_bytes().replace(b"let x = 2;", b"let x = 3;"))
    oc.run_watched(root, new, ["0.7.21"], out, diffs)
    assert json.loads(out.read_text())["watched_diff"] == {}
    scope.write_bytes(scope.read_bytes().replace(b"schema: s", b"schema: S"))
    oc.run_watched(root, new, ["0.7.21"], out, diffs)
    assert json.loads(out.read_text())["watched_diff"] == {"SLICE_MANIFEST": {"0.7.21": "differs"}}
    (new / "dist/lib/scope/scope-audit.js").unlink()
    oc.run_watched(root, new, ["0.7.21"], out, diffs)
    assert json.loads(out.read_text())["watched_diff"]["scope-audit.js"] == {"0.7.21": "missing"}

    shutil.rmtree(oc.baseline_dir(root, "0.7.16") / "watched")
    assert oc.run_watched(root, new, ["0.7.16"], out, diffs) == oc.EXIT_SETUP
    # No baseline at all is a setup error, never an empty (equal) comparison.
    assert oc.run_watched(root, new, [], out, diffs) == oc.EXIT_SETUP
    assert json.loads(out.read_text())["setup"] == ["no baseline to compare with"]


# ---------------------------------------------------------------------------------------------
# Classification and verdicts


@pytest.mark.parametrize(
    ("rc", "oom", "kind", "want"),
    [
        (0, False, "product", "ok"),
        (1, False, "product", "fail"),
        (2, False, "product", "fail"),
        (124, False, "product", "setup"),
        (128, False, "product", "setup"),
        (137, False, "product", "setup"),
        (143, False, "product", "setup"),
        (-9, False, "product", "setup"),
        (1, True, "product", "setup"),
        (0, True, "product", "setup"),
        (1, False, "harness", "setup"),
        (2, False, "harness", "setup"),
        (1, False, "compare", "fail"),
        (2, False, "compare", "setup"),
        (3, False, "compare", "setup"),
        ("1", False, "product", "setup"),
        (True, False, "product", "setup"),
    ],
)
def test_exit_code_and_oom_classification(rc: object, oom: bool, kind: str, want: str) -> None:
    """A signal, a timeout or an OOM kill is setup, never the product's fail."""
    assert oc.classify_exit(rc, oom, kind) == want


def _all_ok(seed: bool = False, drop: str | None = None) -> list[dict]:
    names = oc.SEED_REQUIRED_STEPS if seed else oc.REQUIRED_STEPS
    return [{"name": n, "result": "ok"} for n in names if n != drop]


def test_decide_order() -> None:
    """Safety fail > setup > fail > missing step > seed > review > pass; REVIEW is never PASS."""
    ok = _all_ok()
    assert oc.decide(ok, {}, False) == ("PASS", None, None)
    assert oc.decide(ok, {"x": {}}, False)[0] == "REVIEW"
    assert oc.decide(_all_ok(seed=True), {"x": {}}, True)[0] == "SEED"
    assert oc.decide([*ok, {"name": "help", "result": "fail"}], {}, False) == ("FAIL", "help", None)
    setup = {"name": "live", "result": "setup", "detail": "exit 137"}
    assert oc.decide([setup, {"name": "help", "result": "fail"}], {}, False)[0] == "SETUP-ERROR"
    contact = {"name": "daemon-contact", "result": "fail"}
    assert oc.decide([setup, contact], {}, False) == ("FAIL", "daemon-contact", None)
    assert oc.decide([], {}, False)[0] == "SETUP-ERROR"
    assert oc.decide([*ok, {"name": "x", "result": "weird"}], {}, False)[0] == "SETUP-ERROR"
    assert oc.decide(ok, ["not", "an", "object"], False)[0] == "SETUP-ERROR"  # type: ignore[arg-type]


def test_decide_requires_every_step_of_the_mode() -> None:
    """Audit iter 1 item 1: the steps decide() never sees are not passes.

    A missing step is a SETUP-ERROR that names it; the audit's two repros (five rows, two safety
    rows) are not PASS.
    """
    rows = ("install", "tree", "baselines", "daemon-contact", "host-touched")
    five = [{"name": n, "result": "ok"} for n in rows]
    verdict, failed, why = oc.decide(five, {}, False)
    assert (verdict, failed) == ("SETUP-ERROR", None)
    assert "live" in why and "watched" in why and "delegate-oracle" in why
    safety = [{"name": n, "result": "ok"} for n in oc.SAFETY_STEPS]
    assert oc.decide(safety, {}, False)[0] == "SETUP-ERROR"
    assert oc.decide(safety, {}, True)[0] == "SETUP-ERROR"
    # Seed mode needs its own set, and not the compared steps.
    assert "baselines" not in oc.SEED_REQUIRED_STEPS
    assert set(oc.SEED_REQUIRED_STEPS) < set(oc.REQUIRED_STEPS)


@pytest.mark.parametrize("seed", [False, True])
def test_sweep_dropping_any_required_step_is_never_green(seed: bool) -> None:
    """The sweep: remove each required step in turn; the verdict is SETUP-ERROR and names it."""
    names = oc.SEED_REQUIRED_STEPS if seed else oc.REQUIRED_STEPS
    assert oc.decide(_all_ok(seed), {}, seed)[0] == ("SEED" if seed else "PASS")
    for name in names:
        verdict, _, why = oc.decide(_all_ok(seed, drop=name), {}, seed)
        assert verdict == "SETUP-ERROR", name
        assert name in why
        assert oc.decide(_all_ok(seed, drop=name), {"x": {}}, seed)[0] == "SETUP-ERROR", name


def test_read_steps_is_defensive(tmp_path: Path) -> None:
    """Garbage rows in steps.tsv are setup steps, never a pass."""
    path = tmp_path / "steps.tsv"
    path.write_text(
        "live\t0\t3.2\t0\t\nhelp\t1\t1.0\t0\tflag gone\nwho\t0\t1\t0\t\nlive\tNaN\t1\t0\t\n"
        "short\nlive\t137\t9\t0\t\ndelegate-oracle\t1\t50\t1\t\n",
        encoding="utf-8",
    )
    got = [(s["name"], s["result"]) for s in oc.read_steps(path)]
    assert got == [
        ("live", "ok"), ("help", "fail"), ("who", "setup"), ("live", "setup"),
        ("steps.tsv:5", "setup"), ("live", "setup"), ("delegate-oracle", "setup"),
    ]  # fmt: skip
    assert oc.read_steps(path)[6]["detail"].startswith("an OOM kill")


@pytest.mark.parametrize(
    "sep", ["\f", "\v", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029", "\r"]
)
def test_read_steps_splits_rows_on_newline_only(sep: str, tmp_path: Path) -> None:
    """A FAIL detail holding a str.splitlines() break stays one FAIL row (slice 3 audit, item 3)."""
    path = tmp_path / "steps.tsv"
    path.write_text(f"help\t1\t1.0\t0\tflag{sep}gone\nlive\t0\t1.0\t0\t\n", encoding="utf-8",
                    newline="")  # fmt: skip
    got = oc.read_steps(path)
    assert [(s["name"], s["result"]) for s in got] == [("help", "fail"), ("live", "ok")]
    assert got[0]["detail"] == f"flag{sep}gone"
    assert oc.decide([*_all_ok()[:-2], got[0], *_all_ok()[-2:]], {}, False)[:2] == ("FAIL", "help")


@pytest.mark.parametrize("oom", ["unknown", "", "x", "-1", "NaN", "1e3"])
def test_read_steps_unmeasured_oom_is_setup(oom: str, tmp_path: Path) -> None:
    """An unmeasured oom_kill (memory.events unreadable) is never 'no OOM' (slice 3 audit, 1)."""
    path = tmp_path / "steps.tsv"
    path.write_text(f"live\t0\t3.2\t{oom}\t\nhelp\t0\t1.0\t0\t\n", encoding="utf-8")
    got = oc.read_steps(path)
    assert [(s["name"], s["result"]) for s in got] == [("live", "setup"), ("help", "ok")]
    assert "oom_kill not measured" in got[0]["detail"]


def test_the_shell_records_an_unmeasured_oom_as_unknown() -> None:
    """step() starts the oom delta at `unknown` and sets a number only from two numeric reads."""
    text = SCRIPT.read_text(encoding="utf-8")
    body = text[text.index("step() {") : text.index("\n}\n", text.index("step() {"))]
    assert "delta=unknown" in body and "delta=0" not in body
    assert "[[ $o0 =~ ^[0-9]+$ && $o1 =~ ^[0-9]+$ ]]" in body
    script = (
        "o0=unknown o1=3 delta=unknown\n"
        + body[body.index("    if [[ $o0") : body.index("    if [[ $rc")]
        + 'printf "%s" "$delta"\n'
    )
    out = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=True)
    assert out.stdout == "unknown"


GREEN_DIFF = {"ok": True, **{key: [] for key in oc.HELP_DIFF_LISTS}}


def _green_state(state: Path, seed: bool = False) -> None:
    """A state dir whose every required step and input is present and ok, as the steps write it."""
    state.mkdir(parents=True, exist_ok=True)
    names = oc.SEED_REQUIRED_STEPS if seed else oc.REQUIRED_STEPS
    (state / "steps.tsv").write_text("".join(f"{n}\t0\t1\t0\t\n" for n in names), "utf-8")
    oc.write_json(
        state / "help.json",
        {
            "commands": [{"words": ["scope"], "flags": []}],
            "per_baseline": {} if seed else {"0.7.16": {"scope": GREEN_DIFF}},
            "setup": [],
            "problems": [],
        },
    )
    (state / "help").mkdir(exist_ok=True)
    (state / "help" / "scope.txt").write_text(PARENT, encoding="utf-8")
    if not seed:
        oc.write_json(state / "watched.json", {"watched_diff": {}, "setup": []})
        cmp = {"ok": True, "problems": [], "json_paths_added": {}}
        oc.write_json(
            state / "captures.json",
            {"per_baseline": {"0.7.16": cmp}, "json_paths_added": {}, "setup": [], "problems": []},
        )
        oc.write_json(state / "baselines.json", {"baseline": "0.7.16", "person_baseline": "0.7.16"})


def _ns(tmp_path: Path, seed: bool = False) -> argparse.Namespace:
    return argparse.Namespace(
        state=str(tmp_path / "state"),
        evidence=str(tmp_path / "evidence"),
        version="0.7.21",
        integrity="local",
        rig_version_output="",
        package=str(tmp_path / "no"),
        tree_sha="",
        root_alias=[],
        seed=seed,
    )


def test_report_writes_the_verdict_and_exits_with_its_code(tmp_path: Path, capsys) -> None:
    """report: verdict.json with the §5.4 keys, REPORT.md, the final line and the exit code."""
    state, evidence, ns = tmp_path / "state", tmp_path / "evidence", _ns(tmp_path)
    _green_state(state)
    assert oc.report(ns) == 0
    assert capsys.readouterr().out.splitlines()[-1] == f"openrig-compat 0.7.21: PASS ({evidence})"
    (state / "captures.json").write_text('{"json_paths_added": NaN}', encoding="utf-8")
    assert oc.report(ns) == 2  # the hostile captures.json is a setup error, not a REVIEW
    verdict = json.loads((evidence / "verdict.json").read_text())
    keys = ("version", "verdict", "failed_step", "baseline", "person_baseline", "watched_diff",
            "help_diff", "json_paths_added", "setup_error", "timings")  # fmt: skip
    assert all(key in verdict for key in keys)
    assert verdict["verdict"] == "SETUP-ERROR"
    _green_state(state)
    review = {"watched_diff": {"scope-audit.js": {"0.7.16": "differs"}}, "setup": []}
    oc.write_json(state / "watched.json", review)
    assert oc.report(ns) == 3
    assert capsys.readouterr().out.splitlines()[-1] == f"openrig-compat 0.7.21: REVIEW ({evidence})"
    assert "| live | ok | 0 |" in (evidence / "REPORT.md").read_text()
    with (state / "steps.tsv").open("a", encoding="utf-8") as fh:
        fh.write("help\t1\t1\t0\tx\n")
    assert oc.report(ns) == 1
    assert capsys.readouterr().out.strip().endswith(f"FAIL help ({evidence})")
    _green_state(tmp_path / "seed" / "state", seed=True)
    assert oc.report(_ns(tmp_path / "seed", seed=True)) == oc.VERDICT_EXIT["SEED"]


@pytest.mark.parametrize("value", [[1], "x", 1, None, True])
@pytest.mark.parametrize("name", ["watched.json", "help.json", "captures.json", "baselines.json"])
def test_report_non_object_state_json_is_setup(name: str, value: object, tmp_path: Path) -> None:
    """Audit iter 1 item 2: a state JSON that is not an object is a setup step, never {}."""
    _green_state(tmp_path / "state")
    (tmp_path / "state" / name).write_text(json.dumps(value), encoding="utf-8")
    assert oc.report(_ns(tmp_path)) == oc.VERDICT_EXIT["SETUP-ERROR"]
    verdict = json.loads((tmp_path / "evidence" / "verdict.json").read_text())
    assert verdict["setup_error"].startswith(name)


WRONG_TYPES = [None, True, 0, 1.5, "", "x", [], [1], {}, {"x": 1}]


def _schema_breaks(green: dict) -> list[object]:
    """Every whole-file and per-key way to break a green state JSON (the schema sweep)."""
    out: list[object] = [{}, [], None, "", 0, "x"]
    for key in green:
        out.append({k: v for k, v in green.items() if k != key})  # the key missing
        out += [{**green, key: bad} for bad in WRONG_TYPES if bad != green[key]
                and not (key in ("setup", "problems") and bad == [])]  # fmt: skip
    return out


@pytest.mark.parametrize(
    ("name", "seed"),
    [
        ("watched.json", False),
        ("captures.json", False),
        ("help.json", False),
        ("baselines.json", False),
        ("help.json", True),
    ],  # fmt: skip
)
def test_audit_iter2_empty_object_repro(name: str, seed: bool, tmp_path: Path) -> None:
    """Audit iter 2 item 1: a required state JSON that is `{}` is SETUP-ERROR, never green."""
    _green_state(tmp_path / "state", seed)
    (tmp_path / "state" / name).write_text("{}", encoding="utf-8")
    assert oc.report(_ns(tmp_path, seed)) == oc.VERDICT_EXIT["SETUP-ERROR"]
    verdict = json.loads((tmp_path / "evidence" / "verdict.json").read_text())
    assert verdict["verdict"] == "SETUP-ERROR" and verdict["setup_error"].startswith(name)


def _sweep_cases(seed: bool) -> list[tuple[str, object]]:
    """Each way of taking one required input away from an otherwise green state dir."""
    files = ["help.json"] if seed else ["watched.json", "help.json", "captures.json",
                                        "baselines.json"]  # fmt: skip
    names = oc.SEED_REQUIRED_STEPS if seed else oc.REQUIRED_STEPS
    cases: list[tuple[str, object]] = [("step", n) for n in names]
    cases += [("delete", f) for f in files] + [("steps.tsv", None)]
    cases += [("truncate", f) for f in files]
    if seed:  # the captured texts become the next baseline: each must be whole
        cut = PARENT.split("\nCommands:")[0] + "\n"
        cases += [("help-text", ""), ("help-text", cut), ("delete", "help/scope.txt")]
    cases += [("help.json", {"commands": [], "per_baseline": {}})]
    probe = Path(os.environ.get("TMPDIR", "/tmp")) / f"compat-sweep-{os.getpid()}-{seed}"
    _green_state(probe, seed)
    for name in files:
        green = json.loads((probe / name).read_text())
        cases += [(name, bad) for bad in _schema_breaks(green)]
    if not seed:  # one field of a green file changed: each schema rule on its own
        help_green = json.loads((probe / "help.json").read_text())
        cap_green = json.loads((probe / "captures.json").read_text())
        diff = help_green["per_baseline"]["0.7.16"]["scope"]
        cmp = cap_green["per_baseline"]["0.7.16"]
        helps = [{"0.7.16": {}}, {"0.7.16": {"scope": {**diff, "ok": False}}},
                 {"0.7.16": {"scope": {"ok": True}}}, {"0.7.16": {"scope": []}}]  # fmt: skip
        helps += [{"0.7.16": {"scope": {**diff, k: bad}}}
                  for k in oc.HELP_DIFF_LISTS for bad in (None, "x", [1])]  # fmt: skip
        cases += [("help.json", {**help_green, "per_baseline": per}) for per in helps]
        cases += [("help.json", {**help_green, "problems": ["vs 0.7.16: x"]})]
        caps = [{"0.7.16": {}}, {"0.7.16": {**cmp, "ok": False}}, {"0.7.16": []},
                {"0.7.16": {**cmp, "problems": [1]}}, {"0.7.16": {**cmp, "ok": "yes"}},
                {"0.7.16": {**cmp, "json_paths_added": {"01-a.json": []}}}]  # fmt: skip
        cases += [("captures.json", {**cap_green, "per_baseline": per}) for per in caps]
        cases += [("captures.json", {**cap_green, "problems": ["vs 0.7.16: x"]})]
        cases += [("watched.json", {"watched_diff": {}, "setup": ["x"]})]
        cases += [("watched.json", {"watched_diff": bad, "setup": []}) for bad in (
            {"scope-audit.js": {}}, {"nope.js": {"0.7.16": "differs"}},
            {"scope-audit.js": {"0.7.16": "x"}}, {"scope-audit.js": "differs"})]  # fmt: skip
    shutil.rmtree(probe)
    if not seed:
        cases += [
            ("watched.json", {"setup": []}),
            ("watched.json", {"watched_diff": []}),
            ("watched.json", {"watched_diff": "x"}),
            ("captures.json", {"json_paths_added": {}}),
            ("captures.json", {"per_baseline": {"0.7.21": {}}}),
            ("help.json", {"commands": [{"words": ["scope"]}], "per_baseline": {}}),
            ("baselines.json", {"baseline": "0.7.16"}),
            ("baselines.json", {"baseline": "", "person_baseline": ""}),
        ]  # fmt: skip
    return cases


@pytest.mark.parametrize("seed", [False, True], ids=["compare", "seed"])
def test_report_sweep_never_green_without_every_input(seed: bool, tmp_path: Path) -> None:
    """The sweep through report(): remove or break each required step and input in turn.

    The result is SETUP-ERROR every time, never PASS, REVIEW or SEED.
    """
    green = "SEED" if seed else "PASS"
    _green_state(tmp_path / "base" / "state", seed)
    assert oc.report(_ns(tmp_path / "base", seed)) == oc.VERDICT_EXIT[green]
    for i, (what, arg) in enumerate(_sweep_cases(seed)):
        state = tmp_path / str(i) / "state"
        _green_state(state, seed)
        if what == "step":
            rows = (state / "steps.tsv").read_text().splitlines(keepends=True)
            (state / "steps.tsv").write_text("".join(r for r in rows if r.split("\t")[0] != arg))
        elif what == "delete":
            (state / str(arg)).unlink()
        elif what == "truncate":
            text = (state / str(arg)).read_text()
            (state / str(arg)).write_text(text[: len(text) // 2])
        elif what == "help-text":
            (state / "help" / "scope.txt").write_text(str(arg))
        elif arg is None and what == "steps.tsv":
            (state / what).unlink()
        else:
            (state / what).write_text(json.dumps(arg), encoding="utf-8")
        rc = oc.report(_ns(tmp_path / str(i), seed))
        assert rc == oc.VERDICT_EXIT["SETUP-ERROR"], (what, arg, rc)
        shutil.rmtree(tmp_path / str(i))


# ---------------------------------------------------------------------------------------------
# Baselines and the clone's contract


def test_select_baselines(tmp_path: Path) -> None:
    """Newest same-minor dir <= V, and the newest person-accepted one; none names --seed."""
    root = _baseline_root(tmp_path, {"0.7.16": "person", "0.7.21": "person", "0.7.22": "watcher"})
    with pytest.raises(oc.CompatInputError, match="--seed"):
        oc.select_baselines(root, "0.7.21")
    for v in ("0.7.16", "0.7.22", "0.6.13", "0.7.30", "junk", "0.7.016"):
        (oc.baseline_dir(root, "0.7.16").parent / v).mkdir(parents=True)
    assert oc.select_baselines(root, "0.7.21") == {
        "baseline": "0.7.16",
        "person_baseline": "0.7.16",
    }
    assert oc.select_baselines(root, "0.7.25") == {
        "baseline": "0.7.22",
        "person_baseline": "0.7.16",
    }
    with pytest.raises(oc.CompatInputError, match="--seed"):
        oc.select_baselines(root, "0.8.0")
    (root / ".agents/config/openrig_contract.yml").write_text("accepted: [", encoding="utf-8")
    with pytest.raises(oc.CompatInputError, match="openrig_contract.yml"):
        oc.select_baselines(root, "0.7.21")


def test_contract_edits_in_the_clone(tmp_path: Path) -> None:
    """Unlist a listed version (when its minor keeps another), add an unlisted one as watcher."""
    root = _baseline_root(tmp_path, {"0.6.13": "person", "0.7.16": "person", "0.7.21": "person"})
    path = root / ".agents/config/openrig_contract.yml"
    before = path.read_bytes()
    assert oc.contract_unlist(path, root, "0.7.21") == "unlisted"
    assert "0.7.21" not in oc.load_contract_entries(root)
    assert oc.contract_unlist(path, root, "0.7.21") == "not-listed"
    assert oc.contract_unlist(path, root, "0.7.16") == "sole"  # the range needs a 0.7 entry
    path.write_bytes(before)
    oc.contract_add_watcher(path, root, "0.7.22", "local", "openrig-compat-run")
    assert oc.load_contract_entries(root)["0.7.22"] == "watcher"
    with pytest.raises(oc.CompatInputError):
        oc.contract_add_watcher(path, root, "0.7.23", 'x", accepted_by: person}', "v")
    with pytest.raises(oc.CompatInputError):
        oc.contract_add_watcher(path, root, "0.7.22", "local", "v")  # duplicate


def test_copy_tree_refuses_escapes(tmp_path: Path) -> None:
    """Only repo-relative paths inside the tree are copied; missing ones are skipped."""
    src, dst = tmp_path / "src", tmp_path / "dst"
    (src / "a").mkdir(parents=True)
    (src / "a" / "f.txt").write_text("x", encoding="utf-8")
    os.symlink("f.txt", src / "a" / "link")
    assert oc.copy_tree(src, dst, b"a/f.txt\0a/link\0gone.txt\0") == 2
    assert (dst / "a" / "link").is_symlink()
    for bad in (b"../x\0", b"/etc/passwd\0"):
        with pytest.raises(oc.CompatInputError):
            oc.copy_tree(src, dst, bad)


# ---------------------------------------------------------------------------------------------
# Host state


def test_openrig_home_list_filter(tmp_path: Path) -> None:
    """Regular files directly in the dir, with the daemon's logs and databases left out."""
    home = tmp_path / ".openrig"
    (home / "sub").mkdir(parents=True)
    names = ["config.json", "daemon.json", "daemon.log", "openrig.sqlite", "openrig.sqlite-wal",
             "x.db", "x.db-journal", "daemon.pid", "hosts.yaml"]  # fmt: skip
    for name in names:
        (home / name).write_text("x", encoding="utf-8")
    (home / "sub" / "deep.json").write_text("x", encoding="utf-8")
    os.symlink("config.json", home / "link.json")
    rows = oc.openrig_home_list(home)
    assert [r.split()[0].rsplit("/", 1)[1] for r in rows] == [
        "config.json", "daemon.json", "hosts.yaml"
    ]  # fmt: skip
    assert all(len(r.split()) == 3 for r in rows)
    assert oc.openrig_home_list(tmp_path / "absent") == []


def test_oom_kill_count(tmp_path: Path) -> None:
    """memory.events of the process's cgroup is read defensively."""
    cg, root = tmp_path / "cgroup", tmp_path / "sys"
    (root / "user.slice" / "run-1.scope").mkdir(parents=True)
    cg.write_text("0::/user.slice/run-1.scope\n", encoding="utf-8")
    events = root / "user.slice" / "run-1.scope" / "memory.events"
    events.write_text("low 0\nhigh 0\nmax 4\noom 2\noom_kill 3\n", encoding="utf-8")
    assert oc.oom_kill_count(cg, root) == 3
    events.write_text("oom_kill NaN\n", encoding="utf-8")
    assert oc.oom_kill_count(cg, root) is None
    cg.write_text("0::/../../etc\n", encoding="utf-8")
    assert oc.oom_kill_count(cg, root) is None


def test_listener_records_a_connection(tmp_path: Path) -> None:
    """The loopback listener logs every accepted connection, and survives being stopped."""
    port_file, log = tmp_path / "port", tmp_path / "conn.log"
    proc = subprocess.Popen(  # noqa: S603 - our own module
        [sys.executable, str(MODULE), "listen", "--port-file", str(port_file), "--log", str(log)]
    )
    try:
        for _ in range(100):
            if port_file.exists() and port_file.read_text().strip():
                break
            time.sleep(0.05)
        port = int(port_file.read_text())
        assert oc.connection_count(log) == 0
        with socket.create_connection(("127.0.0.1", port), timeout=5):
            pass
        for _ in range(100):
            if oc.connection_count(log) == 1:
                break
            time.sleep(0.05)
    finally:
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=10)
    assert oc.connection_count(log) == 1
    assert oc.connection_count(tmp_path / "missing") is None


# ---------------------------------------------------------------------------------------------
# The shell script's refusals (all before any install)


FALLTHROUGH_STUB = """#!/bin/sh
echo "$(basename "$0") $*" >>"$STUB_LOG"
exit 1
"""


def _sandbox(tmp_path: Path) -> dict[str, str]:
    """A fake HOME and stub npm/npx/node/uv/rig first on PATH (each logs and exits 1).

    A refusal that failed to refuse can then never install a real candidate or write under the
    real HOME: it reaches a stub, which the caller sees in the log (slice 3 audit, item 4).
    """
    stubs = tmp_path / "fallthrough-stubs"
    stubs.mkdir(exist_ok=True)
    for name in ("npm", "npx", "node", "uv", "rig"):
        (stubs / name).write_text(FALLTHROUGH_STUB, encoding="utf-8")
        (stubs / name).chmod(0o755)
    (tmp_path / "fake-home").mkdir(exist_ok=True)
    return {"HOME": str(tmp_path / "fake-home"), "PATH": f"{stubs}:/usr/bin:/bin",
            "STUB_LOG": str(tmp_path / "fallthrough.log")}  # fmt: skip


def _run_script(
    args: list[str], env_tmpdir: str | None, tmp_path: Path, extra: dict[str, str] | None = None
) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items()
           if k not in ("TMPDIR", "HACF_COMPAT_EVIDENCE_ROOT", "UV_CACHE_DIR",
                        "XDG_STATE_HOME")}  # fmt: skip
    env.update(_sandbox(tmp_path))
    env.update(extra or {})
    if env_tmpdir is not None:
        env["TMPDIR"] = env_tmpdir
    return subprocess.run(  # noqa: S603
        ["bash", str(SCRIPT), *args], capture_output=True, text=True, env=env, timeout=60
    )


def _fell_through(tmp_path: Path) -> list[str]:
    log = tmp_path / "fallthrough.log"
    return log.read_text().splitlines() if log.exists() else []


@pytest.mark.parametrize(
    ("args", "needle"),
    [
        (["0.7.21", "--accept", "--seed"], "exclude each other"),
        (["0.7.21", "--frobnicate"], "unknown option"),
        ([], "a version is required"),
        (["0.7.21", "0.7.22"], "one version only"),
        (["v0.7.21"], "not a plain M.m.p"),
        (["0.7.21", "--registry", "file:///etc"], "--registry"),
        (["0.7.21", "--package-dir", "/nonexistent-dir"], "not a directory"),
    ],
)
def test_script_rejects_bad_arguments(args: list[str], needle: str, tmp_path: Path) -> None:
    """Usage errors exit 2, before anything is installed (--accept itself is slice 4's)."""
    proc = _run_script(args, str(tmp_path), tmp_path)
    assert proc.returncode == 2, proc.stderr
    assert needle in proc.stderr
    assert _fell_through(tmp_path) == []


def _tmpfs_dir() -> str | None:
    for candidate in ("/dev/shm", f"/run/user/{os.getuid()}"):
        if os.path.isdir(candidate) and os.access(candidate, os.W_OK):
            out = subprocess.run(["stat", "-f", "-c", "%T", candidate], capture_output=True,
                                 text=True, check=False)  # fmt: skip
            if out.stdout.strip() == "tmpfs":
                return candidate
    return None


def test_script_refuses_tmp_tmpfs_and_unset_tmpdir(tmp_path: Path) -> None:
    """TMPDIR unset, /tmp or on a tmpfs: exit 2 before anything is installed.

    Each run is sandboxed (fake HOME, stub npm first on PATH): a refusal that fell through would
    reach the stub, never a real install under the real HOME.
    """
    proc = _run_script(["0.7.21"], None, tmp_path)
    assert proc.returncode == 2 and "TMPDIR is not set" in proc.stderr
    proc = _run_script(["0.7.21"], "/tmp", tmp_path)
    assert proc.returncode == 2 and "TMPDIR is /tmp" in proc.stderr
    assert _fell_through(tmp_path) == []
    tmpfs = _tmpfs_dir()
    if tmpfs is None:
        pytest.skip("no writable tmpfs on this host")
    proc = _run_script(["0.7.21"], tmpfs, tmp_path)
    assert proc.returncode == 2 and "tmpfs" in proc.stderr
    assert _fell_through(tmp_path) == []
    assert list((tmp_path / "fake-home").iterdir()) == []


def _mem_available_mb() -> int:
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) // 1024
    return 0


def test_the_sandbox_catches_a_fall_through(tmp_path: Path) -> None:
    """The control for the refusal tests: an accepted TMPDIR reaches the stub npm, not a real one.

    It ends as a SETUP-ERROR (install), with its evidence under the fake HOME.
    """
    if _mem_available_mb() < 6144:
        pytest.skip("MemAvailable < 6144 MB: the script stops at its own memory check")
    ondisk = tmp_path / "ondisk"
    ondisk.mkdir()
    proc = _run_script(["0.7.21"], str(ondisk), tmp_path)
    assert proc.returncode == 2, proc.stderr
    assert any(line.startswith("npm i ") for line in _fell_through(tmp_path))
    assert "openrig-compat 0.7.21: SETUP-ERROR" in proc.stdout
    evidence = list((tmp_path / "fake-home/.local/state/hacf/evidence").iterdir())
    assert len(evidence) == 1 and str(evidence[0]) in proc.stdout


@pytest.mark.parametrize("source", ["env", "xdg"])
def test_the_evidence_root_setting_and_the_xdg_default(source: str, tmp_path: Path) -> None:
    """HACF_COMPAT_EVIDENCE_ROOT wins over XDG_STATE_HOME; the HOME default is the test above."""
    if _mem_available_mb() < 6144:
        pytest.skip("MemAvailable < 6144 MB: the script stops at its own memory check")
    ondisk = tmp_path / "ondisk"
    ondisk.mkdir()
    extra = {"XDG_STATE_HOME": str(tmp_path / "xdg")}
    if source == "env":
        extra["HACF_COMPAT_EVIDENCE_ROOT"] = str(tmp_path / "env-root")
    want = tmp_path / "env-root" if source == "env" else tmp_path / "xdg/hacf/evidence"
    proc = _run_script(["0.7.21"], str(ondisk), tmp_path, extra)
    assert proc.returncode == 2, proc.stderr
    evidence = list(want.iterdir())
    assert len(evidence) == 1 and str(evidence[0]) in proc.stdout
    assert not (tmp_path / "fake-home/.local/state").exists()
    if source == "env":
        assert not (tmp_path / "xdg").exists()


def test_no_shipped_file_reads_the_live_overrides() -> None:
    """HACF_LIVE_ACCEPT_EXTRA and HACF_LIVE_RECORD_DIR are dev-only (R13)."""
    hits = []
    for path in (REPO_ROOT / ".agents").rglob("*"):
        if path.is_file() and not path.is_symlink():
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            hits += [f"{path}: {n}" for n in ("HACF_LIVE_ACCEPT_EXTRA", "HACF_LIVE_RECORD_DIR")
                     if n in text]  # fmt: skip
    assert hits == []
