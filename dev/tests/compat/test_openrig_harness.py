"""The compat run's community-harness smoke (OPR.99.0.13.1): the `harness` step and its verdicts.

Packages are synthetic and built under tmp_path: a minimal ES-module preset parser, and a launcher
and runner that reproduce the four agy branches. The launcher only ever runs against the stub. No
npm, network, host rig or model.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT
from test_openrig_compat import _green_state, _ns

sys.path.insert(0, str(REPO_ROOT / "dev"))
import openrig_compat as oc  # noqa: E402

SCRIPT = REPO_ROOT / "dev" / "openrig_compat.sh"
STUB = REPO_ROOT / "dev" / "tests" / "compat" / "stubs" / "agy"
EXAMPLES = "daemon/docs/reference/community-harness-examples"
PARSER = "daemon/dist/domain/community-harness-presets.js"
need_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")

FAKE_PARSER = """import fs from "node:fs";
import path from "node:path";
export function listCommunityHarnessPresets(dir) {
  if (!fs.existsSync(dir)) return [];
  return fs.readdirSync(dir).filter((f) => f.endsWith(".yaml")).sort().map((f) => {
    const text = fs.readFileSync(path.join(dir, f), "utf8");
    if (text.includes("BROKEN")) return { ok: false, kind: "parse", path: f, message: "x" };
    const found = text.match(/^name: (.*)$/m);
    return { ok: true, preset: { name: found ? found[1] : "" } };
  });
}
"""

LAUNCHER = r"""#!/bin/sh
set -eu
home=""; bin=""
while [ $# -gt 0 ]; do
  case "$1" in
    --agy-home) home="$2"; shift 2 ;;
    --agy) bin="$2"; shift 2 ;;
    *) break ;;
  esac
done
report() { printf '%s\n' "$1" >"$OPENRIG_LAUNCH_RESULT"; }
if [ -n "${OPENRIG_RESUME_REF:-}" ]; then
  id="$OPENRIG_RESUME_REF"
  case "$id" in *[!A-Za-z0-9-]*) exit 2 ;; esac
  if [ ! -f "$home/.gemini/antigravity-cli/conversations/$id.db" ]; then
    report '{"format":1,"error":"missing"}'; exit 1
  fi
else
  out=$("$bin" -p ready --output-format json)
  id=$(printf '%s' "$out" | sed -n 's/^{"conversation_id":"\([A-Za-z0-9-]*\)".*/\1/p')
fi
report "{\"format\":1,\"nativeRef\":\"$id\",\"harness\":\"$OPENRIG_RUNTIME\"}"
exec "$bin" --conversation "$id"
"""

FILES = {
    "agy.yaml": "name: agy\n",
    "agy-plain.yaml": "name: agy-plain\n",
    "openrig-agy-launch": LAUNCHER,
    "openrig-agy-runner.mjs": "export const ready = 1;\n",
}


def _package(path: Path, files: dict[str, str] | None = None, parser: str = FAKE_PARSER) -> Path:
    """A synthetic package: the preset parser and one agy example dir."""
    (path / "daemon").mkdir(parents=True)
    (path / "daemon/package.json").write_text('{"type": "module"}\n', encoding="utf-8")
    (path / PARSER).parent.mkdir(parents=True)
    (path / PARSER).write_text(parser, encoding="utf-8")
    if files is not None:
        (path / EXAMPLES / "agy").mkdir(parents=True)
        for name, text in files.items():
            (path / EXAMPLES / "agy" / name).write_text(text, encoding="utf-8")
    return path


def _root(tmp_path: Path) -> Path:
    """A tree holding the real stub and, when a test adds them, baselines."""
    root = tmp_path / "tree"
    stub = root / "dev/tests/compat/stubs/agy"
    stub.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(STUB, stub)
    return root


def _baseline(root: Path, version: str, files: dict[str, str]) -> None:
    dest = oc.baseline_dir(root, version) / "harness" / "agy"
    dest.mkdir(parents=True)
    for name, text in files.items():
        (dest / name).write_text(text, encoding="utf-8")


def _run(
    tmp_path: Path,
    package: Path,
    baselines: tuple[str, ...] = (),
    names: tuple[str, ...] = ("agy",),
    seed: bool = False,
    root: Path | None = None,
) -> tuple[int, dict]:
    root = root or _root(tmp_path)
    out = tmp_path / "h.json"
    rc = oc.run_harness(
        root,
        package,
        list(baselines),
        tmp_path / "work",
        out,
        tmp_path / "diffs",
        list(names),
        seed,
    )
    return rc, json.loads(out.read_text())


def _status(result: tuple[int, dict]) -> tuple[int, str, str]:
    rc, data = result
    entry = data["harness"]["agy"]
    return rc, entry["status"], entry["detail"]


def test_the_stub_is_committed_executable() -> None:
    """The stub is a POSIX sh script with its executable bit."""
    assert STUB.read_text(encoding="utf-8").startswith("#!/bin/sh\n")
    assert STUB.stat().st_mode & 0o111


# --- statuses ------------------------------------------------------------------------------------


@need_node
def test_unreferenced_when_no_baseline_holds_the_harness(tmp_path: Path) -> None:
    """Present, all smokes pass, no baseline holds it: unreferenced, exit 0."""
    rc, data = _run(tmp_path, _package(tmp_path / "p", FILES), ("0.7.21",))
    entry = data["harness"]["agy"]
    assert (rc, entry["status"]) == (0, "unreferenced"), entry
    assert entry["files"] == sorted(FILES) and data["setup"] == []


@need_node
def test_ok_changed_missing_and_extra_against_baselines(tmp_path: Path) -> None:
    """Equal bytes: ok. A differing, missing or extra file: changed, with a diff for text."""
    root = _root(tmp_path)
    _baseline(root, "0.7.21", FILES)
    package = _package(tmp_path / "p", FILES)
    assert _status(_run(tmp_path, package, ("0.7.21",), root=root))[:2] == (0, "ok")
    changed = {**FILES, "agy.yaml": "name: agy\n# new\n", "extra.txt": "x\n"}
    del changed["openrig-agy-runner.mjs"]
    changed["openrig-agy-runner.mjs"] = FILES["openrig-agy-runner.mjs"]
    shutil.rmtree(package / EXAMPLES / "agy")
    (package / EXAMPLES / "agy").mkdir()
    for name, text in changed.items():
        (package / EXAMPLES / "agy" / name).write_text(text, encoding="utf-8")
    _baseline(root, "0.7.16", {**FILES, "gone.txt": "g\n"})
    rc, data = _run(tmp_path, package, ("0.7.21", "0.7.16"), root=root)
    entry = data["harness"]["agy"]
    assert (rc, entry["status"]) == (0, "changed"), entry
    assert entry["diff"]["agy.yaml"] == {"0.7.21": "differs", "0.7.16": "differs"}
    assert entry["diff"]["extra.txt"] == {"0.7.21": "extra", "0.7.16": "extra"}
    assert entry["diff"]["gone.txt"] == {"0.7.16": "missing"}
    assert (tmp_path / "diffs/harness-agy-agy.yaml-0.7.21.diff").is_file()


@need_node
def test_absent_with_or_without_a_baseline(tmp_path: Path) -> None:
    """No example dir: absent (REVIEW), even when a baseline holds the harness."""
    root = _root(tmp_path)
    _baseline(root, "0.7.21", FILES)
    package = _package(tmp_path / "p")
    assert _status(_run(tmp_path, package, ("0.7.21",), root=root))[:2] == (0, "absent")
    assert _status(_run(tmp_path, package, (), root=root))[:2] == (0, "absent")


@need_node
@pytest.mark.parametrize(
    ("files", "text"),
    [
        ({**FILES, "agy.yaml": "name: other\n"}, "agy.yaml"),
        ({**FILES, "agy.yaml": "BROKEN\n"}, "agy.yaml"),
        ({**FILES, "third.yaml": "name: third\n"}, "preset names"),
        ({**FILES, "openrig-agy-launch": "#!/bin/sh\nif then\n"}, "sh -n"),
        ({**FILES, "openrig-agy-runner.mjs": "const = ;\n"}, "node --check"),
        ({k: v for k, v in FILES.items() if k != "openrig-agy-launch"}, "openrig-agy-launch"),
    ],
)
def test_product_failures_are_fail(tmp_path: Path, files: dict, text: str) -> None:
    """A broken preset, wrong name set, syntax error or missing file is a product FAIL (exit 1)."""
    rc, status, detail = _status(_run(tmp_path, _package(tmp_path / "p", files)))
    assert (rc, status) == (1, "fail") and text in detail, detail


@need_node
@pytest.mark.parametrize(
    ("old", "new", "case"),
    [
        ('"harness\\":\\"$OPENRIG_RUNTIME', '"harness\\":\\"other', "fresh"),
        ('--conversation "$id"\n', '--conversation "$id" extra\n', "fresh"),
        ("exit 1\n", "exit 3\n", "resume missing"),
        ('"error":"missing"', '"error":"gone"', "resume missing"),
        ("exit 2 ;;", "exit 5 ;;", "unusual id"),
        ('if [ -n "${OPENRIG_RESUME_REF:-}" ]; then', "if false; then", "resume missing"),
        (
            'conversations/$id.db" ]; then',
            'conversations/$id.db" ] || true; then',
            "resume present",
        ),
    ],
)
def test_each_launcher_case_can_fail(tmp_path: Path, old: str, new: str, case: str) -> None:
    """Each of the four launcher cases catches its own broken behaviour."""
    assert old in LAUNCHER, old
    files = {**FILES, "openrig-agy-launch": LAUNCHER.replace(old, new, 1)}
    rc, status, detail = _status(_run(tmp_path, _package(tmp_path / "p", files)))
    assert (rc, status) == (1, "fail") and case in detail, detail


@need_node
def test_a_parser_that_writes_to_openrig_home_fails(tmp_path: Path) -> None:
    """The parser must leave HOME and OPENRIG_HOME empty."""
    writer = FAKE_PARSER.replace(
        "  if (!fs.existsSync(dir)) return [];",
        '  fs.writeFileSync(path.join(process.env.OPENRIG_HOME, "x"), "1");\n'
        "  if (!fs.existsSync(dir)) return [];",
    )
    rc, status, detail = _status(_run(tmp_path, _package(tmp_path / "p", FILES, writer)))
    assert (rc, status) == (1, "fail") and "OPENRIG_HOME" in detail, detail


@need_node
@pytest.mark.parametrize(
    "parser",
    [
        "export function listCommunityHarnessPresets() { return { a: 1 }; }\n",
        "this is not javascript (\n",
        "export const other = 1;\n",
    ],
)
def test_a_parser_that_is_unusable_is_setup(tmp_path: Path, parser: str) -> None:
    """Non-array output, a syntax error or a missing export is the checker's problem (exit 2)."""
    rc, data = _run(tmp_path, _package(tmp_path / "p", FILES, parser))
    assert rc == 2 and data["harness"]["agy"]["status"] == "setup" and data["setup"]


@need_node
def test_checker_inabilities_are_setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Unreadable root, odd example entries, unknown name, no parser, no node: setup."""
    good = _package(tmp_path / "p", FILES)
    assert _status(_run(tmp_path, tmp_path / "nowhere"))[:2] == (2, "setup")
    rc, data = _run(tmp_path, good, names=("agy", "mystery"))
    assert rc == 2 and data["harness"]["mystery"]["status"] == "setup"
    assert data["harness"]["agy"]["status"] == "unreferenced"
    sub = good / EXAMPLES / "agy" / "sub"
    sub.mkdir()
    assert _status(_run(tmp_path, good))[:2] == (2, "setup")
    sub.rmdir()
    for index in range(33):
        (good / EXAMPLES / "agy" / f"f{index}").write_text("x", encoding="utf-8")
    assert _status(_run(tmp_path, good))[:2] == (2, "setup")
    for index in range(33):
        (good / EXAMPLES / "agy" / f"f{index}").unlink()
    (good / EXAMPLES / "agy" / "big").write_bytes(b"x" * (1024 * 1024 + 1))
    assert _status(_run(tmp_path, good))[:2] == (2, "setup")
    (good / EXAMPLES / "agy" / "big").unlink()
    (good / PARSER).unlink()
    assert _status(_run(tmp_path, good))[:2] == (2, "setup")
    other = _package(tmp_path / "link", None)
    (other / EXAMPLES).mkdir(parents=True)
    (other / EXAMPLES / "agy").symlink_to(good / EXAMPLES / "agy")
    assert _status(_run(tmp_path, other))[:2] == (2, "setup")
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    assert _status(_run(tmp_path, _package(tmp_path / "q", FILES)))[:2] == (2, "setup")


@need_node
def test_a_hung_smoke_is_setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A subprocess over its timeout is a setup error, not a product failure."""
    monkeypatch.setattr(oc, "HARNESS_TIMEOUT", 1)
    files = {**FILES, "openrig-agy-launch": "#!/bin/sh\nsleep 30\n"}
    rc, status, detail = _status(_run(tmp_path, _package(tmp_path / "p", files)))
    assert (rc, status) == (2, "setup") and "timed out" in detail, detail


@need_node
def test_setup_outranks_fail_in_the_exit_code(tmp_path: Path) -> None:
    """Exit 2 when any harness is setup, even when another fails."""
    files = {**FILES, "agy.yaml": "BROKEN\n"}
    rc, data = _run(tmp_path, _package(tmp_path / "p", files), names=("agy", "mystery"))
    assert rc == 2 and data["harness"]["agy"]["status"] == "fail"


@need_node
def test_seed_makes_no_comparison(tmp_path: Path) -> None:
    """Under --seed a present harness is ok and an absent one absent; no changed or unreferenced."""
    root = _root(tmp_path)
    _baseline(root, "0.7.21", {**FILES, "agy.yaml": "old\n"})
    package = _package(tmp_path / "p", FILES)
    assert _status(_run(tmp_path, package, (), seed=True, root=root))[:2] == (0, "ok")
    assert _status(_run(tmp_path, _package(tmp_path / "q"), (), seed=True, root=root))[:2] == (
        0,
        "absent",
    )


# --- verdict, report, baseline candidate, accept --------------------------------------------------


def test_harness_is_a_required_compare_step() -> None:
    """The step is `compare`, right after watched; the seed list has it after spec-validate."""
    names = list(oc.STEP_KINDS)
    assert oc.STEP_KINDS["harness"] == "compare" and names.index("harness") == 1 + names.index(
        "watched"
    )
    seed = list(oc.SEED_REQUIRED_STEPS)
    assert seed.index("harness") == 1 + seed.index("spec-validate")


def test_decide_with_harness_review() -> None:
    """A harness review is REVIEW outside seed only; the other order is unchanged."""
    steps = [{"name": n, "result": "ok"} for n in oc.REQUIRED_STEPS]
    assert oc.decide(steps, {}, False)[0] == "PASS"
    assert oc.decide(steps, {}, False, ["agy"])[0] == "REVIEW"
    seed = [{"name": n, "result": "ok"} for n in oc.SEED_REQUIRED_STEPS]
    assert oc.decide(seed, {}, True, ["agy"])[0] == "SEED"
    failed = [dict(s, result="fail") if s["name"] == "harness" else s for s in steps]
    assert oc.decide(failed, {}, False, ["agy"])[:2] == ("FAIL", "harness")


def _harness_state(state: Path, status: str, detail: str = "d") -> None:
    oc.write_json(
        state / "harness.json",
        {
            "harness": {"agy": {"status": status, "detail": detail, "files": [], "diff": {}}},
            "setup": [],
        },
    )


@pytest.mark.parametrize(
    ("status", "verdict"),
    [("ok", "PASS"), ("absent", "REVIEW"), ("changed", "REVIEW"), ("unreferenced", "REVIEW")],
)
def test_report_turns_statuses_into_verdicts(tmp_path: Path, status: str, verdict: str) -> None:
    """report() reads harness.json, writes verdict.json's map and one REPORT line per non-ok."""
    state = tmp_path / "state"
    _green_state(state)
    _harness_state(state, status, "why")
    assert oc.report(_ns(tmp_path)) == oc.VERDICT_EXIT[verdict]
    out = json.loads((tmp_path / "evidence/verdict.json").read_text())
    assert out["harness"] == {"agy": status}
    text = (tmp_path / "evidence/REPORT.md").read_text()
    assert (f"Harness agy: {status}: why" in text) is (status != "ok")


@pytest.mark.parametrize(
    "data",
    [
        {"harness": {}, "setup": []},
        {
            "harness": {"agy": {"status": "weird", "detail": "", "files": [], "diff": {}}},
            "setup": [],
        },
        {
            "harness": {"agy": {"status": "fail", "detail": "", "files": [], "diff": {}}},
            "setup": [],
        },
        {
            "harness": {"agy": {"status": "setup", "detail": "", "files": [], "diff": {}}},
            "setup": [],
        },
        {"harness": {"agy": "ok"}, "setup": []},
        {"harness": {"agy": {"status": "ok", "detail": "", "files": [], "diff": {}}}},
        {
            "harness": {"agy": {"status": "ok", "detail": "", "files": [], "diff": {}}},
            "setup": ["x"],
        },
        {"setup": []},
    ],
)
def test_report_refuses_a_bad_harness_file(tmp_path: Path, data: dict) -> None:
    """A missing configured name, an unknown status or a contradicting file is SETUP-ERROR."""
    state = tmp_path / "state"
    _green_state(state)
    oc.write_json(state / "harness.json", data)
    assert oc.report(_ns(tmp_path)) == oc.VERDICT_EXIT["SETUP-ERROR"]


def test_report_needs_the_file_in_seed_and_leaves_the_map_empty(tmp_path: Path) -> None:
    """Seed validates harness.json too, writes {} and stays SEED."""
    state = tmp_path / "state"
    _green_state(state, seed=True)
    assert oc.report(_ns(tmp_path, seed=True)) == oc.VERDICT_EXIT["SEED"]
    assert json.loads((tmp_path / "evidence/verdict.json").read_text())["harness"] == {}
    (state / "harness.json").unlink()
    assert oc.report(_ns(tmp_path, seed=True)) == oc.VERDICT_EXIT["SETUP-ERROR"]


def test_baseline_candidate_carries_present_harnesses(tmp_path: Path) -> None:
    """The candidate gets harness/<n>/ for present ones; meta lists configured and absent."""
    state = tmp_path / "state"
    (state / "recorded").mkdir(parents=True)
    (state / "help").mkdir()
    package = _package(tmp_path / "p", FILES)
    dest = tmp_path / "cand"
    oc.write_baseline_candidate(
        state, package, dest, {"version": "x"}, (), harnesses=("agy", "gone")
    )
    assert sorted(p.name for p in (dest / "harness/agy").iterdir()) == sorted(FILES)
    meta = json.loads((dest / "meta.json").read_text())
    assert meta["harnesses"] == ["agy", "gone"] and meta["harness_absent"] == ["gone"]


@pytest.mark.parametrize("harness", [None, {}, {"agy": "changed"}, {"agy": "ok", "x": "fail"}, []])
def test_accept_refuses_unless_every_harness_is_ok(tmp_path: Path, harness: object) -> None:
    """load_pass_verdict needs a non-empty harness map with only ok values."""
    from test_openrig_accept import V1, _evidence, _green_verdict

    verdict = _green_verdict()
    if harness is None:
        del verdict["harness"]
    else:
        verdict["harness"] = harness
    with pytest.raises(oc.CompatInputError, match="harness not ok"):
        oc.load_pass_verdict(_evidence(tmp_path, verdict), V1)


# --- the shell's --harness flag -------------------------------------------------------------------


def _shell(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True, timeout=60)


@pytest.mark.parametrize("bad", ["", "-x", "Agy", "a b", "a/b", "a" * 64, "a\nb"])
def test_the_shell_refuses_a_bad_harness_name(bad: str) -> None:
    """A harness name outside the pattern is a usage error, exit 2."""
    got = _shell("0.7.23", "--harness", bad)
    assert got.returncode == 2 and "harness" in got.stderr, got.stderr


def test_the_shell_refuses_a_missing_harness_value() -> None:
    """--harness with nothing after it is a usage error."""
    got = _shell("0.7.23", "--harness")
    assert got.returncode == 2 and "--harness needs a name" in got.stderr


def test_the_shell_parses_the_harness_list(tmp_path: Path) -> None:
    """Default agy; repeatable; duplicates dropped keeping the first; flags passed to both steps."""
    text = SCRIPT.read_text(encoding="utf-8")
    start = text.index("# harness list:")
    block = text[start : text.index("# end of harness list", start)]
    for given, want in (([], ["agy"]), (["b", "agy", "b"], ["b", "agy"])):
        array = " ".join(given)
        script = f"harnesses=({array})\n{block}\nprintf '%s ' \"${{harness_flags[@]}}\"\n"
        done = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=True)
        assert done.stdout.split() == [x for n in want for x in ("--harness", n)]
    assert "--harness <name>" in text and "step harness" in text
    assert "harness.json" in text[text.index("for f in captures.json") :][:300]
    assert text.count('"${harness_flags[@]}"') >= 2
