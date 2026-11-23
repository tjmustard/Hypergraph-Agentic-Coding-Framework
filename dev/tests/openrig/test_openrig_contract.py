"""Tests for hyper_openrig.load_contract(): the fail-closed .agents/config/openrig_contract.yml.

Every malformed-file case drops the whole file to FALLBACK_RIG_VERSIONS with no range, and the
fallback is never wider than that tuple (release-0.6.2 R3). Every file a test writes is under
tmp_path; nothing here runs rig.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import hyper_openrig
import pytest

REPO = Path(__file__).resolve().parents[3]
SHIPPED = REPO / ".agents" / "config" / "openrig_contract.yml"
SCRIPT = REPO / ".agents" / "scripts" / "hyper_openrig.py"

E613 = '{version: "0.6.13", integrity: "sha512-a", verdict: "v", accepted_by: person}'
E716 = '{version: "0.7.16", integrity: "sha512-b", verdict: "v", accepted_by: person}'
E721 = '{version: "0.7.21", integrity: "sha512-c", verdict: "v", accepted_by: person}'
RANGE = "{major: 0, minor: 7, min_patch: 16}"


def _doc(
    entries: tuple[str, ...] = (E613, E716, E721),
    ranges: tuple[str, ...] = (RANGE,),
    head: str = "schema: 1\n",
    tail: str = "probe: {schema: 1}\n",
) -> str:
    accepted = "".join(f"  - {e}\n" for e in entries)
    rng = "".join(f"  - {r}\n" for r in ranges) if ranges else ""
    return f"{head}accepted:\n{accepted}range:{' []' if not ranges else ''}\n{rng}{tail}"


VALID = _doc()

# A billion-laughs document under the valid top-level keys: 9**7 references if expanded.
_LEVELS = ['&a ["x","x","x","x","x","x","x","x","x"]'] + [
    f"&{chr(98 + i)} [{','.join([f'*{chr(97 + i)}'] * 9)}]" for i in range(6)
]
BOMB = f"schema: 1\naccepted: [{', '.join(_LEVELS)}]\nrange: []\nprobe: {{schema: 1}}\n"

CASES = {
    "empty_file": "",
    "not_a_mapping": "- 0.6.13\n- 0.7.16\n",
    "schema_2": VALID.replace("schema: 1\n", "schema: 2\n", 1),
    "schema_bool": VALID.replace("schema: 1\n", "schema: true\n", 1),
    "unknown_top_level_key": VALID + "min_hacf: 0.6.2\n",
    "missing_top_level_key": VALID.replace("probe: {schema: 1}\n", ""),
    "entry_missing_key": _doc(entries=(E613, E716, E721.replace(', verdict: "v"', ""))),
    "entry_extra_key": _doc(entries=(E613, E716, E721.replace("}", ", note: x}"))),
    "version_yaml_float": _doc(entries=(E613, E716.replace('"0.7.16"', "0.7"))),
    "version_unquoted": _doc(entries=(E613, E716, E721.replace('"0.7.21"', "0.10.1"))),
    "version_unquoted_plain": _doc(entries=(E613, E716, E721.replace('"0.7.21"', "0.7.21"))),
    "version_leading_zero": _doc(entries=(E613, E716, E721.replace("0.7.21", "0.7.016"))),
    "version_prerelease": _doc(entries=(E613, E716, E721.replace("0.7.21", "0.7.22-rc.1"))),
    "version_v_prefix": _doc(entries=(E613, E716, E721.replace("0.7.21", "v0.7.21"))),
    "duplicate_version": _doc(entries=(E613, E716, E716)),
    "accepted_by_robot": _doc(entries=(E613, E716, E721.replace("person", "robot"))),
    "accepted_empty": _doc(entries=(), ranges=()),
    "range_minor_without_version": _doc(ranges=(RANGE, "{major: 0, minor: 8, min_patch: 0}")),
    "two_ranges_one_minor": _doc(ranges=(RANGE, "{major: 0, minor: 7, min_patch: 20}")),
    "range_negative_min_patch": _doc(ranges=("{major: 0, minor: 7, min_patch: -1}",)),
    "range_string_int": _doc(ranges=('{major: 0, minor: 7, min_patch: "16"}',)),
    "probe_schema_zero": _doc(tail="probe: {schema: 0}\n"),
    "probe_extra_key": _doc(tail="probe: {schema: 1, cache: x}\n"),
    "yaml_error": "schema: [\n",
    "too_large_65_kib": VALID + "#" + "x" * (65 * 1024) + "\n",
    "alias_bomb": BOMB,
    "recursive_alias": "schema: 1\naccepted: &a [*a]\nrange: []\nprobe: {schema: 1}\n",
    "schema_float": VALID.replace("schema: 1\n", "schema: 1.0\n", 1),
    "invalid_unquoted_date": _doc(
        entries=(E613, E716, E721.replace('verdict: "v"', "verdict: 2026-02-30"))
    ),
    "deep_nesting_within_cap": "schema: 1\naccepted: " + "[" * 20000 + "]" * 20000 + "\n",
    "not_utf8": None,  # written as bytes below
}


def _assert_fallback(contract: hyper_openrig.Contract, path: Path) -> None:
    assert contract.source == "fallback"
    assert contract.versions == hyper_openrig.FALLBACK_RIG_VERSIONS
    assert all(e.accepted_by == "person" and e.integrity == "" for e in contract.accepted)
    assert contract.range == ()
    assert contract.probe_schema == 1
    assert set(contract.versions) <= set(hyper_openrig.FALLBACK_RIG_VERSIONS), "never wider"
    assert contract.warning and str(path) in contract.warning
    assert "\n" not in contract.warning


@pytest.mark.parametrize("case", sorted(CASES))
def test_malformed_contract_falls_back(tmp_path: Path, case: str) -> None:
    """Each malformed file drops whole to the fallback set, no range, never wider."""
    path = tmp_path / "openrig_contract.yml"
    if CASES[case] is None:
        path.write_bytes(b"schema: 1\n\xff\xfe\n")
    else:
        path.write_text(CASES[case], encoding="utf-8")
    contract = hyper_openrig.load_contract(path)
    _assert_fallback(contract, path)
    assert hyper_openrig.contract_rig_version(contract) == "0.7.21"


def test_missing_contract_falls_back(tmp_path: Path) -> None:
    """A missing file is the same fallback."""
    path = tmp_path / "absent.yml"
    contract = hyper_openrig.load_contract(path)
    _assert_fallback(contract, path)
    assert "missing" in contract.warning


def test_read_error_falls_back(tmp_path: Path) -> None:
    """A read error (a directory at the path) is the same fallback."""
    path = tmp_path / "openrig_contract.yml"
    path.mkdir()  # a directory at the path: open() fails
    contract = hyper_openrig.load_contract(path)
    _assert_fallback(contract, path)
    assert "cannot read" in contract.warning


def test_fallback_never_widens_and_never_stops() -> None:
    """A fallback still delegates the person set, keeps 0.7.x native outside it, stops 0.8."""
    fallback = hyper_openrig.load_contract(Path("/nonexistent/openrig_contract.yml"))
    assert hyper_openrig.version_decision("0.7.21 (x)", fallback) == ("delegate", None)
    mode, warning = hyper_openrig.version_decision("0.7.40", fallback)
    assert mode == "native" and "probed range" not in warning
    assert hyper_openrig.version_decision("0.8.0", fallback)[0] == "stop"


def test_warning_printed_once_per_process(tmp_path: Path) -> None:
    """Three fallback loads in one process print one warning line."""
    code = (
        "import sys; from pathlib import Path; sys.path.insert(0, sys.argv[1]); "
        "import hyper_openrig as h; "
        "[h.load_contract(Path(sys.argv[2])) for _ in range(3)]"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code, str(SCRIPT.parent), str(tmp_path / "absent.yml")],
        capture_output=True,
        text=True,
        check=True,
    )
    assert proc.stderr.count("hyper_openrig: warning:") == 1
    assert "absent.yml: missing" in proc.stderr


@pytest.mark.parametrize("case", ["invalid_unquoted_date", "deep_nesting_within_cap", "yaml_error"])
def test_installed_bad_contract_never_stops_a_command(tmp_path: Path, case: str) -> None:
    """A bad installed contract file: the adapter still imports, warns once, and commands run."""
    agents = tmp_path / ".agents"
    shutil.copytree(REPO / ".agents" / "scripts", agents / "scripts")
    (agents / "config").mkdir()
    (agents / "config" / "openrig_contract.yml").write_text(CASES[case], encoding="utf-8")
    proc = subprocess.run(
        [
            sys.executable,
            str(agents / "scripts" / "hyper_openrig.py"),
            "contract",
            "show",
            "--json",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stderr.count("hyper_openrig: warning:") == 1, proc.stderr
    assert "Traceback" not in proc.stderr
    data = json.loads(proc.stdout)
    assert data["source"] == "fallback" and data["range"] == []
    assert [e["version"] for e in data["accepted"]] == list(hyper_openrig.FALLBACK_RIG_VERSIONS)
    spec = subprocess.run(
        [sys.executable, str(agents / "scripts" / "hyper_spec.py"), "--help"],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert spec.returncode == 0, spec.stderr


def test_valid_document_parses_as_file(tmp_path: Path) -> None:
    """The well-formed test document parses as `file`."""
    path = tmp_path / "openrig_contract.yml"
    path.write_text(VALID, encoding="utf-8")
    contract = hyper_openrig.load_contract(path)
    assert contract.source == "file" and contract.warning is None
    assert contract.versions == ("0.6.13", "0.7.16", "0.7.21")
    assert contract.range == ((0, 7, 16),)


def test_watcher_entry_does_not_move_rig_version(tmp_path: Path) -> None:
    """A watcher entry is accepted but RIG_VERSION stays the highest person entry (R4)."""
    watcher = '{version: "0.7.22", integrity: "sha512-d", verdict: "v", accepted_by: watcher}'
    path = tmp_path / "openrig_contract.yml"
    path.write_text(_doc(entries=(E613, E716, E721, watcher)), encoding="utf-8")
    contract = hyper_openrig.load_contract(path)
    assert contract.source == "file" and contract.versions[-1] == "0.7.22"
    assert hyper_openrig.contract_rig_version(contract) == "0.7.21"
    assert hyper_openrig.version_decision("0.7.22", contract) == ("delegate", None)


def test_shipped_contract() -> None:
    """The shipped file parses as `file`: three person entries and one range."""
    contract = hyper_openrig.load_contract(SHIPPED)
    assert contract.source == "file" and contract.warning is None
    people = [e for e in contract.accepted if e.accepted_by == "person"]
    assert [e.version for e in people] == ["0.6.13", "0.7.16", "0.7.21"]
    assert all(e.integrity.startswith("sha512-") and e.verdict for e in people)
    assert contract.range == ((0, 7, 16),)
    assert contract.probe_schema == 1
    assert hyper_openrig.load_contract() == contract, "the default path is the shipped file"


def test_fallback_equals_shipped_person_set() -> None:
    """F17: the compiled-in tuple is the shipped file's accepted_by: person versions, in order."""
    contract = hyper_openrig.load_contract(SHIPPED)
    people = tuple(e.version for e in contract.accepted if e.accepted_by == "person")
    assert hyper_openrig.FALLBACK_RIG_VERSIONS == people


def test_rig_version_is_highest_person_entry() -> None:
    """RIG_VERSION and ACCEPTED_RIG_VERSIONS come from the shipped file at import."""
    assert hyper_openrig.RIG_VERSION == "0.7.21"
    assert hyper_openrig.ACCEPTED_RIG_VERSIONS == hyper_openrig.load_contract(SHIPPED).versions


def _show(*args: str) -> subprocess.CompletedProcess:
    # No `rig` on PATH: `contract show` resolves rig (slice 2), and tests never run the host's.
    dirs = os.environ.get("PATH", "").split(os.pathsep)
    path = os.pathsep.join(d for d in dirs if d and not (Path(d) / "rig").exists())
    return subprocess.run(
        [sys.executable, str(SCRIPT), "contract", "show", *args],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PATH": path},
    )


def test_contract_show() -> None:
    """`contract show` and `contract show --json` print what HACF will do, exit 0."""
    proc = _show()
    assert proc.returncode == 0, proc.stderr
    assert f"contract: {SHIPPED}" in proc.stdout and "source: file" in proc.stdout
    for version in ("0.6.13", "0.7.16", "0.7.21"):
        assert f"  {version}  accepted_by: person" in proc.stdout
    assert "verdict: openrig-compat-0.7.21-20261123T075801Z" in proc.stdout
    assert "range: 0.7.x (x >= 16)" in proc.stdout
    assert "RIG_VERSION: 0.7.21" in proc.stdout

    proc = _show("--json")
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert set(data) == {
        "path", "source", "warning", "accepted", "range", "rig_version",
        "opt_out", "rig", "decision", "probes",
    }  # fmt: skip
    assert data["path"] == str(SHIPPED) and data["source"] == "file" and data["warning"] is None
    assert [e["version"] for e in data["accepted"]] == ["0.6.13", "0.7.16", "0.7.21"]
    assert data["range"] == [{"major": 0, "minor": 7, "min_patch": 16}]
    assert data["rig_version"] == "0.7.21"
