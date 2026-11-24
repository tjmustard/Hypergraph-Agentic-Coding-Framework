"""Short per-leg summaries of check.sh's pytest and ruff legs (OPR.99.0.7.3).

check.sh writes each leg's full output to a log (and pytest's junit XML next to it) and prints
only this script's block: the status, the counts, the failing ids with the tail of their
tracebacks, and the log path. A summary never hides a failure: when the junit XML is missing or
does not parse, or a failing leg names no failing test, the block prints the log's last lines.

    python3 dev/scripts/summarize_pytest.py --label '[2/4] pytest (uv venv)' --exit 1 \
        --log RUN/2-pytest-uv.log --junitxml RUN/2-pytest-uv.xml
    python3 dev/scripts/summarize_pytest.py --raw --label '[4/4] ruff' --exit 0 \
        --note '3 file(s)' --log RUN/4-ruff.log

It always exits 0: a summary problem never changes check.sh's verdict. `summarize_output(text)`
summarizes a captured check.sh output instead (the release gate), leg by leg.

Python 3.10+, standard library only.
"""

from __future__ import annotations

import argparse
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

INDENT = "    "
MAX_FAILURES = 20
TB_LINES = 15
TAIL = 40


@dataclass
class Junit:
    """The counts and failing tests of one junit XML."""

    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    seconds: float = 0.0
    # (kind, id, text): kind is FAILED or ERROR
    failures: list[tuple[str, str, str]] = field(default_factory=list)


def _test_id(classname: str, name: str, root: Path) -> str:
    """`<file>::[<Class>::]<name>` from junit's dotted `classname`, as pytest names a test.

    The module is the longest dotted prefix that is a file under `root`; without one, the parts
    from the first capitalised one on are classes.
    """
    parts = [p for p in classname.split(".") if p]
    for cut in range(len(parts), 0, -1):
        if (root / Path(*parts[:cut])).with_suffix(".py").is_file():
            break
    else:
        cut = next((i for i, p in enumerate(parts) if i and p[:1].isupper()), len(parts))
    if not parts:
        return name
    return "::".join(["/".join(parts[:cut]) + ".py", *parts[cut:], name])


def parse_junit(path: Path, root: Path | None = None) -> Junit:
    """Count the testcases of a junit XML; raises OSError or ParseError when it cannot."""
    root = root or Path.cwd()
    tree = ET.parse(path).getroot()
    suites = [tree] if tree.tag == "testsuite" else list(tree.iter("testsuite"))
    out = Junit()
    for suite in suites:
        out.seconds += float(suite.get("time") or 0)
        for case in suite.iter("testcase"):
            test = _test_id(case.get("classname", ""), case.get("name", ""), root)
            kinds = {child.tag: child for child in case}
            if "error" in kinds:
                out.errors += 1
                out.failures.append(("ERROR", test, _text(kinds["error"])))
            elif "failure" in kinds:
                out.failed += 1
                out.failures.append(("FAILED", test, _text(kinds["failure"])))
            elif "skipped" in kinds:
                out.skipped += 1
            else:
                out.passed += 1
    return out


def _text(node: ET.Element) -> str:
    """A failure's traceback text, or its message when the text is empty."""
    return (node.text or "").strip() or node.get("message", "")


def _status(code: int) -> str:
    return f"{'PASS' if code == 0 else 'FAIL'} (exit {code})"


def _read_tail(log: Path, tail: int) -> list[str]:
    """The log's last `tail` lines, or the one `(log unreadable: …)` line."""
    try:
        lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        return [f"(log unreadable: {exc.strerror or exc})"]
    return lines[-tail:] if tail else []


def pytest_block(
    label: str,
    code: int,
    log: Path,
    junitxml: Path | None,
    max_failures: int = MAX_FAILURES,
    tb_lines: int = TB_LINES,
    tail: int = TAIL,
) -> list[str]:
    """The summary block of one pytest leg, unindented (sub-lines carry their own indent)."""
    paths = f"log: {log}" + (f"  junit: {junitxml}" if junitxml else "")
    try:
        if junitxml is None:
            raise OSError("no --junitxml")
        j = parse_junit(junitxml)
    except (OSError, ET.ParseError, ValueError):
        lines = [f"{label}: {_status(code)}", f"  (no junit XML; the last {tail} lines of the log)"]
        return lines + [f"    {ln}" for ln in _read_tail(log, tail)] + [f"  {paths}"]
    counts = (
        f"{j.passed} passed, {j.failed} failed, {j.errors} errors, {j.skipped} skipped "
        f"in {j.seconds:.1f}s"
    )
    lines = [f"{label}: {_status(code)} {counts}"]
    for kind, test, text in j.failures[:max_failures]:
        lines.append(f"  {kind} {test}")
        lines += [f"    {ln}" for ln in text.splitlines()[-tb_lines:]]
    if len(j.failures) > max_failures:
        lines.append(f"  … and {len(j.failures) - max_failures} more")
    if code != 0 and not j.failures:
        # A red leg whose XML names no failing test (an internal error, an interrupt, exit 5):
        # the log tail is the only evidence, so it is always shown.
        lines.append(f"  (no failing test in the junit XML; the last {tail} lines of the log)")
        lines += [f"    {ln}" for ln in _read_tail(log, tail)]
    lines.append(f"  {paths}")
    return lines


def raw_block(label: str, code: int, log: Path, note: str = "", tail: int = TAIL) -> list[str]:
    """The ruff leg's block: status and note; on FAIL the log's last lines; the log path."""
    lines = [f"{label}: {_status(code)}" + (f" {note}" if note else "")]
    if code != 0:
        lines += [f"    {ln}" for ln in _read_tail(log, tail)]
    lines.append(f"  log: {log}")
    return lines


# --------------------------------------------------------------- captured check.sh output

HEADER_RE = re.compile(r"^==> (\[\d+/4\] .*)$")
# pytest's final line: `===== 1 failed, 3 passed in 0.12s =====` (framed, or bare under -q).
FINAL_RE = re.compile(r"^=*\s*((?:\d+ [a-z]+(?:, )?)+) in ([0-9.]+)s\b.*?=*\s*$")
COUNT_RE = re.compile(r"(\d+) ([a-z]+)")
ID_RE = re.compile(r"^(FAILED|ERROR) (\S+)")


def summarize_output(text: str, max_failures: int = MAX_FAILURES, tail: int = TAIL) -> list[str]:
    """Per-leg lines for a captured check.sh output, split at its `==> [n/4]` headers.

    A leg with pytest's final line gets its status, counts and FAILED/ERROR ids; a leg without
    one (the preflight, ruff, or a pytest leg cut off before its end) gets its last lines.
    """
    legs: list[tuple[str, list[str]]] = []
    for line in text.splitlines():
        match = HEADER_RE.match(line)
        if match:
            legs.append((match[1], []))
        elif legs:
            legs[-1][1].append(line)
    out: list[str] = []
    for label, body in legs:
        final = next((m for m in map(FINAL_RE.match, reversed(body)) if m), None)
        if final is None:
            out.append(f"{label}: (no pytest result line; the last {tail} lines)")
            out += [f"    {ln}" for ln in body[-tail:]]
            continue
        counts = {kind: int(n) for n, kind in COUNT_RE.findall(final[1])}
        bad = counts.get("failed", 0) + counts.get("error", 0) + counts.get("errors", 0)
        out.append(f"{label}: {'FAIL' if bad else 'PASS'} {final[1]} in {final[2]}s")
        ids = [f"{m[1]} {m[2]}" for m in map(ID_RE.match, body) if m]
        out += [f"  {i}" for i in ids[:max_failures]]
        if len(ids) > max_failures:
            out.append(f"  … and {len(ids) - max_failures} more")
    return out


def main(argv: list[str] | None = None) -> int:
    """Print one leg's block, indented; always exit 0."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--label", required=True)
    parser.add_argument("--exit", dest="code", type=int, required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--junitxml", type=Path)
    parser.add_argument("--max-failures", type=int, default=MAX_FAILURES)
    parser.add_argument("--tb-lines", type=int, default=TB_LINES)
    parser.add_argument("--tail", type=int, default=TAIL)
    parser.add_argument("--raw", action="store_true", help="no junit parse (the ruff leg)")
    parser.add_argument("--note", default="", help="text after the status (the ruff leg)")
    try:
        args = parser.parse_args(argv)
        if args.raw:
            lines = raw_block(args.label, args.code, args.log, args.note, args.tail)
        else:
            lines = pytest_block(
                args.label,
                args.code,
                args.log,
                args.junitxml,
                args.max_failures,
                args.tb_lines,
                args.tail,
            )
        print("\n".join(INDENT + ln for ln in lines), flush=True)
    except SystemExit as exc:  # argparse: the usage error is already on stderr
        print(f"{INDENT}(summary failed: bad arguments, exit {exc.code})", flush=True)
    except Exception as exc:  # noqa: BLE001 - a summary problem never changes the verdict
        print(f"{INDENT}(summary failed: {type(exc).__name__}: {exc})", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
