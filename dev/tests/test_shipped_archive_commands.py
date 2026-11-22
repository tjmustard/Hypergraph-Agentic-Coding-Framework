"""Shipped skills and install templates name archive_specs.py's documented form (E3, mission R17b).

`archive_specs.py` documents two forms: `--miniprd PATH` and `--paths PATH... --label LABEL`. A bare
`archive_specs.py` only prints usage and exits 1, and the positional `<Feature_Name>` form is an
undocumented compatibility path. No file under `.agents/skills/` or `.agents/install-templates/`
may tell an agent to run either.

The rule, applied to code only (inline code spans and the lines of fenced code blocks), for each
occurrence of `archive_specs.py` in a code fragment:

- it is an **invocation with arguments** when the next whitespace-separated token in the same
  fragment starts with a letter, digit, `_`, `-`, `<`, `[`, `$`, a quote, `.`, `/` or `~` (an
  argument, not prose punctuation such as `—` or a `#` comment);
- it is a **bare invocation** when nothing follows it in the fragment and it is directly preceded
  by a path (`.../archive_specs.py`) or by a `python`/`python3` command word;
- anything else is a **mention** (a fragment that is only the name, or the name followed by prose
  punctuation), and is allowed.

An invocation passes only when its arguments hold `--miniprd`, or both `--paths` and `--label`.
Prose outside code (for example "use `archive_specs.py`" in a table) is a mention.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ROOTS = (".agents/skills", ".agents/install-templates")
NAME = "archive_specs.py"
CODE_SPAN = re.compile(r"`([^`\n]+)`")
FENCE = re.compile(r"^\s*(```|~~~)")
ARGUMENT_START = re.compile(r"""[A-Za-z0-9_\-<\[$"'./~]""")
COMMAND_BEFORE = re.compile(r"(/|\bpython3?\s+)$")


def code_fragments(text: str) -> list[tuple[int, str]]:
    """Return (line number, fragment) for each fenced code line and each inline code span."""
    fragments = []
    in_fence = False
    for number, line in enumerate(text.splitlines(), 1):
        if FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            fragments.append((number, line))
        else:
            fragments += [(number, span) for span in CODE_SPAN.findall(line)]
    return fragments


def bad_invocations(fragment: str) -> list[str]:
    """Return each archive_specs.py invocation in a code fragment that breaks the rule."""
    bad = []
    for match in re.finditer(re.escape(NAME), fragment):
        before = fragment[: match.start()]
        after = fragment[match.end() :]
        rest = after.split()
        if rest and ARGUMENT_START.match(rest[0]):
            arguments = rest
        elif not after.strip() and COMMAND_BEFORE.search(before):
            arguments = []
        else:
            continue  # a mention
        if "--miniprd" in arguments or ("--paths" in arguments and "--label" in arguments):
            continue
        bad.append(fragment.strip())
    return bad


def scan(text: str) -> list[str]:
    """Return "line: fragment" for every invocation in text that breaks the rule."""
    return [
        f"{number}: {bad}"
        for number, fragment in code_fragments(text)
        for bad in bad_invocations(fragment)
    ]


def shipped_files() -> list[Path]:
    """Return every file under the shipped skill and install-template roots."""
    return sorted(
        path for root in ROOTS for path in (REPO_ROOT / root).rglob("*") if path.is_file()
    )


def test_matcher_self_check() -> None:
    """The matcher flags the positional form and a bare code span; the documented ones pass."""
    assert scan("run `python3 .agents/scripts/archive_specs.py <Feature_Name>` now") != []
    assert scan("archive with `python3 .agents/scripts/archive_specs.py`.") != []
    assert scan("```bash\npython3 .agents/scripts/archive_specs.py MyFeature\n```\n") != []
    good = (
        "`python3 .agents/scripts/archive_specs.py --paths a.md b.md --label m`, "
        "`archive_specs.py --miniprd spec/compiled/MiniPRD_X.md`, use `archive_specs.py`.\n"
        "```\n├── archive/  # written only by archive_specs.py — BLOCKED\n"
        "Phase 1: /hyper-resolve → archive_specs.py\n```\n"
    )
    assert scan(good) == []


def test_shipped_files_use_the_documented_form() -> None:
    """No shipped skill or install template names a bare or positional archive_specs.py run."""
    files = shipped_files()
    assert any(path.name == "SKILL.md" for path in files)
    hits = []
    for path in files:
        text = path.read_bytes().decode("utf-8", "replace")
        if NAME in text:
            hits += [f"{path.relative_to(REPO_ROOT)}:{hit}" for hit in scan(text)]
    assert hits == [], "\n".join(hits)
