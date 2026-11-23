"""The patch rule and the auto-accept of dev/openrig_compat.py (release-0.6.2 slice 4, R15).

Every accept runs in a temp git repo built from a tiny tree (the real hyper_openrig.py and a
contract file); nothing here touches the real repository's branches. The accept trusts only a
schema-valid verdict.json with verdict PASS: every other input is swept and must leave no commit.
"""

from __future__ import annotations

import copy
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "dev"))
import openrig_compat as oc  # noqa: E402

SCRIPT = REPO_ROOT / "dev" / "openrig_compat.sh"
INTEGRITY = "sha512-" + "A" * 86 + "=="
EVIDENCE_NAME = "openrig-compat-0.7.22-20261123T060000Z"


def _shipped_contract(extra: str = "") -> object:
    raw = (REPO_ROOT / oc.CONTRACT_PATH).read_bytes()
    if extra:
        raw = _with_entries(raw.decode(), extra).encode()
    return oc._shipped(REPO_ROOT)._parse_contract(raw)


def _with_entries(text: str, extra: str) -> str:
    """The contract text with extra entry lines after the last accepted entry."""
    lines = text.splitlines(keepends=True)
    last = max(i for i, ln in enumerate(lines) if oc._ENTRY_RE.match(ln))
    return "".join([*lines[: last + 1], extra, *lines[last + 1 :]])


def _watcher_line(version: str) -> str:
    return (f'  - {{version: "{version}", integrity: "{INTEGRITY}", verdict: "x", '
            "accepted_by: watcher}\n")  # fmt: skip


# ---------------------------------------------------------------------------------------------
# Mini-requirement 1: the patch table (mission R15)

PATCH_TABLE = [
    ("0.7.22", True), ("0.8.0", False), ("1.7.22", False), ("0.7.15", False), ("0.7.20", False),
    ("0.7.21", False), ("0.7.22-rc.1", False), ("0.7.22+build.1", False), ("0.7.022", False),
    ("v0.7.22", False), ("", False),
]  # fmt: skip


@pytest.mark.parametrize(("version", "want"), PATCH_TABLE)
def test_patch_rule_against_the_shipped_contract(version: str, want: bool) -> None:
    """Patch rule against the shipped contract."""
    assert oc.is_patch_of_accepted_minor(version, _shipped_contract()) is want


@pytest.mark.parametrize(("version", "want"), [("0.7.23", True), ("0.7.22", False)])
def test_patch_rule_counts_watcher_entries(version: str, want: bool) -> None:
    """Patch rule counts watcher entries."""
    contract = _shipped_contract(_watcher_line("0.7.22"))
    assert oc.is_patch_of_accepted_minor(version, contract) is want


@pytest.mark.parametrize("bad", [None, 7, 0.7, b"0.7.22", ["0.7.22"], "0.7.22\n", " 0.7.22"])
def test_patch_rule_refuses_non_strings(bad: object) -> None:
    """Patch rule refuses non strings."""
    assert oc.is_patch_of_accepted_minor(bad, _shipped_contract()) is False


# ---------------------------------------------------------------------------------------------
# Mini-requirement 2: accept() in a temp git repo


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=True
    ).stdout


def _repo(tmp_path: Path, extra_entries: str = "", branch: str = oc.ACCEPT_BRANCH) -> Path:
    """A tiny tree under git: the real hyper_openrig.py, the shipped contract, one fixture dir."""
    root = tmp_path / "wt"
    (root / ".agents/scripts").mkdir(parents=True)
    (root / ".agents/config").mkdir(parents=True)
    for name in ("hyper_openrig.py", "_runtime.py"):
        if (REPO_ROOT / ".agents/scripts" / name).exists():
            shutil.copy(REPO_ROOT / ".agents/scripts" / name, root / ".agents/scripts")
    text = (REPO_ROOT / oc.CONTRACT_PATH).read_text(encoding="utf-8")
    if extra_entries:
        text = _with_entries(text, extra_entries)
    (root / oc.CONTRACT_PATH).write_text(text, encoding="utf-8")
    (root / oc.FIXTURE_ROOT / "0.7.21").mkdir(parents=True)
    (root / oc.FIXTURE_ROOT / "0.7.21/meta.json").write_text("{}\n", encoding="utf-8")
    (root / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", "-b", branch, str(root)], check=True)
    for key, value in (("user.name", "Test Person"), ("user.email", "test@example.invalid"),
                       ("commit.gpgsign", "false")):  # fmt: skip
        _git(root, "config", key, value)
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "tree")
    return root


def _green_verdict(version: str = "0.7.22") -> dict:
    steps = [{"name": name, "result": "ok", "rc": 0, "seconds": 1.0, "detail": ""}
             for name in oc.REQUIRED_STEPS]  # fmt: skip
    return {
        "version": version, "verdict": "PASS", "failed_step": None, "baseline": "0.7.21",
        "person_baseline": "0.7.21", "watched_diff": {}, "help_diff": {}, "json_paths_added": {},
        "setup_error": None, "timings": {"install": 1.0}, "steps": steps, "integrity": INTEGRITY,
        "problems": {"captures": [], "help": []},
    }  # fmt: skip


def _evidence(tmp_path: Path, verdict: object = None, version: str = "0.7.22",
              raw: bytes | None = None) -> Path:  # fmt: skip
    evidence = tmp_path / "evidence" / EVIDENCE_NAME.replace("0.7.22", version)
    (evidence / "baseline-candidate/captures").mkdir(parents=True)
    (evidence / "baseline-candidate/meta.json").write_text('{"version": "x"}\n', encoding="utf-8")
    (evidence / "baseline-candidate/captures/01-a.json").write_text("{}\n", encoding="utf-8")
    if raw is not None:
        (evidence / "verdict.json").write_bytes(raw)
    elif verdict is not None:
        (evidence / "verdict.json").write_text(json.dumps(verdict), encoding="utf-8")
    return evidence


OK = [sys.executable, "-c", "import sys; sys.exit(0)"]
NOT_OK = [sys.executable, "-c", "import sys; sys.exit(3)"]


def _commits(root: Path) -> int:
    return int(_git(root, "rev-list", "--count", "HEAD").strip())


def test_accept_makes_one_commit_of_exactly_two_paths(tmp_path: Path) -> None:
    """Accept makes one commit of exactly two paths."""
    root = _repo(tmp_path)
    evidence = _evidence(tmp_path, _green_verdict())
    got = oc.accept(
        root, "0.7.22", evidence, OK, expect_head=_git(root, "rev-parse", "HEAD").strip()
    )
    assert got["result"] == "accepted", got
    assert _commits(root) == 2
    assert got["sha"] == _git(root, "rev-parse", "HEAD").strip()
    shown = sorted(_git(root, "show", "--name-only", "--format=", "HEAD").split())
    assert shown == sorted([oc.CONTRACT_PATH, f"{oc.FIXTURE_ROOT}/0.7.22/meta.json",
                            f"{oc.FIXTURE_ROOT}/0.7.22/captures/01-a.json"])  # fmt: skip
    message = _git(root, "log", "-1", "--format=%B")
    assert message.splitlines()[0] == "openrig-compat: accept rig 0.7.22 (watcher)"
    assert message.splitlines()[1] == ""
    assert f"HACF-Compat-Verdict: {evidence.name}" in message.splitlines()
    assert "Agentic-Assistant" not in message and "Co-authored-by" not in message.lower()
    assert _git(root, "status", "--porcelain") == ""
    contract = oc._shipped(root)._parse_contract((root / oc.CONTRACT_PATH).read_bytes())
    entry = next(e for e in contract.accepted if e.version == "0.7.22")
    assert (entry.accepted_by, entry.integrity, entry.verdict) == ("watcher", INTEGRITY,
                                                                   evidence.name)  # fmt: skip
    assert _git(root, "symbolic-ref", "--short", "HEAD").strip() == oc.ACCEPT_BRANCH


def test_accept_tests_failed_restores_the_tree(tmp_path: Path) -> None:
    """Accept tests failed restores the tree."""
    root = _repo(tmp_path)
    before = (root / oc.CONTRACT_PATH).read_bytes()
    got = oc.accept(root, "0.7.22", _evidence(tmp_path, _green_verdict()), NOT_OK)
    assert got["result"] == "tests-failed"
    assert _commits(root) == 1
    assert (root / oc.CONTRACT_PATH).read_bytes() == before
    assert not (root / oc.FIXTURE_ROOT / "0.7.22").exists()
    assert _git(root, "status", "--porcelain", "--untracked-files=all") == ""


def test_accept_runs_the_tests_on_the_tree_to_be_committed(tmp_path: Path) -> None:
    """The test argv sees the new entry and the fixture dir, in the work tree."""
    root = _repo(tmp_path)
    probe = [sys.executable, "-c",
             "import pathlib, sys;"
             " t = pathlib.Path('.agents/config/openrig_contract.yml').read_text();"
             " sys.exit(0 if '0.7.22' in t and pathlib.Path("
             f"'{oc.FIXTURE_ROOT}/0.7.22/meta.json').is_file() else 9)"]  # fmt: skip
    assert (
        oc.accept(root, "0.7.22", _evidence(tmp_path, _green_verdict()), probe)["result"]
        == "accepted"
    )


def test_accept_refuses_a_tree_the_tests_changed(tmp_path: Path) -> None:
    """Accept refuses a tree the tests changed."""
    root = _repo(tmp_path)
    scribble = [sys.executable, "-c", "open('stray.txt', 'w').write('x')"]
    got = oc.accept(root, "0.7.22", _evidence(tmp_path, _green_verdict()), scribble)
    assert got["result"] == "error" and "beyond the two" in got["reason"]
    assert _commits(root) == 1
    assert _git(root, "status", "--porcelain", "--untracked-files=all").split() == [
        "??",
        "stray.txt",
    ]


def test_accept_wrong_branch(tmp_path: Path) -> None:
    """Accept wrong branch."""
    root = _repo(tmp_path, branch="main")
    got = oc.accept(root, "0.7.22", _evidence(tmp_path, _green_verdict()), OK)
    assert got["result"] == "wrong-branch" and _commits(root) == 1


def test_accept_detached_head(tmp_path: Path) -> None:
    """Accept detached head."""
    root = _repo(tmp_path)
    _git(root, "checkout", "-q", "--detach")
    got = oc.accept(root, "0.7.22", _evidence(tmp_path, _green_verdict()), OK)
    assert got["result"] == "wrong-branch"


@pytest.mark.parametrize("how", ["untracked", "modified", "staged", "started-dirty"])
def test_accept_dirty_tree(how: str, tmp_path: Path) -> None:
    """Accept dirty tree."""
    root = _repo(tmp_path)
    if how == "untracked":
        (root / "new.txt").write_text("x", encoding="utf-8")
    elif how == "modified":
        (root / ".gitignore").write_text("x\n", encoding="utf-8")
    elif how == "staged":
        (root / "new.txt").write_text("x", encoding="utf-8")
        _git(root, "add", "new.txt")
    got = oc.accept(root, "0.7.22", _evidence(tmp_path, _green_verdict()), OK,
                    started_clean=how != "started-dirty")  # fmt: skip
    assert got["result"] == "dirty-tree" and _commits(root) == 1


def test_accept_head_moved(tmp_path: Path) -> None:
    """Accept head moved."""
    root = _repo(tmp_path)
    got = oc.accept(root, "0.7.22", _evidence(tmp_path, _green_verdict()), OK, expect_head="0" * 40)
    assert got["result"] == "head-moved" and _commits(root) == 1


def test_accept_not_a_patch(tmp_path: Path) -> None:
    """Accept not a patch."""
    root = _repo(tmp_path)
    got = oc.accept(root, "0.8.0", _evidence(tmp_path, _green_verdict("0.8.0"), "0.8.0"), OK)
    assert got["result"] == "not-a-patch" and _commits(root) == 1


def test_accept_review_streak(tmp_path: Path) -> None:
    """Three watcher entries after the newest person entry: the 4th is REVIEW (F13)."""
    root = _repo(tmp_path, "".join(_watcher_line(v) for v in ("0.7.22", "0.7.23", "0.7.24")))
    got = oc.accept(root, "0.7.25", _evidence(tmp_path, _green_verdict("0.7.25"), "0.7.25"), OK)
    assert got["result"] == "review-streak" and _commits(root) == 1
    # two watcher entries: the third is still accepted
    root2 = _repo(tmp_path / "two", "".join(_watcher_line(v) for v in ("0.7.22", "0.7.23")))
    evidence = _evidence(tmp_path / "two", _green_verdict("0.7.24"), "0.7.24")
    got = oc.accept(root2, "0.7.24", evidence, OK)
    assert got["result"] == "accepted", got


def test_watcher_streak_resets_after_a_person_entry() -> None:
    """Watcher streak resets after a person entry."""
    lines = "".join(_watcher_line(v) for v in ("0.7.22", "0.7.23", "0.7.24"))
    person = (f'  - {{version: "0.7.25", integrity: "{INTEGRITY}", verdict: "x", '
              "accepted_by: person}\n")  # fmt: skip
    assert oc.watcher_streak("0.7.25", _shipped_contract(lines)) == 3
    assert oc.watcher_streak("0.7.26", _shipped_contract(lines + person)) == 0


def test_accept_refuses_an_existing_fixture_dir(tmp_path: Path) -> None:
    """Accept refuses an existing fixture dir."""
    root = _repo(tmp_path)
    (root / oc.FIXTURE_ROOT / "0.7.22").mkdir()
    (root / oc.FIXTURE_ROOT / "0.7.22/keep").write_text("x", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "pre-existing")
    got = oc.accept(root, "0.7.22", _evidence(tmp_path, _green_verdict()), OK)
    assert got["result"] == "error" and _commits(root) == 2
    assert (root / oc.FIXTURE_ROOT / "0.7.22/keep").read_text() == "x"


def _verdict_sweep() -> list[tuple[str, object]]:
    """Every verdict.json that is not a schema-valid green PASS for 0.7.22."""
    cases: list[tuple[str, object]] = []
    green = _green_verdict()
    for verdict in ("REVIEW", "FAIL", "SETUP-ERROR", "SEED", "pass", ""):
        cases.append((f"verdict={verdict!r}", {**green, "verdict": verdict}))
    for key in oc.VERDICT_KEYS:
        dropped = dict(green)
        del dropped[key]
        cases.append((f"missing {key}", dropped))
        for bad in (None, 1, 1.5, True, "x", [], {}):
            if isinstance(bad, oc.VERDICT_KEYS[key]) and key not in ("version", "integrity"):
                continue  # a valid type for this key; covered by the value checks below
            cases.append((f"{key}={bad!r}", {**green, key: bad}))
    cases += [
        ("other version", {**green, "version": "0.7.23"}),
        ("failed step", {**green, "failed_step": "help"}),
        ("setup error", {**green, "setup_error": "npm"}),
        ("watched diff", {**green, "watched_diff": {"scope-audit.js": {"0.7.21": "differs"}}}),
        ("bad baseline", {**green, "baseline": "v0.7.21"}),
        ("integrity local", {**green, "integrity": "local"}),
        ("already accepted", {**green, "accept": {"result": "accepted"}}),
        ("no steps", {**green, "steps": []}),
        ("a step not ok", {**green, "steps": [*green["steps"][:-1],
                                              {**green["steps"][-1], "result": "setup"}]}),
        ("a step missing", {**green, "steps": green["steps"][1:]}),
        ("a step not an object", {**green, "steps": [*green["steps"], "ok"]}),
        ("not an object", [green]),
    ]  # fmt: skip
    return cases


@pytest.mark.parametrize(("label", "verdict"), _verdict_sweep(), ids=lambda x: str(x)[:40])
def test_accept_sweep_never_commits_without_a_green_pass(label: str, verdict: object,
                                                         tmp_path: Path) -> None:  # fmt: skip
    """Accept sweep never commits without a green PASS."""
    root = _repo(tmp_path)
    got = oc.accept(root, "0.7.22", _evidence(tmp_path, copy.deepcopy(verdict)), OK)
    assert got["result"] == "not-pass", (label, got)
    assert _commits(root) == 1
    assert _git(root, "status", "--porcelain", "--untracked-files=all") == ""


@pytest.mark.parametrize("raw", [
    b"", b"{", b"null", b"\xff\xfe", b'{"verdict": NaN}', b'{"verdict": Infinity}',
    b'{"n": ' + b"9" * 400 + b"}", b"[" * 5000 + b"]" * 5000,
    json.dumps(_green_verdict()).replace('"PASS"', '"PASS", "verdict": "FAIL"').encode(),
])  # fmt: skip
def test_accept_hostile_verdict_bytes(raw: bytes, tmp_path: Path) -> None:
    """Accept hostile verdict bytes."""
    root = _repo(tmp_path)
    got = oc.accept(root, "0.7.22", _evidence(tmp_path, raw=raw), OK)
    assert got["result"] == "not-pass", got
    assert _commits(root) == 1


def test_accept_missing_verdict_and_candidate(tmp_path: Path) -> None:
    """Accept missing verdict and candidate."""
    root = _repo(tmp_path)
    assert oc.accept(root, "0.7.22", _evidence(tmp_path), OK)["result"] == "not-pass"
    evidence = _evidence(tmp_path / "b", _green_verdict())
    shutil.rmtree(evidence / "baseline-candidate")
    assert oc.accept(root, "0.7.22", evidence, OK)["result"] == "error"
    assert _commits(root) == 1


def test_accept_refuses_a_broken_contract_file(tmp_path: Path) -> None:
    """Accept refuses a broken contract file."""
    root = _repo(tmp_path)
    (root / oc.CONTRACT_PATH).write_text("schema: 1\naccepted: [\n", encoding="utf-8")
    _git(root, "commit", "-qam", "break it")
    got = oc.accept(root, "0.7.22", _evidence(tmp_path, _green_verdict()), OK)
    assert got["result"] == "error" and _commits(root) == 2


def test_accept_cli_records_the_result_in_verdict_json(tmp_path: Path, capsys) -> None:
    """Accept cli records the result in verdict json."""
    root = _repo(tmp_path)
    evidence = _evidence(tmp_path, _green_verdict())
    rc = oc.main(["accept", "--root", str(root), "--version", "0.7.22", "--evidence", str(evidence),
                  "--", *OK])  # fmt: skip
    assert rc == 0
    data = json.loads((evidence / "verdict.json").read_text())
    assert data["accept"]["result"] == "accepted" and data["accept"]["sha"]
    assert "accept accepted" in capsys.readouterr().out
    # a REVIEW: no commit, the reason recorded
    evidence = _evidence(
        tmp_path / "r", {**_green_verdict("0.7.23"), "verdict": "REVIEW"}, "0.7.23"
    )
    rc = oc.main(["accept", "--root", str(root), "--version", "0.7.23", "--evidence", str(evidence),
                  "--", *OK])  # fmt: skip
    assert (
        rc == 1
        and json.loads((evidence / "verdict.json").read_text())["accept"]["result"] == "not-pass"
    )
    # an unreadable verdict.json: the result goes to accept.json
    evidence = _evidence(tmp_path / "u", raw=b"{")
    oc.main(["accept", "--root", str(root), "--version", "0.7.22", "--evidence", str(evidence),
             "--", *OK])  # fmt: skip
    assert json.loads((evidence / "accept.json").read_text())["result"] == "not-pass"
    assert _commits(root) == 2


# ---------------------------------------------------------------------------------------------
# The shell's --accept arguments, and no push anywhere


@pytest.mark.parametrize(("args", "needle"), [
    (["0.7.22", "--accept", "--seed"], "exclude each other"),
    (["0.7.22", "--accept", "--package-dir", "/"], "never takes --package-dir"),
])  # fmt: skip
def test_script_accept_argument_refusals(args: list[str], needle: str, tmp_path: Path) -> None:
    """Script accept argument refusals."""
    env = {k: v for k, v in os.environ.items() if k not in ("HACF_COMPAT_EVIDENCE_ROOT",)}
    env.update(TMPDIR=str(tmp_path), HOME=str(tmp_path / "home"))
    proc = subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True, env=env,
                          timeout=60, check=False)  # fmt: skip
    assert proc.returncode == 2 and needle in proc.stderr
    assert not (tmp_path / "home").exists()


def test_nothing_calls_git_push() -> None:
    """Nothing calls git push."""
    for name in ("openrig_compat.sh", "openrig_compat.py", "openrig_watch.sh",
                 "openrig_watch_install.sh"):  # fmt: skip
        text = (REPO_ROOT / "dev" / name).read_text(encoding="utf-8")
        code = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
        assert not re.search(r"\bgit\b[^\n]*\bpush\b", code), name
        assert '"push"' not in code and "'push'" not in code, name
        assert "--no-verify" not in code, name
