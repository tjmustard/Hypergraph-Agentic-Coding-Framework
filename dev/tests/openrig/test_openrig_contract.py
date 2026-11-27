"""Tests for hyper_openrig.load_contract(): the fail-closed .agents/config/openrig_contract.yml.

Every malformed-file case drops the whole file to FALLBACK_RIG_VERSIONS with no range, and the
fallback is never wider than that tuple (release-0.6.2 R3). Every file a test writes is under
tmp_path; nothing here runs rig.
"""

from __future__ import annotations

import json
import os
import re
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
    assert hyper_openrig.contract_rig_version(contract) == "0.7.23"


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
    """The shipped file parses as `file`: four person entries and one range."""
    contract = hyper_openrig.load_contract(SHIPPED)
    assert contract.source == "file" and contract.warning is None
    people = [e for e in contract.accepted if e.accepted_by == "person"]
    assert [e.version for e in people] == ["0.6.13", "0.7.16", "0.7.21", "0.7.23"]
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
    contract = hyper_openrig.load_contract(SHIPPED)
    people = [e.version for e in contract.accepted if e.accepted_by == "person"]
    assert hyper_openrig.RIG_VERSION == highest_person(people)
    assert hyper_openrig.ACCEPTED_RIG_VERSIONS == hyper_openrig.load_contract(SHIPPED).versions


def test_the_0_7_23_baseline_holds_the_agy_harness_and_a_review() -> None:
    """R156: the accepted baseline has harness/agy/ and a REVIEW.md, and names no private path."""
    base = REPO / "dev" / "tests" / "fixtures" / "openrig-compat" / "0.7.23"
    assert (base / "harness" / "agy").is_dir() and any((base / "harness" / "agy").iterdir())
    assert (base / "REVIEW.md").is_file()
    roots = ["/" + "home/", "/" + "Users/"]  # built here: this file must hold neither
    needles = [os.environ.get("HOME") or str(Path.home()), *roots]
    files = [p for p in base.rglob("*") if p.is_file()]
    assert files
    hits = [
        f"{p.relative_to(base)}: needle {i}"
        for p in files
        for i, needle in enumerate(needles)
        if needle.encode() in p.read_bytes()
    ]
    assert hits == []


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


MARKER = "HACF_COMPAT_WATCHER_VERSIONS"
PERSON_PINNED = ["0.6.13", "0.7.16", "0.7.21", "0.7.23"]


def _version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def highest_person(versions: list[str]) -> str:
    """The highest version (by tuple) of a list of accepted_by: person versions."""
    return max(versions, key=_version_key)


def check_accepted(accepted: list[dict], marker: str | None) -> str | None:
    """Return a problem when `accepted` is not the pinned person set plus the marker's watchers.

    Without a marker nothing but the pinned person entries is allowed. A marker names the watcher
    entries a compat harness run vouches for: each must be a plain M.m.p, appear exactly once and
    be accepted_by: watcher, and no other entry may exist (OPR.99.0.12.2 MR 3).
    """
    people = [e["version"] for e in accepted if e["accepted_by"] == "person"]
    if people != PERSON_PINNED:
        return f"person entries {people} differ from the pinned {PERSON_PINNED}"
    others = [e for e in accepted if e["accepted_by"] != "person"]
    if marker is None and others:
        return f"unexpected entries without {MARKER}: {[e['version'] for e in others]}"
    if marker is None:
        return None
    names = marker.split(",")
    for name in names:
        if not re.fullmatch(r"\d+\.\d+\.\d+", name):
            return f"{MARKER} holds {name!r}, which is not M.m.p"
    if len(set(names)) != len(names) or len({e["version"] for e in others}) != len(others):
        return "duplicate entries"
    if {e["version"] for e in others} != set(names):
        return (
            f"non-person entries {sorted(e['version'] for e in others)} != marker {sorted(names)}"
        )
    if any(e["accepted_by"] != "watcher" for e in others):
        return "a marker-named entry is not accepted_by: watcher"
    for entry in accepted:
        if entry["version"] in names and entry["accepted_by"] == "person":
            return f"{entry['version']} is named by the marker but accepted_by: person"
    return None


def _entry(version: str, by: str) -> dict:
    return {"version": version, "accepted_by": by}


PEOPLE = [_entry(v, "person") for v in PERSON_PINNED]


def test_check_accepted_cases() -> None:
    """The exact check passes the pinned set and tolerates only the watchers a marker names."""
    assert check_accepted(PEOPLE, None) is None
    for bad in ("", ",", "0.7.22,", ",0.7.22", "0.7.22,,"):
        problem = check_accepted([*PEOPLE, _entry("0.7.22", "watcher")], bad)
        assert problem is not None and "not M.m.p" in problem, bad
    assert check_accepted(PEOPLE, "") is not None
    assert check_accepted([*PEOPLE, _entry("0.7.22", "watcher")], "0.7.22") is None
    assert check_accepted([*PEOPLE, _entry("0.7.22", "watcher")], None) is not None
    assert check_accepted([*PEOPLE, _entry("0.7.22", "watcher")], "0.7.23") is not None
    assert check_accepted(PEOPLE, "0.7.22") is not None
    assert check_accepted([*PEOPLE, _entry("0.7.22", "watcher")], "0.7.2x") is not None
    assert check_accepted([*PEOPLE, _entry("0.7.22", "watcher")], "0.7.22,0.7.22") is not None
    assert check_accepted([*PEOPLE[:2], _entry("0.7.21", "watcher")], "0.7.21") is not None
    assert check_accepted([*PEOPLE, _entry("0.7.22", "person")], "0.7.22") is not None
    assert highest_person(PERSON_PINNED) == "0.7.23"


def test_contract_show() -> None:
    """`contract show` and `contract show --json` print what HACF will do, exit 0."""
    proc = _show()
    assert proc.returncode == 0, proc.stderr
    assert f"contract: {SHIPPED}" in proc.stdout and "source: file" in proc.stdout
    for version in PERSON_PINNED:
        assert f"  {version}  accepted_by: person" in proc.stdout
    assert "verdict: openrig-compat-0.7.21-20261123T075801Z" in proc.stdout
    assert "verdict: openrig-compat-0.7.23-20261126T044840Z" in proc.stdout
    assert "range: 0.7.x (x >= 16)" in proc.stdout
    assert "RIG_VERSION: 0.7.23" in proc.stdout

    proc = _show("--json")
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert set(data) == {
        "path", "source", "warning", "accepted", "range", "rig_version",
        "opt_out", "rig", "decision", "probes",
    }  # fmt: skip
    assert data["path"] == str(SHIPPED) and data["source"] == "file" and data["warning"] is None
    assert check_accepted(data["accepted"], os.environ.get(MARKER)) is None
    assert [e["version"] for e in data["accepted"] if e["accepted_by"] == "person"] == PERSON_PINNED
    assert data["range"] == [{"major": 0, "minor": 7, "min_patch": 16}]
    assert data["rig_version"] == highest_person(PERSON_PINNED)


def test_derived_unlisted_version_is_in_range_and_unlisted() -> None:
    """openrig_versions derives an in-range version no entry lists, whatever the file lists."""
    import openrig_versions

    contract = hyper_openrig.load_contract(SHIPPED)
    version, shown = openrig_versions.unlisted_in_range(contract)
    assert version not in contract.versions and shown.startswith(f"{version} (fake")
    assert hyper_openrig.version_decision(version, contract, "pass") == ("delegate", None)
    patches = openrig_versions.unlisted_patches(contract, 3)
    assert patches[0] == version and len(set(patches)) == 3
    assert not set(patches) & set(contract.versions)
    with pytest.raises(ValueError):
        openrig_versions.unlisted_in_range(contract._replace(range=()))
