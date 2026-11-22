#!/usr/bin/env python3
"""Hyper Daemon: run exactly one slice through the workflow engine (hyper_engine.py).

Usage:
    hyper_daemon.py <slice spec path> [--resume RUN_ID] [--executor NAME]
                    [--unverified-executor] [--allow-unsandboxed] [--allow-unsandboxed-oracle]
                    [--max-budget-usd-per-run USD]

The slice spec is a legacy MiniPRD (spec/compiled/MiniPRD_<Name>.md) or a v6 slice
(missions/<m>/slices/<slice>/SPEC.md, or its directory). The engine runs it in its own
worktree with its executor, oracle and audit phases; the main checkout is not touched. Every
dependency of the slice must already be audited. The run flags pass through unchanged.

The v0.5.x form `hyper_daemon.py [--orchestrated] <superprd_name> <miniprd_path>
[--api-response-id ID]` was removed and exits 2 with this usage.

Exit codes: the engine's (0 audited, 1 blocked or stopped, 3 lock held, 5 refused), and 2 for
usage or a spec path that names no slice.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import io
import json
import sys
from pathlib import Path

from _runtime import require_runtime

require_runtime()

import hyper_engine as he  # noqa: E402
from hyper_failures import FailureType  # noqa: E402

EXIT_USAGE = 2
REMOVED = ("--orchestrated", "--api-response-id")


class SelectionError(Exception):
    """The spec path names no runnable slice."""


def select_slice(
    spec: Path, repo: he.Repo, config: dict, probes: he.Probes, build
) -> list[he.Slice]:
    """Return a one-slice plan for spec; refuse when a dependency is not yet audited.

    build is the engine's own build_plan. A legacy MiniPRD is looked up in the chain of its own
    directory; a v6 slice in the engine's mission plan.
    """
    path = spec.resolve()
    if path.is_file() and path.name.startswith("MiniPRD_") and path.suffix == ".md":
        full = he._legacy_plan(repo, path.parent)
        directory = path
    else:
        directory = path.parent if path.name == "SPEC.md" else path
        if not (directory / "SPEC.md").is_file():
            raise SelectionError(f"{spec}: neither a MiniPRD_*.md nor a v6 slice SPEC.md")
        full = build(repo, config, probes)
    try:
        rel = directory.relative_to(repo.top.resolve()).as_posix()
    except ValueError:
        raise SelectionError(f"{spec} is not inside the repository {repo.top}") from None
    found = [item for item in full if item.path == rel]
    if not found:
        raise SelectionError(f"{rel}: the engine's plan has no such slice")
    item = found[0]
    if item.initial != "pending":
        raise SelectionError(f"{item.id} is {item.initial}; there is nothing to run")
    initial = {other.id: other.initial for other in full}
    waiting = [dep for dep in item.depends_on if initial.get(dep) != "audited"]
    if waiting:
        raise SelectionError(f"{item.id} depends on {', '.join(waiting)}, not yet audited")
    return [dataclasses.replace(item, depends_on=[])]


@contextlib.contextmanager
def one_slice(spec: Path):
    """Point the engine's build_plan at spec's single slice, then restore it."""
    build = he.build_plan

    def patched(repo, config, probes):
        try:
            return select_slice(spec, repo, config, probes, build)
        except SelectionError as exc:
            raise he.EngineError(FailureType.preflight_failed.value, str(exc)) from None

    he.build_plan = patched
    try:
        yield
    finally:
        he.build_plan = build


def build_parser() -> argparse.ArgumentParser:
    """Return the CLI parser."""
    parser = argparse.ArgumentParser(prog="hyper_daemon.py", description=__doc__.split("\n")[0])
    parser.add_argument("spec", type=Path, help="the slice spec: a MiniPRD or a v6 SPEC.md")
    parser.add_argument("--resume", metavar="RUN_ID", help="continue a recorded engine run")
    parser.add_argument("--executor", default="claude-cli", help="executor name")
    parser.add_argument(
        "--unverified-executor", action="store_true", help="allow an executor with verified: false"
    )
    parser.add_argument(
        "--allow-unsandboxed", action="store_true", help="run agent phases unsandboxed"
    )
    parser.add_argument(
        "--allow-unsandboxed-oracle", action="store_true", help="run oracles without bwrap"
    )
    parser.add_argument(
        "--max-budget-usd-per-run", type=he._usd, metavar="USD", help="the run's spend cap"
    )
    return parser


def _usage_error(parser: argparse.ArgumentParser, message: str) -> int:
    parser.print_usage(sys.stderr)
    print(f"hyper_daemon.py: {message}", file=sys.stderr)
    return EXIT_USAGE


def summarize(top: Path, text: str) -> None:
    """Print the slice's status and failure type from the run the engine reported."""
    try:
        report = json.loads(text)
        state = he.load_state(top, report["run_id"])
    except (ValueError, TypeError, KeyError, OSError):
        return  # a refusal: the engine already printed its report
    for sid, record in state["slices"].items():
        failure = record.get("failure_type")
        if failure is not None:
            failure = FailureType(failure).value
        print(f"hyper_daemon.py: {sid}: {record['status']} ({failure or 'ok'})", file=sys.stderr)


def main(argv: list[str] | None = None, probes: he.Probes | None = None) -> int:
    """Run the CLI and return its exit code."""
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    removed = [arg for arg in argv if arg.split("=", 1)[0] in REMOVED]
    if removed:
        return _usage_error(parser, f"{removed[0].split('=', 1)[0]} was removed")
    ns, extra = parser.parse_known_args(argv)
    if extra:
        return _usage_error(
            parser,
            f"unexpected arguments {' '.join(extra)}: the daemon takes one slice spec path "
            "(the <superprd_name> <miniprd_path> form was removed)",
        )
    probes = probes or he.Probes()
    flags = {key: getattr(ns, key) for key in he.DEFAULT_FLAGS}
    try:
        repo = he.discover(ns.spec.resolve().parent, probes)
        config, _ = he.resolve_config(repo.top, flags)
        select_slice(ns.spec, repo, config, probes, he.build_plan)  # refuse before the lock
    except (SelectionError, he.EngineError) as exc:
        return _usage_error(parser, str(exc))
    buffer = io.StringIO()
    with one_slice(ns.spec), contextlib.redirect_stdout(buffer):
        code = he.run_engine(repo.top, flags, None, probes=probes, resume=ns.resume)
    sys.stdout.write(buffer.getvalue())
    summarize(repo.top, buffer.getvalue())
    return code


if __name__ == "__main__":
    sys.exit(main())
