#!/usr/bin/env python3
"""Which files a seat opened, read from its transcripts (OPR.99.0.16.12 MR 7, R217). Dev only.

    tool_paths.py --root <dir> <transcript.jsonl>...

From the named transcripts only, it reads each `tool_use` block's `name` and its `file_path`,
`path` or `notebook_path` input, and nothing else: never message text, never a tool result. It
prints {"paths": [...], "outside": [...]}: the sorted paths relative to the root, and the paths
outside it as they are. Stdlib only, Python 3.10+. It writes nothing.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path

PATH_KEYS = ("file_path", "path", "notebook_path")


def tool_inputs(transcript: Path) -> Iterable[str]:
    """Yield the path inputs of a transcript's tool_use blocks.

    Raises:
        OSError: When the transcript cannot be read.
    """
    with transcript.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if not isinstance(entry, dict) or entry.get("type") != "assistant":
                continue
            message = entry.get("message")
            content = message.get("content") if isinstance(message, dict) else None
            for block in content if isinstance(content, list) else []:
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                inputs = block.get("input")
                if not isinstance(block.get("name"), str) or not isinstance(inputs, dict):
                    continue
                for key in PATH_KEYS:
                    value = inputs.get(key)
                    if isinstance(value, str) and value:
                        yield value


def opened(transcripts: Sequence[Path], root: Path) -> dict[str, list[str]]:
    """Return the opened paths: those under the root relative to it, the others under `outside`.

    A relative input is taken from the root. Paths are normalised, not resolved.

    Raises:
        OSError: When a transcript cannot be read.
    """
    base = os.path.normpath(os.path.abspath(root))
    inside: set[str] = set()
    outside: set[str] = set()
    for transcript in transcripts:
        for raw in tool_inputs(transcript):
            path = os.path.normpath(raw if os.path.isabs(raw) else os.path.join(base, raw))
            if path == base:
                inside.add(".")
            elif path.startswith(base + os.sep):
                inside.add(path[len(base) + 1 :])
            else:
                outside.add(path)
    return {"paths": sorted(inside), "outside": sorted(outside)}


def main(argv: Sequence[str] | None = None) -> int:
    """Print the record; exit 2 on usage or an unreadable transcript."""
    parser = argparse.ArgumentParser(prog="tool_paths.py", description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("transcripts", type=Path, nargs="+")
    args = parser.parse_args(argv)
    try:
        record = opened(args.transcripts, args.root)
    except OSError as error:
        print(f"tool_paths: {error}", file=sys.stderr)
        return 2
    print(json.dumps(record))
    return 0


if __name__ == "__main__":
    sys.exit(main())
