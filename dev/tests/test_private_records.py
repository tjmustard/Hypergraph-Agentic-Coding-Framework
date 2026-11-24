"""No file under dev/tests/ outside private_records/ reads a private record as a repo path.

The tests of private_records/conftest.py live beside it, in
private_records/test_private_records_conftest.py: the public export leaves that directory out.
"""

from __future__ import annotations

import re
import subprocess

from conftest import REPO_ROOT

TESTS = REPO_ROOT / "dev" / "tests"
PRIVATE = TESTS / "private_records"


def test_no_private_record_reader_outside_private_records() -> None:
    """No file under dev/tests/ outside private_records/ names a private record as a repo path."""
    # Built at runtime, so this file never names them itself.
    needles = [
        re.compile(r"REPO_ROOT\s*/\s*[\"']" + re.escape("lore_hyper" + "graph.yml")),
        re.compile("e4da" + "6ef"),
        re.compile("b01d" + "41b"),
        re.compile(re.escape("STORY-" + "0002-openrig-spike")),
    ]
    listed = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", "dev/tests"],
        cwd=REPO_ROOT,
        capture_output=True,
        check=True,
    ).stdout.decode("utf-8")
    hits = []
    for rel in filter(None, listed.split("\0")):
        path = REPO_ROOT / rel
        if PRIVATE in path.parents or not path.is_file():
            continue
        text = path.read_bytes().decode("utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), 1):
            if any(needle.search(line) for needle in needles):
                hits.append(f"{rel}:{lineno}")
    assert not hits, "private-record readers outside private_records/:\n" + "\n".join(hits)
