#!/usr/bin/env python3
"""Hyper Orchestrator: a thin CLI over the workflow engine (hyper_engine.py).

Usage:
    hyper_orchestrator.py <mission dir | legacy compiled dir> [--dry-run] [--max-workers N]
                          [--resume RUN_ID] [--executor NAME] [--unverified-executor]
                          [--allow-unsandboxed] [--allow-unsandboxed-oracle]
                          [--max-budget-usd-per-run USD]

The target names the slices to run: a v6 mission directory (it holds mission.yaml) selects that
mission's slices, and a legacy compiled directory (it holds MiniPRD_*.md) selects its flat
MiniPRDs as a chain. The engine resolves them, orders them as a DAG and runs each one in its
own worktree; nothing here calls the Anthropic API, checks out a branch in the working tree or
pairs MiniPRDs by name. A dependency outside the target must already be audited.

--max-workers sets autonomy.max_parallel (capped at 8). The run flags pass through to the
engine unchanged. --dry-run prints the slices in DAG order and the engine command, then
returns 0 without taking engine.lock or writing a run dir, worktree or branch.

After a run, every slice the engine blocked gets one entry in spec/active/FAILED_WORKFLOWS.md.
At 50 entries the file is rotated into spec/archive/ by `archive_specs.py --paths` (the only
writer of spec/archive/) and started again.

Exit codes: the engine's (0 ok, 1 blocked slices or stopped, 3 lock held, 5 refused), and 2 for
usage, including the removed --max-api-calls.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import io
import json
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Callable, Iterator

from _runtime import require_runtime

require_runtime()

import _runtime  # noqa: E402
import hyper_engine as he  # noqa: E402

SCRIPTS_DIR = Path(__file__).resolve().parent
ARCHIVE_SCRIPT = SCRIPTS_DIR / "archive_specs.py"
ENGINE_SCRIPT = SCRIPTS_DIR / "hyper_engine.py"
MAX_WORKERS_CAP = 8
EXIT_USAGE = 2
FAILED_WORKFLOWS_REL = "spec/active/FAILED_WORKFLOWS.md"
FAILED_ENTRY_MARK = "## [FAILED]"
MAX_FAILED_ENTRIES = 50
FAILED_WORKFLOWS_HEADER = (
    "# Failed Workflows\n\n"
    "Failure entries, appended by the HACF tools that fail a workflow. At 50 entries the file\n"
    "is rotated into spec/archive/ by `archive_specs.py --paths` and started again.\n\n---\n"
)
REMOVED_FLAGS = {
    "--max-api-calls": "--max-api-calls was removed; cap a run's spend with "
    "--max-budget-usd-per-run <usd>",
}


class SelectionError(Exception):
    """The target names no runnable slice set."""


# ---------------------------------------------------------------------------
# Slice selection
# ---------------------------------------------------------------------------


def _inside(path: Path, top: Path) -> str:
    """Return path relative to top (posix), or raise SelectionError when it lies outside."""
    try:
        return path.resolve().relative_to(top.resolve()).as_posix()
    except ValueError:
        raise SelectionError(f"{path} is not inside the repository {top}") from None


def detach(chosen: list[he.Slice], full: list[he.Slice], where: str) -> list[he.Slice]:
    """Drop depends_on edges to audited slices outside chosen; refuse any other outside edge."""
    ids = {item.id for item in chosen}
    initial = {item.id: item.initial for item in full}
    result = []
    for item in chosen:
        kept = []
        for dep in item.depends_on:
            if dep in ids:
                kept.append(dep)
            elif initial.get(dep) != "audited":
                raise SelectionError(
                    f"{item.id} depends on {dep}, which is outside {where} and not audited"
                )
        result.append(dataclasses.replace(item, depends_on=kept))
    return result


def select_plan(
    target: Path,
    repo: he.Repo,
    config: dict,
    probes: he.Probes,
    build: Callable[..., list[he.Slice]],
) -> list[he.Slice]:
    """Return the slices target names, in the engine's DAG order.

    build is the engine's own build_plan (v6 missions when any exist, else the legacy dir).
    """
    rel = _inside(target, repo.top)
    if (target / "mission.yaml").is_file():
        full = build(repo, config, probes)
        chosen = [item for item in full if item.path == rel or item.path.startswith(rel + "/")]
        if not chosen:
            raise SelectionError(f"{rel}: the engine's plan has no slice in this mission")
        return detach(chosen, full, rel)
    if target.is_dir() and any(target.glob("MiniPRD_*.md")):
        return he._legacy_plan(repo, target.resolve())
    raise SelectionError(
        f"{target}: neither a v6 mission directory (mission.yaml) nor a legacy compiled "
        "directory (MiniPRD_*.md)"
    )


@contextlib.contextmanager
def engine_overrides(
    plan: Callable[..., list[he.Slice]], max_parallel: int | None = None
) -> Iterator[None]:
    """Run the engine in-process with this plan and max_parallel, then restore its functions.

    plan(repo, config, probes, build) replaces build_plan, where build is the engine's own;
    max_parallel, when given, replaces autonomy.max_parallel after the config is resolved.
    """
    build, resolve = he.build_plan, he.resolve_config

    def patched_build(repo, config, probes):
        return plan(repo, config, probes, build)

    def patched_resolve(top, flags=None):
        config, ignored = resolve(top, flags)
        if max_parallel is not None:
            config["autonomy"]["max_parallel"] = max_parallel
        return config, ignored

    he.build_plan, he.resolve_config = patched_build, patched_resolve
    try:
        yield
    finally:
        he.build_plan, he.resolve_config = build, resolve


def run_captured(top: Path, flags: dict, probes: he.Probes, resume: str | None) -> tuple:
    """Run the engine; echo and return (exit code, its stdout report or None)."""
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = he.run_engine(top, flags, None, probes=probes, resume=resume)
    text = buffer.getvalue()
    sys.stdout.write(text)
    try:
        report = json.loads(text)
    except ValueError:
        report = None
    return code, report if isinstance(report, dict) else None


# ---------------------------------------------------------------------------
# FAILED_WORKFLOWS.md
# ---------------------------------------------------------------------------


def rotate_failed_workflows(path: Path, archive_script: Path = ARCHIVE_SCRIPT) -> bool:
    """Move the log into spec/archive/ through archive_specs.py; True when it moved."""
    argv = [
        sys.executable,
        str(archive_script),
        "--paths",
        str(path.resolve()),
        "--label",
        "failed_workflows",
    ]
    proc = subprocess.run(argv, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        output = (proc.stdout + proc.stderr).strip()
        print(
            f"hyper_orchestrator.py: rotation refused; appending anyway: {output}", file=sys.stderr
        )
        return False
    return True


def append_failed_workflow(top: Path, entry: str, archive_script: Path = ARCHIVE_SCRIPT) -> Path:
    """Append one entry to spec/active/FAILED_WORKFLOWS.md, rotating it first at 50 entries."""
    path = Path(top) / FAILED_WORKFLOWS_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        text = path.read_text(encoding="utf-8", errors="replace")
        if text.count(FAILED_ENTRY_MARK) >= MAX_FAILED_ENTRIES:
            rotate_failed_workflows(path, archive_script)
    if not path.exists():
        path.write_text(FAILED_WORKFLOWS_HEADER, encoding="utf-8")
    with path.open("a", encoding="utf-8") as fh:
        fh.write(entry)
    return path


def failed_entries(state: dict, plan: dict[str, str]) -> list[str]:
    """Return one FAILED_WORKFLOWS entry per slice the run blocked (upstream ones excluded)."""
    entries = []
    now = _runtime.utc_now_iso()
    for sid, record in state.get("slices", {}).items():
        if record.get("status") != "blocked":
            continue
        kept = record.get("worktree") if record.get("worktree_kept") else "-"
        entries.append(
            f"\n{FAILED_ENTRY_MARK} {sid} — {now}\n\n"
            f"- **Run:** {state.get('run_id')}\n"
            f"- **Slice:** {sid}\n"
            f"- **Spec:** {plan.get(sid, '-')}\n"
            f"- **Failure Type:** {record.get('failure_type')}\n"
            f"- **Attempts:** {record.get('attempts', 0)}\n"
            f"- **Worktree kept:** {kept}\n"
            "\n---\n"
        )
    return entries


def record_failures(
    top: Path, report: dict | None, paths: dict[str, str], archive_script: Path = ARCHIVE_SCRIPT
) -> int:
    """Append the blocked slices of the run named in report; return how many were written.

    paths maps a slice id to its spec path (the selected plan).
    """
    if not report or "run_id" not in report:
        return 0
    try:
        state = he.load_state(top, report["run_id"])
    except (OSError, ValueError, KeyError) as exc:
        print(f"hyper_orchestrator.py: cannot read the run report: {exc}", file=sys.stderr)
        return 0
    entries = failed_entries(state, paths)
    for entry in entries:
        append_failed_workflow(top, entry, archive_script)
    return len(entries)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def add_run_flags(parser: argparse.ArgumentParser) -> None:
    """Add the engine's run flags, passed through unchanged."""
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


def engine_flags(ns: argparse.Namespace) -> dict:
    """Return the engine's run flags from the parsed namespace."""
    return {key: getattr(ns, key) for key in he.DEFAULT_FLAGS}


def engine_argv(ns: argparse.Namespace) -> list[str]:
    """Return the engine command line equivalent to these run flags."""
    argv = ["python3", str(ENGINE_SCRIPT), "run"]
    if ns.resume:
        argv += ["--resume", ns.resume]
    if ns.executor != he.DEFAULT_FLAGS["executor"]:
        argv += ["--executor", ns.executor]
    for key in ("unverified_executor", "allow_unsandboxed", "allow_unsandboxed_oracle"):
        if getattr(ns, key):
            argv.append("--" + key.replace("_", "-"))
    if ns.max_budget_usd_per_run is not None:
        argv += ["--max-budget-usd-per-run", str(ns.max_budget_usd_per_run)]
    return argv


def build_parser() -> argparse.ArgumentParser:
    """Return the CLI parser."""
    parser = argparse.ArgumentParser(
        prog="hyper_orchestrator.py", description=__doc__.split("\n")[0]
    )
    parser.add_argument("target", type=Path, help="a v6 mission dir or a legacy compiled dir")
    parser.add_argument(
        "--dry-run", action="store_true", help="print the DAG order and the engine command only"
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        metavar="N",
        help=f"autonomy.max_parallel for this run (capped at {MAX_WORKERS_CAP})",
    )
    add_run_flags(parser)
    return parser


def removed_flag(argv: list[str]) -> str | None:
    """Return the message for a removed flag in argv, if any."""
    for arg in argv:
        name = arg.split("=", 1)[0]
        if name in REMOVED_FLAGS:
            return REMOVED_FLAGS[name]
    return None


def dry_run(target: Path, ns: argparse.Namespace, probes: he.Probes, workers: int | None) -> int:
    """Print the slices in DAG order and the engine command; take no lock, write nothing."""
    repo = he.discover(target, probes)
    config, _ = he.resolve_config(repo.top, engine_flags(ns))
    plan = select_plan(target, repo, config, probes, he.build_plan)
    print(f"slices ({len(plan)}) in DAG order from {_inside(target, repo.top)}:")
    for index, item in enumerate(plan, 1):
        status = "" if item.initial == "pending" else f"  [{item.initial}]"
        print(f"  {index}. {item.id}  {item.path}{status}")
    parallel = workers if workers is not None else config["autonomy"]["max_parallel"]
    print(f"max_parallel: {parallel}")
    print("engine command (run in-process, limited to the slices above):")
    print("  " + shlex.join(engine_argv(ns)))
    return he.EXIT_OK


def main(argv: list[str] | None = None, probes: he.Probes | None = None) -> int:
    """Run the CLI and return its exit code."""
    argv = list(sys.argv[1:] if argv is None else argv)
    message = removed_flag(argv)
    parser = build_parser()
    if message:
        parser.print_usage(sys.stderr)
        print(f"hyper_orchestrator.py: {message}", file=sys.stderr)
        return EXIT_USAGE
    ns = parser.parse_args(argv)
    probes = probes or he.Probes()
    workers = ns.max_workers
    if workers is not None:
        if workers < 1:
            parser.print_usage(sys.stderr)
            print("hyper_orchestrator.py: --max-workers must be at least 1", file=sys.stderr)
            return EXIT_USAGE
        if workers > MAX_WORKERS_CAP:
            print(f"hyper_orchestrator.py: --max-workers {workers} capped at 8", file=sys.stderr)
            workers = MAX_WORKERS_CAP
    target = ns.target
    try:
        if ns.dry_run:
            return dry_run(target, ns, probes, workers)
        repo = he.discover(target, probes)
        paths: dict[str, str] = {}

        def plan(repo, config, probes, build):
            try:
                chosen = select_plan(target, repo, config, probes, build)
            except SelectionError as exc:
                raise he.EngineError("preflight_failed", str(exc)) from None
            paths.update((item.id, item.path) for item in chosen)
            return chosen

        with engine_overrides(plan, workers):
            code, report = run_captured(repo.top, engine_flags(ns), probes, ns.resume)
    except (SelectionError, he.EngineError) as exc:
        print(f"hyper_orchestrator.py: {exc}", file=sys.stderr)
        return EXIT_USAGE
    written = record_failures(repo.top, report, paths)
    if written:
        print(f"hyper_orchestrator.py: {written} blocked slice(s) in {FAILED_WORKFLOWS_REL}")
    return code


if __name__ == "__main__":
    sys.exit(main())
