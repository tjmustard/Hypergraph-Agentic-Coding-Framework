"""Tests for dev/scripts/summarize_pytest.py, check.sh's per-leg summary (OPR.99.0.7.3)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from conftest import REPO_ROOT, clean_env

SCRIPT = REPO_ROOT / "dev" / "scripts" / "summarize_pytest.py"
sys.path.insert(0, str(SCRIPT.parent))
import summarize_pytest as sp  # noqa: E402


def _case(classname: str, name: str, child: str = "") -> str:
    return f'<testcase classname="{classname}" name="{name}" time="0.1">{child}</testcase>'


def _tb(n: int, tag: str = "failure") -> str:
    body = "\n".join(f"tb line {i}" for i in range(1, n + 1))
    return f'<{tag} message="boom">{body}</{tag}>'


def _junit(path: Path, cases: list[str], seconds: str = "61.2") -> Path:
    path.write_text(
        '<?xml version="1.0" encoding="utf-8"?><testsuites name="pytest">'
        f'<testsuite name="pytest" time="{seconds}">{"".join(cases)}</testsuite></testsuites>',
        encoding="utf-8",
    )
    return path


def _log(path: Path, n: int) -> Path:
    path.write_text("".join(f"log line {i}\n" for i in range(1, n + 1)), encoding="utf-8")
    return path


def _cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=REPO_ROOT,
        env=clean_env(),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def test_counts_ids_and_short_tracebacks(tmp_path):
    """3 passed, 2 failed, 1 error, 1 skipped: the counts, all three ids, 15-line tracebacks."""
    xml = _junit(
        tmp_path / "r.xml",
        [
            _case("dev.tests.x.test_a", "test_ok1"),
            _case("dev.tests.x.test_a", "test_ok2"),
            _case("dev.tests.x.test_a.TestK", "test_ok3"),
            _case("dev.tests.x.test_a", "test_b", _tb(30)),
            _case("dev.tests.x.test_a.TestK", "test_c[p-1]", _tb(3)),
            _case("dev.tests.y.test_e", "test_err", _tb(20, "error")),
            _case("dev.tests.x.test_a", "test_skip", '<skipped message="later"/>'),
        ],
    )
    log = _log(tmp_path / "r.log", 5)
    proc = _cli("--label", "[2/4] pytest (uv venv)", "--exit", "1", "--log", str(log),
                "--junitxml", str(xml))  # fmt: skip
    assert proc.returncode == 0, proc.stderr
    lines = proc.stdout.splitlines()
    assert lines[0] == (
        "    [2/4] pytest (uv venv): FAIL (exit 1) 3 passed, 2 failed, 1 errors, 1 skipped in 61.2s"
    )
    assert "      FAILED dev/tests/x/test_a.py::test_b" in lines
    assert "      FAILED dev/tests/x/test_a.py::TestK::test_c[p-1]" in lines
    assert "      ERROR dev/tests/y/test_e.py::test_err" in lines
    first = lines.index("      FAILED dev/tests/x/test_a.py::test_b")
    assert lines[first + 1 : first + 16] == [f"        tb line {i}" for i in range(16, 31)]
    assert lines[first + 16].startswith("      FAILED ")
    err = lines.index("      ERROR dev/tests/y/test_e.py::test_err")
    assert lines[err + 1 : err + 16] == [f"        tb line {i}" for i in range(6, 21)]
    assert lines[-1] == f"      log: {log}  junit: {xml}"
    assert not any("log line" in ln for ln in lines), "a junit summary does not dump the log"


def test_more_than_twenty_failures(tmp_path):
    """25 failures: the first 20 ids, then `… and 5 more`."""
    xml = _junit(tmp_path / "r.xml", [_case("t.test_m", f"test_{i}", _tb(1)) for i in range(25)])
    lines = sp.pytest_block("L", 1, tmp_path / "r.log", xml)
    ids = [ln for ln in lines if ln.startswith("  FAILED ")]
    assert ids == [f"  FAILED t/test_m.py::test_{i}" for i in range(20)]
    assert "  … and 5 more" in lines


def test_missing_and_truncated_xml_fall_back_to_the_log_tail(tmp_path):
    """No XML, or a truncated one: the fallback header and exactly the log's last 40 lines."""
    log = _log(tmp_path / "r.log", 100)
    truncated = tmp_path / "t.xml"
    truncated.write_text('<?xml version="1.0"?><testsuites><testsuite name="p">', encoding="utf-8")
    for xml in (tmp_path / "absent.xml", truncated):
        proc = _cli("--label", "L", "--exit", "1", "--log", str(log), "--junitxml", str(xml))
        assert proc.returncode == 0, proc.stderr
        lines = proc.stdout.splitlines()
        assert lines[0] == "    L: FAIL (exit 1)"
        assert lines[1] == "      (no junit XML; the last 40 lines of the log)"
        assert lines[2:42] == [f"        log line {i}" for i in range(61, 101)]
        assert lines[42:] == [f"      log: {log}  junit: {xml}"]


def test_passing_leg_is_two_lines(tmp_path):
    """A passing leg prints its first and last lines only."""
    xml = _junit(tmp_path / "r.xml", [_case("t.test_p", "test_a")], seconds="0.5")
    lines = sp.pytest_block("[3/4] pytest (py)", 0, tmp_path / "r.log", xml)
    assert lines == [
        "[3/4] pytest (py): PASS (exit 0) 1 passed, 0 failed, 0 errors, 0 skipped in 0.5s",
        f"  log: {tmp_path / 'r.log'}  junit: {xml}",
    ]


def test_red_exit_with_no_failing_testcase_shows_the_tail(tmp_path):
    """Never a green-looking block over a red leg: no failing testcase means the log's tail."""
    xml = _junit(tmp_path / "r.xml", [_case("t.test_p", "test_a")])
    lines = sp.pytest_block("L", 3, _log(tmp_path / "r.log", 50), xml)
    assert lines[0].startswith("L: FAIL (exit 3) 1 passed")
    assert "  (no failing test in the junit XML; the last 40 lines of the log)" in lines
    assert "    log line 50" in lines and "    log line 10" not in lines


def test_raw_prints_the_last_forty_lines(tmp_path):
    """--raw on a failing 100-line log: the status line, the last 40 lines, the log line."""
    log = _log(tmp_path / "ruff.log", 100)
    proc = _cli("--raw", "--label", "[4/4] ruff", "--exit", "1", "--note", "3 file(s)",
                "--log", str(log))  # fmt: skip
    lines = proc.stdout.splitlines()
    assert proc.returncode == 0
    assert lines[0] == "    [4/4] ruff: FAIL (exit 1) 3 file(s)"
    assert lines[1:41] == [f"        log line {i}" for i in range(61, 101)]
    assert lines[41:] == [f"      log: {log}"]
    passing = sp.raw_block("[4/4] ruff", 0, log, "no changed Python files")
    assert passing == ["[4/4] ruff: PASS (exit 0) no changed Python files", f"  log: {log}"]


def test_unreadable_log_exits_zero(tmp_path):
    """An unreadable log is reported in the block, and the script still exits 0."""
    proc = _cli("--label", "L", "--exit", "1", "--log", str(tmp_path / "nope.log"))
    assert proc.returncode == 0, proc.stderr
    assert "        (log unreadable: No such file or directory)" in proc.stdout.splitlines()
    proc = _cli("--label", "L")  # a bad call is a summary problem, never a verdict
    assert proc.returncode == 0
    assert "(summary failed" in proc.stdout


CAPTURED = """\
==> [1/4] preflight: py can import pytest, yaml and xdist
    xdist 3.8.0 (py), 3.8.0 (uv venv)
==> workers: 4 (HACF_TEST_WORKERS=4; --dist loadgroup)
==> [2/4] pytest (uv venv)
...F...
=========================== short test summary info ============================
SKIPPED [1] dev/tests/a/test_s.py:3: network unavailable
FAILED dev/tests/a/test_x.py::test_broken - AssertionError: no
==================== 1 failed, 3 passed, 1 skipped in 1.25s ====================
==> [3/4] pytest (py)
....
============================== 4 passed in 0.90s ===============================
"""


def test_summarize_output_per_leg():
    """A captured two-leg output: per-leg status, counts and the FAILED id."""
    lines = sp.summarize_output(CAPTURED)
    assert lines[0].startswith("[1/4] preflight: py can import pytest, yaml and xdist: (no pytest")
    assert "[2/4] pytest (uv venv): FAIL 1 failed, 3 passed, 1 skipped in 1.25s" in lines
    assert "  FAILED dev/tests/a/test_x.py::test_broken" in lines
    assert lines[-1] == "[3/4] pytest (py): PASS 4 passed in 0.90s"


def test_summarize_output_cut_off_leg():
    """A leg cut off before pytest's final line gets its last 40 lines."""
    cut = (
        CAPTURED.split("==> [3/4]", 1)[0]
        + "==> [3/4] pytest (py)\n"
        + "".join(f"progress {i}\n" for i in range(60))
    )
    lines = sp.summarize_output(cut)
    at = lines.index("[3/4] pytest (py): (no pytest result line; the last 40 lines)")
    assert lines[at + 1 :] == [f"    progress {i}" for i in range(20, 60)]
    assert "[2/4] pytest (uv venv): FAIL 1 failed, 3 passed, 1 skipped in 1.25s" in lines
