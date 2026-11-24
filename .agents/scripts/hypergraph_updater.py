#!/usr/bin/env python3
"""hypergraph_updater.py — blast-radius propagation for the HACF component ledger.

Every write goes through hyper_ledger.py, the ledger's single validated writer: it takes the
ledger lock, refuses a change that adds an error the ledger did not have, and replaces the
file atomically. Normative design: the architecture.yml section of HACF's v0.6.0 design
notes (not shipped).

Usage
-----
Positional form (the mandatory execute-skill call; its contract is frozen at 984ca46):
    python3 hypergraph_updater.py <path_to_yaml> <dirty_node_1> [dirty_node_2 ...]

Diff form:
    python3 hypergraph_updater.py [<path_to_yaml>] --from-diff <rev-range>
        [--slice <SID>] [--apply] [--outbox <dir>] [--json]

Positional form: marks the named nodes `dirty` and the rest of their blast radius
(`implements` parents plus reverse `depends_on`; `member_of` is not traversed)
`needs_review`, then prints the 984ca46 banner with the blast radius sorted. A missing
ledger, an unknown node or too few arguments exits 1 with the 984ca46 message, and so does
a ledger that does not parse (984ca46 died with a traceback; this prints the parse error to
stderr); a failed write exits with hyper_ledger's code (3 lock timeout, 5 refused). Node ids
are not checked against hyper_ledger's id pattern, as in 984ca46. Warnings about
pre-existing ledger defects go to stderr, so stdout stays golden.

Diff form: maps the paths a rev-range changed (`git diff --name-only`; a single revision
compares the working tree and adds untracked, non-ignored files) to nodes through their
`files` globs (a node without `files`: its `associated_file`), and prints `nodes`, the
sorted `blast_radius` and `unmapped`, the changed paths no node covers, as
`key: [json list]` lines (`--json`: one object with those keys). Without a leading path it
reads `spec/compiled/architecture.yml` at the repository top level. Only `--apply` writes:
the mapped nodes become `dirty`, the rest of their blast radius `needs_review`, and
`--slice` is recorded in each written node's `provenance.last_slice`. Exit codes: 0 ok,
1 missing ledger, 2 usage, 3 lock timeout, 4 parse error, 5 refused.

Code graph (diff form only): when `.agents/local/hacf.yml` selects a code-graph backend other
than `none`, hyper_codegraph.py's `diff_impact` runs too, with the full commit id of a single
revision as its base; a rev-range runs no backend and reports `unsupported` (R34). On status
`ok`, the ledger nodes covering its changed and impacted files join the blast radius
(`--apply` marks them `needs_review`), and the output gains a `code_graph` entry with that
status and those components. On any other status, stderr says "code graph <status>: blast
radius from git diff plus files: globs" and the output's `code_graph` entry lists no
components. With the backend `none` (the default) the output is unchanged.

Outbox: with HACF_OUTBOX set, or `--outbox <dir>` in the diff form, a write takes no lock and
leaves the ledger byte-identical. It records hyper_ledger `mark` entries in that directory
(the named or mapped nodes `dirty`, then the rest of the radius `needs_review`), which
`hyper_ledger.py apply-outbox` replays. A `mark` entry has no provenance field, so `--slice`
is not recorded there; a warning says so. apply-outbox replays only ids that match
hyper_ledger's id pattern, so a write whose radius holds another id exits 2 and records
nothing.

Post-impact hook: after each impact that writes (the positional form, and the diff form with
--apply), `hyper_lore.stale_from_impact(<blast radius>, <changed paths>)` runs when hyper_lore
imports (S05a provides it); a missing hyper_lore is skipped silently, and the hook never fails
the updater. A read-only diff run writes no lore: the diff form's report carries
`lore_stale_candidates`, the ids `hyper_lore.stale_candidates` says the change would mark
([] without a lore graph or hyper_lore).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
from pathlib import Path
from typing import Iterator, TextIO

import _runtime
import hyper_codegraph
import hyper_ledger

EXIT_OK = 0
EXIT_FAIL = 1  # 984ca46: a missing ledger, an unknown node, too few arguments or a parse error
RULE = "=" * 50


class UnknownNode(hyper_ledger.LedgerError):
    """A node the call names is not in the ledger (984ca46 prints FATAL and exits 1)."""

    code = EXIT_FAIL


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _not_found(path_text: str, stream: TextIO) -> int:
    """Report a missing ledger with the 984ca46 line plus the first-run path (R21.6)."""
    print(f"CRITICAL ERROR: Hypergraph not found at {path_text}", file=stream)
    print(
        "Create an empty ledger with `python3 .agents/scripts/hyper_ledger.py init`, "
        "or run /hyper-discover to build one from the codebase.",
        file=stream,
    )
    return EXIT_FAIL


def _report(exc: hyper_ledger.LedgerError) -> int:
    print(str(exc), file=sys.stderr)
    for line in exc.details:
        print(f"  {line}", file=sys.stderr)
    return exc.code


def _report_write(result: hyper_ledger.WriteResult) -> None:
    """Print a ledger write's outcome and its pre-existing defects to stderr."""
    print(result.message, file=sys.stderr)
    for finding in result.carried:
        print(f"{finding.line('warning')} (pre-existing error)", file=sys.stderr)
    if result.warnings:
        print(
            f"note: the ledger has {result.warnings} warning(s); list them with "
            "`python3 .agents/scripts/hyper_ledger.py validate`",
            file=sys.stderr,
        )


def _mark_args(ids: list[str], radius: list[str]) -> list[dict]:
    """Return the hyper_ledger `mark` arguments that dirty ids and flag the rest of radius.

    They skip normalize_args, which checks ids against the CLI pattern: 984ca46 marked any
    string id, so a ledger whose ids predate the pattern must keep working.
    """
    marks = [{"status": "dirty", "ids": list(ids)}]
    rest = [node_id for node_id in radius if node_id not in ids]
    if rest:
        marks.append({"status": "needs_review", "ids": rest})
    return marks


def _outbox_marks(ids: list[str], radius: list[str]) -> list[dict]:
    """Return the outbox entries' `mark` arguments; refuse ids apply-outbox cannot replay."""
    marks = _mark_args(ids, radius)
    pattern = hyper_ledger.ID_RE
    unreplayable = sorted(
        {node_id for args in marks for node_id in args["ids"] if not pattern.match(node_id)}
    )
    if unreplayable:
        # apply-outbox checks every entry's ids against ID_RE, so such an entry would only
        # fail later, at replay; refuse now, before any entry is written.
        raise hyper_ledger.UsageError(
            f"refused: {hyper_ledger.OUTBOX_ENV} is set and node id(s) "
            f"{', '.join(unreplayable)} do not match {pattern.pattern}, so "
            "`hyper_ledger.py apply-outbox` could not replay their mark entry; nothing written"
        )
    return [hyper_ledger.normalize_args("mark", args) for args in marks]


def _record_slice(node: dict, slice_id: str) -> None:
    if "_provenance" in node:
        # migrate-v2 stops on a node whose _provenance and provenance differ.
        raise hyper_ledger.Refused(
            f"refused: node {node.get('id')} still has a v1 _provenance record; run "
            "`python3 .agents/scripts/hyper_ledger.py migrate-v2` before --slice"
        )
    provenance = node.setdefault("provenance", {})
    if not isinstance(provenance, dict):
        raise hyper_ledger.Refused(
            f"refused: node {node.get('id')} has a provenance that is not a mapping; fix it"
        )
    provenance["last_slice"] = slice_id


def propagate(
    data: dict, ids: list[str], slice_id: str | None = None, extra: list[str] | None = None
) -> list[str]:
    """Mark ids dirty and the rest of their blast radius needs_review in data; return the radius.

    The marks run hyper_ledger's own `mark` operation, the one an outbox entry replays.
    `extra` (code-graph components) joins the radius as needs_review; ids the ledger lacks
    are skipped. Raises UnknownNode, naming the first of ids the ledger lacks, before
    anything changes.
    """
    index = hyper_ledger.node_index(data)
    for node_id in ids:
        if node_id not in index:
            raise UnknownNode(f"FATAL: Dirty node '{node_id}' does not exist in hypergraph.")
    radius = hyper_ledger.blast_radius(data, ids)
    if extra:
        radius = sorted(set(radius) | {node_id for node_id in extra if node_id in index})
    for args in _mark_args(ids, radius):
        hyper_ledger.APPLY["mark"](data, args, Path())
    if slice_id:
        for node in hyper_ledger.node_list(data):
            if isinstance(node, dict) and node.get("id") in radius:
                _record_slice(node, slice_id)
    return radius


def _write(
    path: Path,
    ids: list[str],
    slice_id: str | None,
    outbox: Path | None,
    extra: list[str] | None = None,
) -> list[str]:
    """Apply propagate() to the ledger, or record it in the outbox; return the blast radius."""
    if outbox is not None:
        # No lock and no ledger write: the entries are replayed later by apply-outbox.
        _, data = hyper_ledger.load_ledger(path)
        radius = propagate(data, ids, extra=extra)
        for args in _outbox_marks(ids, radius):
            entry = hyper_ledger.write_outbox_entry(outbox, "mark", args)
            print(f"mark: recorded as outbox entry {entry}; ledger not written", file=sys.stderr)
        if slice_id:
            print(
                f"warning: --slice {slice_id} is not recorded: an outbox mark entry has no "
                "provenance field",
                file=sys.stderr,
            )
        return radius
    holder: list[list[str]] = []

    def mutate(data: dict) -> str:
        holder.append(propagate(data, ids, slice_id, extra))
        return f"mark {', '.join(ids)} dirty and their blast radius needs_review"

    _report_write(hyper_ledger.update(path, mutate))
    return holder[0]


def _outbox_dir(explicit: str | None = None) -> Path | None:
    value = explicit or os.environ.get(hyper_ledger.OUTBOX_ENV)
    return Path(value) if value else None


@contextlib.contextmanager
def _outbox_env(outbox: Path | None) -> Iterator[None]:
    """Expose an `--outbox` directory to in-process hooks as HACF_OUTBOX, then restore it."""
    key = hyper_ledger.OUTBOX_ENV
    saved = os.environ.get(key)
    if outbox is not None:
        os.environ[key] = str(outbox)
    try:
        yield
    finally:
        if saved is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = saved


def post_impact(node_ids: list[str], changed_paths: list[str]) -> None:
    """Let hyper_lore mark lore anchored to an impact stale; skip it when hyper_lore is absent."""
    try:
        import hyper_lore
    except ImportError:
        return
    except Exception as exc:  # a broken hyper_lore must not fail the updater
        print(f"warning: post-impact hook skipped: hyper_lore: {exc}", file=sys.stderr)
        return
    hook = getattr(hyper_lore, "stale_from_impact", None)
    if not callable(hook):
        return
    try:
        marked = hook(list(node_ids), list(changed_paths))
    except Exception as exc:  # stale_from_impact catches its own errors; this is a backstop
        print(f"warning: hyper_lore.stale_from_impact failed: {exc}", file=sys.stderr)
        return
    if marked:
        print(f"lore: marked stale: {', '.join(map(str, marked))}", file=sys.stderr)


def lore_candidates(node_ids: list[str], changed_paths: list[str]) -> list[str]:
    """Return the lore ids an impact would mark stale, writing nothing; [] without hyper_lore."""
    try:
        import hyper_lore
    except ImportError:
        return []
    except Exception as exc:  # a broken hyper_lore must not fail the updater
        print(f"warning: lore candidates skipped: hyper_lore: {exc}", file=sys.stderr)
        return []
    preview = getattr(hyper_lore, "stale_candidates", None)
    if not callable(preview):
        return []
    try:
        return [str(item) for item in preview(list(node_ids), list(changed_paths))]
    except Exception as exc:  # stale_candidates catches its own errors; this is a backstop
        print(f"warning: hyper_lore.stale_candidates failed: {exc}", file=sys.stderr)
        return []


# ---------------------------------------------------------------------------
# Positional form (984ca46 contract)
# ---------------------------------------------------------------------------


def propagate_blast_radius(yaml_path: str, dirty_node_ids: list[str]) -> int:
    """Run the positional form on the ledger at yaml_path; return the exit code."""
    path = Path(yaml_path)
    if not os.path.exists(path):
        return _not_found(yaml_path, sys.stdout)
    try:
        radius = _write(path, dirty_node_ids, None, _outbox_dir())
    except UnknownNode as exc:
        print(str(exc))
        return exc.code
    except hyper_ledger.ParseError as exc:
        # 984ca46 died with a traceback here (exit 1); keep its exit code, not EXIT_PARSE.
        _report(exc)
        return EXIT_FAIL
    except hyper_ledger.LedgerError as exc:
        return _report(exc)

    print(RULE)
    print("HYPERGRAPH UPDATER: SUCCESS")
    print(RULE)
    print(f"Target YAML: {yaml_path}")
    print(f"Nodes dirtied by direct execution: {dirty_node_ids}")
    print(f"Total nodes needing review (Blast Radius): {radius}")
    print(RULE)
    print("AGENT INSTRUCTION: If you are the Builder Agent, your job is complete.")
    print("If you are the Auditor Agent, you must now semantically rewrite the")
    print("'outputs', 'inputs', and 'description' of these nodes based on the")
    print("new code, and return their state to 'clean'.")
    print(RULE)
    sys.stdout.flush()
    post_impact(radius, [])
    return EXIT_OK


# ---------------------------------------------------------------------------
# Diff form
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Return the parser for the diff form's flags (the leading ledger path is split off)."""
    parser = argparse.ArgumentParser(
        prog="hypergraph_updater.py [<path_to_yaml>]",
        description="Blast radius of a git diff, mapped through the ledger's files: globs.",
        allow_abbrev=False,
    )
    parser.add_argument(
        "--from-diff",
        required=True,
        metavar="REV-RANGE",
        help="A..B, or one revision to compare the working tree (untracked files included)",
    )
    parser.add_argument("--slice", metavar="SID", help="record SID in provenance.last_slice")
    parser.add_argument(
        "--apply", action="store_true", help="mark the nodes dirty and their radius needs_review"
    )
    parser.add_argument(
        "--outbox", metavar="DIR", help="record the --apply write in DIR, not the ledger"
    )
    parser.add_argument("--json", action="store_true", help="print one JSON object")
    return parser


def run_from_diff(yaml_path: str | None, ns: argparse.Namespace) -> int:
    """Run the diff form; return the exit code."""
    path = Path(yaml_path) if yaml_path else hyper_ledger.default_ledger_path()
    if not os.path.exists(path):
        return _not_found(yaml_path or str(path), sys.stderr)
    outbox = _outbox_dir(ns.outbox)
    impact = hyper_ledger.impact_from_diff(path, ns.from_diff)
    report: dict = {key: impact[key] for key in ("nodes", "blast_radius", "unmapped")}
    graph = code_graph_impact(path, ns.from_diff)
    extra: list[str] = []
    if graph is not None:
        if graph["status"] == "ok":
            extra = graph["components"]
            report["blast_radius"] = sorted(set(report["blast_radius"]) | set(extra))
        report["code_graph"] = {"status": graph["status"], "components": extra}
    if ns.apply:
        if impact["nodes"]:
            _write(path, impact["nodes"], ns.slice, outbox, extra)
        else:
            print("--apply: no changed path maps to a node; nothing written", file=sys.stderr)
    report["lore_stale_candidates"] = lore_candidates(report["blast_radius"], impact["changed"])
    if ns.json:
        print(json.dumps(report, indent=2))
    else:
        for key, items in report.items():
            print(f"{key}: {json.dumps(items)}")
    sys.stdout.flush()
    if ns.apply:  # a read-only run writes no lore; it reports lore_stale_candidates instead
        with _outbox_env(outbox):
            post_impact(report["blast_radius"], impact["changed"])
    return EXIT_OK


def code_graph_impact(ledger: Path, rev: str) -> dict | None:
    """Return the code graph's diff_impact result for `--from-diff rev`, or None.

    None when hacf.yml selects the backend `none` (or nothing), so that case runs nothing and
    prints nothing. A single revision passes its full commit id as the base (R34), so
    detect_changes compares the working tree with the commit --from-diff compares. A rev-range
    runs no backend: detect_changes always compares the working tree. On any status but ok,
    stderr says the blast radius falls back to git diff plus files: globs. A broken
    code-graph configuration comes back as a result with status `error`; it never fails the
    updater.
    """
    top = hyper_ledger.repo_toplevel(Path(os.path.abspath(ledger)).parent)
    if top is None:
        return None
    try:
        if hyper_codegraph.configured_backend(top) == hyper_codegraph.BACKEND_NONE:
            return None
        if ".." in rev:  # the rev-range rule hyper_ledger.changed_paths uses
            result = hyper_codegraph.new_result("diff_impact")
            result["status"] = "unsupported"
            print(
                hyper_codegraph.fallback_line("unsupported", "for a revision range"),
                file=sys.stderr,
            )
            return result
        base = hyper_codegraph.commit_id(top, rev)
        result = hyper_codegraph.run_capability(
            "diff_impact", repo_root=top, base=base, ledger=ledger
        )
    except hyper_codegraph.CodeGraphError as exc:
        result = hyper_codegraph.new_result("diff_impact")
        result["error"] = str(exc)
    if result["error"]:
        print(f"code graph: {result['error']}", file=sys.stderr)
    if result["status"] != "ok":
        print(hyper_codegraph.fallback_line(result["status"]), file=sys.stderr)
    return result


def _diff_main(argv: list[str]) -> int:
    yaml_path = argv[0] if argv and not argv[0].startswith("-") else None
    parser = build_parser()
    try:
        ns = parser.parse_args(argv[1:] if yaml_path else argv)
        if (ns.slice is not None or ns.outbox is not None) and not ns.apply:
            parser.error("--slice and --outbox only take effect with --apply")
        if ns.slice is not None:
            ns.slice = ns.slice.strip()
            if not ns.slice:
                parser.error("--slice needs a slice id (for example S01c)")
    except SystemExit as exc:  # argparse: --help (0) or a usage error (2)
        return exc.code if isinstance(exc.code, int) else hyper_ledger.EXIT_USAGE
    try:
        return run_from_diff(yaml_path, ns)
    except UnknownNode as exc:
        print(str(exc), file=sys.stderr)
        return exc.code
    except hyper_ledger.LedgerError as exc:
        return _report(exc)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _print_usage() -> None:
    print(
        "Usage:\n"
        "  Blast-radius propagation:\n"
        "    python3 hypergraph_updater.py <path_to_yaml> <dirty_node_1> [dirty_node_2 ...]\n\n"
        "  Blast radius of a diff (writes only with --apply):\n"
        "    python3 hypergraph_updater.py [<path_to_yaml>] --from-diff <rev-range> "
        "[--slice <SID>] [--apply] [--outbox <dir>] [--json]\n"
    )


def main(argv: list[str] | None = None) -> int:
    """Run the CLI and return its exit code."""
    _runtime.require_runtime()
    argv = sys.argv[1:] if argv is None else list(argv)
    diff_form = any(arg == "--from-diff" or arg.startswith("--from-diff=") for arg in argv)
    if diff_form or (argv and argv[0].startswith("-")):
        return _diff_main(argv)
    if len(argv) < 2:
        _print_usage()
        return EXIT_FAIL
    return propagate_blast_radius(argv[0], argv[1:])


if __name__ == "__main__":
    sys.exit(main())
