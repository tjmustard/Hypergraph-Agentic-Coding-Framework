#!/usr/bin/env python3
"""HACF progress report: what a project's software factory produced, per release.

Read-only, zero-token and offline: no model is called and no network is used. It writes
progress.json (schema hacf.progress-report/v1, see openrig/hacf/schemas/progress-report-v1.json),
progress.md and progress.html into --out-dir, and nothing anywhere else.

    progress-report.py --project <root> [--project <root>]... [--host] --out-dir <dir>
    progress-report.py --validate <progress.json>

Options for the sources git does not hold; a cell whose source is not given is "not recorded":
- --rig <root>=<rig>: the rig that worked on a project, for its telemetry and queue cells;
- --telemetry <dir>: the hacf.telemetry/v1 scorecards (*.json) of dev/telemetry/collect_metrics.py.
  A release's scorecard has its label vX.Y.Z, the project's rig and exactly the release window.
  Tokens and cost come only from it, each cost a list-price equivalent from its price table; this
  tool computes no price and assigns nothing to a seat or release by clock time. Each release's
  `telemetry_hint` holds the arguments that would make its scorecard;
- --queue rig (runs `rig queue list -A -a --json --limit 20000`, read-only) or
  --queue-export <file> with the same JSON: the questions, answers and decisions;
- --junit <X.Y.Z>=<file> (repeatable): pytest junit XML, for tests passing and collected;
- --gates-file <file> (repeatable): hyper_gates.py's gates.jsonl, for the Phase 3 gates.
--junit and --gates-file name no project, so each takes exactly one --project.

The queue tag rule: a row is a question when tagged `hacf:question` or `question-for-tj`, and an
answer when tagged `hacf:answer` or `tj-answer`; no other tag counts and no text is read. It
belongs to a project's rig by a `rig:<rig>` tag or an `@<rig>` source or destination, and to a
release by a `release:X.Y.Z` tag, else by its creation time in (since, until].

What it reads, per release tag vX.Y.Z of a project:
- git (plumbing and read verbs only): tags, commits, line counts, test functions, CHANGELOG.md,
  missions/release-X.Y.Z/PROGRESS.md with its slices' PROGRESS.md, and the lore: statuses from
  lore_hypergraph.yml at the tag, or from each lore file's Status line when the tag has no graph;
- on disk, only RedTeam_Report.md and SuperPRD.md under spec/archive/*/;
- with --collect-cases (opt-in), pytest's collection of an extracted copy of each tag, run by the
  given interpreter inside bwrap with the file system read-only and no network.

A value with no source is "not recorded" with its reason. Nothing is estimated.

Exit codes: 0 written; 1 a source could not be read (not a git work tree, an unreadable tag, out
dir or given file, a malformed junit or gates file), nothing written; 2 usage. A failed, truncated
or unparsable queue list is "not recorded", not an error.
"""

import argparse
import dataclasses
import datetime
import html
import io
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import xml.etree.ElementTree

try:
    import tomllib
except ImportError:  # Python 3.10: markers cannot be read
    tomllib = None

SCHEMA = "hacf.progress-report/v1"
NOT_RECORDED = "not recorded"
PROG = "progress-report"
OUTPUTS = ("progress.json", "progress.md", "progress.html")
GIT_TIMEOUT = 300
COLLECT_TIMEOUT = 600
QUEUE_TIMEOUT = 60
QUEUE_LIMIT = 20000
QUEUE_ARGV = ("rig", "queue", "list", "-A", "-a", "--json", "--limit", str(QUEUE_LIMIT))
# The only keys of a queue row that are kept; a row's text is dropped on read.
QUEUE_KEYS = (
    "qitemId",
    "tsCreated",
    "sourceSession",
    "destinationSession",
    "tags",
    "humanIntent",
    "state",
)
# The tag rule, with the legacy names the report alone still reads.
QUESTION_TAGS = ("hacf:question", "question-for-tj")
ANSWER_TAGS = ("hacf:answer", "tj-answer")
RELEASE_TAG_RE = re.compile(r"release:v?(\d+\.\d+\.\d+)", re.ASCII)
SCORECARD_SCHEMA = "hacf.telemetry/v1"
PRICE_BASIS = "list-price equivalent"
PHASE3_GATE = "novel_fixture_needed"
GATE_KEYS = ("gate", "slice", "reason", "at")
JUNIT_COUNTS = ("tests", "failures", "errors", "skipped")
TAG_RE = re.compile(r"v(\d+)\.(\d+)\.(\d+)", re.ASCII)

LOCKFILES = (
    "uv.lock",
    "poetry.lock",
    "Pipfile.lock",
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "Cargo.lock",
)
# A lockfile is excluded at any depth.
EXCLUDES = (*(f"**/{name}" for name in LOCKFILES), "**/vendor/**", "**/__pycache__/**")
TEST_EXCLUDES = (
    "tests/fixtures/**",
    "tests/candidate_outputs/**",
    "tests/data/**",
    "tests/**/data/**",
)
KINDS = ("unit", "integration", "e2e")
KIND_DIRS = {
    "unit": ("tests/unit",),
    "integration": ("tests/integration",),
    "e2e": ("tests/e2e", "tests/acceptance"),
}
KIND_MARKERS = {"unit": ("unit",), "integration": ("integration",), "e2e": ("e2e", "acceptance")}
TEST_DEF = r"^[[:space:]]*(async[[:space:]]+)?def test_"

# pytest's summary: "N tests collected", "N/M tests collected (K deselected)" or "no tests
# collected (K deselected)"
COLLECTED_RE = re.compile(
    r"(?:no|(\d+)(?:/(\d+))?) tests? collected(?: \((\d+) deselected\))?", re.ASCII
)
REPLAN_SUBJECT_RE = re.compile(r"^\S+ resolve: replan")
AUDIT_FAILED_RE = re.compile(r"^- \S+ audit_failed\b", re.MULTILINE)
# A builder's `executing` Outcomes line with this marker (contract text or JSON form, as in
# dev/telemetry/collect_metrics.py) was written by a pipeline that also records audit_failed
# events and HACF-Replan-Of trailers: all three began in HACF v0.6.4.
TRACKING_MARKER_RE = re.compile(
    r"""^- \S+ executing - .*\bescape_tracking"?\s*:\s*"?v1\b""", re.MULTILINE
)
SLICE_TABLE_RE = re.compile(
    r"<!-- BEGIN HACF GENERATED: slice-table -->(.*?)<!-- END HACF GENERATED: slice-table -->",
    re.DOTALL,
)
ARCHIVE_RE = re.compile(r"\d{8}_\d{6}_(.*)", re.ASCII)
FINDING_RE = re.compile(r"\bF\d{1,3}\b", re.ASCII)
LORE_RE = {
    "adrs": re.compile(r"lore/ADRs/(ADR-\d+)[^/]*\.md", re.ASCII),
    "stories": re.compile(r"lore/STORIES/(STORY-\d+)[^/]*\.md", re.ASCII),
}
LORE_STATUS_RE = re.compile(r"(?im)^\s*\**\s*status\s*\**\s*:\s*\**\s*([A-Za-z]+)")
ACCEPTED = ("ACCEPTED", "APPROVED")
# The lore graph, as .agents/scripts/hyper_lore.py writes it.
LORE_GRAPH = "lore_hypergraph.yml"
GRAPH_STATUSES = ("PROPOSED", "CANON", "REJECTED", "SUPERSEDED", "RETIRED")
GRAPH_LIVE = ("PROPOSED", "CANON")
GRAPH_TYPES = {"decision": "adrs", "story": "stories"}
GRAPH_FIELD_RE = re.compile(r"(?:- |  )(id|type|status): +(.*)")
GRAPH_SOURCE = f"lore graph ({LORE_GRAPH}) at the tag"
LINE_SOURCE = "each file's Status line at the tag (no lore graph)"

REASON_QUEUE = "no queue source given"
REASON_UNTAGGED = "no row carries the hacf:question/hacf:answer tags or their legacy names"
REASON_PHASE3 = "no Phase 3 gate record given"
REASON_TOKENS = "no telemetry source given"
REASON_TESTS = "no test-result record (HACF's pre-push hook records none)"
REASON_OPTION = "the queue records no chosen option (OpenRig 0.7.23)"
REASON_NO_PRICES = "the scorecard carries no usd (made without --prices)"
REASON_NO_TELEMETRY_DATA = "the scorecard holds no telemetry record in this release's window"
REASON_NO_RECORD = "no per-release record exists"
REASON_NO_SOURCE = "the template defines no source"
REASON_NO_TAG = "no release tag"
REASON_NO_COLLECT = "collection not run (--collect-cases)"
REASON_NO_PRD = "no SuperPRD.md beside the release's RedTeam_Report.md"
# {at}: "the tag" or "the latest tag"
COLLECTED_SOURCE = (
    "pytest --collect-only at {at}, the project's own addopts (marker deselection) in force"
)

# The template's sections: (key, column header), in order. Headings and headers are verbatim.
HEADLINE_COLUMNS = (
    ("releases", "Releases"),
    ("span_days", "Span (days)"),
    ("source_lines_net", "Source lines (net)"),
    ("test_lines_net", "Test lines (net)"),
    ("tests", "Tests (functions / collected cases)"),
    ("features_shipped", "Features shipped"),
    ("slices", "Slices"),
    ("human_decisions", "Human decisions"),
)
RELEASE_SECTIONS = (
    (
        "per_release",
        "2. Per release",
        (
            ("date", "Date"),
            ("days", "Days"),
            ("commits", "Commits"),
            ("source_lines", "Source +/−"),
            ("test_lines", "Test +/−"),
            ("unit", "Unit"),
            ("integration", "Integration"),
            ("e2e", "E2E/acceptance"),
            ("features", "Features"),
            ("slices", "Slices (replans)"),
            ("first_pass_audit_pct", "First-pass audit %"),
            ("adrs_stories", "ADRs / stories"),
            ("tokens_cost", "Tokens / cost"),
        ),
    ),
    (
        "quality",
        "3. Quality and safety (per release)",
        (
            ("tests_passing_collected", "Tests passing / collected"),
            ("lint_format_types", "Lint / format / types"),
            ("licence_check", "Licence check"),
            ("red_team_findings", "Red-team findings (resolved)"),
            ("audit_rework_rounds", "Audit rework rounds"),
            ("regressions_caught", "Regressions caught"),
            ("known_issues_carried", "Known issues carried"),
        ),
    ),
    (
        "human",
        "4. Human in the loop (per release)",
        (
            ("questions", "Questions to the human"),
            ("decisions", "Decisions (recommended / not)"),
            ("phase3_gates", "Phase 3 gates"),
            ("lore", "Lore approved / proposed"),
        ),
    ),
)
HEADLINE_TITLE = "1. Headline (whole project to date)"
# The legend under a section's tables (Markdown and HTML only); the headers stay the template's.
LEGEND_CASES = (
    "Collected cases are the cases the project's own pytest configuration selects; "
    '"(N deselected)" counts the cases it deselects.'
)
LEGENDS = {
    HEADLINE_TITLE: (
        "Human decisions: questions / answers, the queue rows tagged hacf:question / "
        "hacf:answer (or their legacy names question-for-tj / tj-answer). Not the same count as "
        "§4's Decisions.",
        LEGEND_CASES,
    ),
    RELEASE_SECTIONS[0][1]: (
        LEGEND_CASES,
        "ADRs / stories: approved (CANON) lore at each tag; lore approved after a tag appears "
        "with the next release.",
    ),
    RELEASE_SECTIONS[2][1]: (
        "Decisions (recommended / not): queue rows raised with the decision intent. Not the "
        "same count as §1's Human decisions.",
    ),
}
# Object values rendered as "first <separator> second"; a null half renders 'not recorded'.
PAIRS = {
    "date": ("first", "last", " → "),
    "source_lines": ("added", "removed", " / "),
    "test_lines": ("added", "removed", " / "),
    "tests": ("functions", "cases", " / "),
    "unit": ("functions", "cases", " / "),
    "integration": ("functions", "cases", " / "),
    "e2e": ("functions", "cases", " / "),
    "adrs_stories": ("adrs", "stories", " / "),
    "human_decisions": ("questions", "answers", " / "),
    "tests_passing_collected": ("passing", "collected", " / "),
    "lore": ("approved", "proposed", " / "),
}
# Object values rendered as "first (second)".
BRACKETED = {"slices": ("slices", "replans"), "red_team_findings": ("findings", "resolved")}
# Totals that show the latest tag's value instead of a sum: test counts and the lore totals.
LATEST = ("unit", "integration", "e2e", "adrs_stories", "tests_passing_collected")


class ReportError(Exception):
    """A source could not be read: exit 1, nothing written."""


class UsageError(Exception):
    """A bad argument: exit 2, nothing written."""


def value(content: object, source: str) -> dict:
    """Return a recorded metric."""
    return {"value": content, "source": source}


def missing(reason: str) -> dict:
    """Return a 'not recorded' metric."""
    return {"value": None, "status": NOT_RECORDED, "reason": reason}


def utc(epoch: float) -> str:
    """Format an epoch time as UTC YYYY-MM-DDTHH:MM:SSZ."""
    moment = datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def days_between(since: int, until: int) -> float:
    """Return the days from since to until, one decimal."""
    return round((until - since) / 86400, 1)


@dataclasses.dataclass
class Options:
    """The options that apply to every project."""

    source_paths: list
    test_paths: list
    excludes: list
    kinds: dict
    collect_python: object = None  # str when --collect-cases is given


@dataclasses.dataclass
class Sources:
    """The sources given beside git, read once for every project. None: not given."""

    scorecards: object = None  # [(file name, scorecard)]
    queue: object = None  # the kept keys of every queue row
    queue_reason: str = REASON_QUEUE  # why there is no queue
    junit: dict = dataclasses.field(default_factory=dict)  # version -> [(file name, counts)]
    gates: object = None  # [(gate, slice)]


# ------------------------------------------------------------------------------------------ git


class Git:
    """Read-only git calls on one project."""

    def __init__(self, root: pathlib.Path) -> None:
        """Bind the calls to `root` with an environment no caller variable can redirect."""
        self.root = root
        dropped = (
            "GIT_DIR",
            "GIT_WORK_TREE",
            "GIT_COMMON_DIR",
            "GIT_INDEX_FILE",
            "GIT_OBJECT_DIRECTORY",
            "GIT_NAMESPACE",
        )
        self.env = {k: v for k, v in os.environ.items() if k not in dropped}
        self.env.update(GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0", LC_ALL="C")

    def fail(self, reason: str) -> ReportError:
        """Return the error for this project, naming it by basename only."""
        return ReportError(f"{self.root.name}: {reason}")

    def raw(self, verb: str, *args: str, ok: tuple = (0,), stdin: bytes = b"") -> tuple:
        """Run one git verb; return (exit code, stdout bytes). Other exit codes are errors."""
        argv = ["git", "--no-optional-locks", "-C", str(self.root), verb, *args]
        try:
            done = subprocess.run(
                argv, env=self.env, input=stdin, capture_output=True, timeout=GIT_TIMEOUT
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise self.fail(f"git {verb} could not run ({type(error).__name__})") from None
        if done.returncode not in ok:
            raise self.fail(f"git {verb} failed (exit {done.returncode})")
        return done.returncode, done.stdout

    def text(self, verb: str, *args: str, **kwargs: object) -> str:
        """Run one git verb and return its stdout as text."""
        return self.raw(verb, *args, **kwargs)[1].decode("utf-8", "replace")

    def commit_time(self, commit: str) -> int:
        """Return a commit's committer time as epoch seconds."""
        out = self.text("log", "-1", "--no-show-signature", "--format=%ct", commit)
        return int(out.strip())

    def entries(self, commit: str, *paths: str) -> list:
        """Return (type, object id, path) of every file at `commit` under the literal paths."""
        out = self.text("ls-tree", "-r", "-z", "--full-tree", commit, "--", *paths)
        found = []
        for record in out.split("\0"):
            if record:
                meta, path = record.split("\t", 1)
                _, kind, oid = meta.split()
                found.append((kind, oid, path))
        return found

    def file(self, commit: str, path: str) -> object:
        """Return the text of one file at `commit`, or None when it is not there."""
        for kind, oid, found in self.entries(commit, path):
            if kind == "blob" and found == path:
                return self.text("cat-file", "blob", oid)
        return None

    def has_files(self, commit: str, paths: list) -> bool:
        """Tell whether `commit` holds any file under the pathspecs."""
        if not any(ch in path for path in paths for ch in ":*?["):
            return bool(self.entries(commit, *paths))
        # ls-tree takes no pathspec magic or globs: diff against the empty tree instead.
        return bool(self.text("diff", "--name-only", self.empty_tree(), commit, "--", *paths))

    def empty_tree(self) -> str:
        """Return the empty tree's object id (nothing is written)."""
        return self.text("hash-object", "-t", "tree", "--stdin").strip()


# -------------------------------------------------------------------------------------- sources


def check_work_tree(git: Git) -> str:
    """Require `git.root` to be the top of a work tree with a commit; return HEAD."""
    code, out = git.raw("rev-parse", "--show-toplevel", ok=(0, 128))
    if code != 0:
        raise git.fail("not a git work tree")
    top = out.decode("utf-8", "replace").rstrip("\n")
    if os.path.realpath(top) != os.path.realpath(git.root):
        raise git.fail("not the top of a git work tree")
    code, out = git.raw("rev-parse", "--verify", "-q", "HEAD^{commit}", ok=(0, 1, 128))
    if code != 0:
        raise git.fail("HEAD names no readable commit")
    return out.decode().strip()


def release_tags(git: Git) -> list:
    """Return (version, tag, commit) of every vX.Y.Z tag, ordered by version."""
    found = []
    for name in git.text("for-each-ref", "--format=%(refname:strip=2)", "refs/tags").split("\n"):
        match = TAG_RE.fullmatch(name)
        if match:
            found.append((tuple(int(part) for part in match.groups()), name))
    releases = []
    for _, tag in sorted(found):
        code, out = git.raw(
            "rev-parse", "--verify", "-q", f"refs/tags/{tag}^{{commit}}", ok=(0, 1, 128)
        )
        if code != 0:
            raise git.fail(f"tag {tag} names no readable commit")
        releases.append((tag[1:], tag, out.decode().strip()))
    return releases


def line_counts(git: Git, base: str, commit: str, paths: list, excludes: list) -> dict:
    """Return the added and removed lines between two commits under the paths."""
    if not (git.has_files(base, paths) or git.has_files(commit, paths)):
        return missing(f"no file under {', '.join(paths)}")
    specs = [*paths, *(f":(exclude,glob){glob}" for glob in excludes)]
    out = git.text(
        "diff",
        "--numstat",
        "--no-ext-diff",
        "--no-textconv",
        "--find-renames",
        "--diff-algorithm=myers",
        base,
        commit,
        "--",
        *specs,
    )
    counts = {"added": 0, "removed": 0, "binary_files": 0}
    for line in out.split("\n"):
        if not line:
            continue
        added, removed, _ = line.split("\t", 2)
        if added == "-":
            counts["binary_files"] += 1
        else:
            counts["added"] += int(added)
            counts["removed"] += int(removed)
    return value(counts, f"git diff --numstat between release tags under {', '.join(paths)}")


def count_functions(git: Git, commit: str, dirs: list, excludes: list) -> object:
    """Count `def test_` lines in the test files under dirs; None when no dir is at the commit."""
    cleaned = [d.strip("/") for d in dirs]
    present = [d for d in cleaned if d == "." or git.has_files(commit, [d])]
    if not present:
        return None
    specs = []
    for directory in present:
        prefix = "" if directory == "." else f"{directory}/"
        specs += [f":(glob){prefix}**/test_*.py", f":(glob){prefix}**/*_test.py"]
    specs += [f":(exclude,glob){glob}" for glob in excludes]
    _, out = git.raw("grep", "-h", "-E", "--no-color", "-e", TEST_DEF, commit, "--", *specs,
                     ok=(0, 1))  # fmt: skip
    return len(out.splitlines())


def changelog_section(text: object, version: str) -> object:
    """Return the lines of the version's CHANGELOG section, or None when there is none."""
    if text is None:
        return None
    heading = re.compile(rf"## \[v?{re.escape(version)}\]")
    section = None
    for line in text.splitlines():
        if section is not None:
            if line.startswith("## ["):
                break
            section.append(line)
        elif heading.match(line):
            section = []
    return section


def bullets(section: list, heading: str) -> object:
    """Count the top-level bullets under `### <heading>`; None when the heading is absent."""
    count = None
    inside = False
    for line in section:
        if re.match(r"#{2,3}\s", line):
            inside = line.startswith("### ") and line[4:].strip().lower() == heading.lower()
            if inside and count is None:
                count = 0
        elif inside and line.startswith("- "):
            count += 1
    return count


def slice_table(text: object) -> object:
    """Return (id, slice, hacf_status) rows of the generated slice table, or None without one."""
    match = SLICE_TABLE_RE.search(text or "")
    if not match:
        return None
    rows = []
    for line in match.group(1).splitlines():
        line = line.strip()
        if not line.startswith("|") or set(line) <= set("|-: "):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) >= 3 and cells[0] != "id":
            rows.append((cells[0], cells[1], cells[2]))
    return rows


def count_replans(git: Git, span: str) -> int:
    """Count the commits in `span` that record a replan, each once."""
    fmt = "%s%x1f%(trailers:key=HACF-Replan-Of,separator=%x1d)%x1e"
    out = git.text("log", "--no-show-signature", f"--format={fmt}", span, "--")
    count = 0
    for chunk in out.split("\x1e"):
        subject, _, trailer = chunk.strip("\n").partition("\x1f")
        if trailer.strip() or REPLAN_SUBJECT_RE.match(subject):
            count += 1
    return count


def mission_metrics(git: Git, version: str, commit: str, span: str) -> dict:
    """Return the slice, replan, first-pass and rework metrics, with the pooled audit counts."""
    base = f"missions/release-{version}"
    rows = slice_table(git.file(commit, f"{base}/PROGRESS.md"))
    if rows is None:
        reason = f"no slice table in {base}/PROGRESS.md at v{version}"
        return {
            "slices": missing(reason),
            "first_pass_audit_pct": missing(reason),
            "audit_rework_rounds": missing(reason),
            "audited": None,
            "pooled": (0, 0),
            "ids": None,
        }
    replans = count_replans(git, span)
    audited = [name for _, name, status in rows if status == "audited"]
    read = clean = failed = marked = 0
    for name in audited:
        progress = git.file(commit, f"{base}/slices/{name}/PROGRESS.md")
        if progress is None:
            continue
        lines = len(AUDIT_FAILED_RE.findall(progress))
        read += 1
        failed += lines
        clean += lines == 0
        marked += TRACKING_MARKER_RE.search(progress) is not None
    source = f"audit_failed lines in {base}/slices/*/PROGRESS.md of audited slices"
    counted = {"slices": len(rows), "replans": replans}
    # Without the records, a count of none is not a zero: the release predates its source.
    untracked = None
    if not marked:
        untracked = (
            f"v{version}'s slice records predate HACF's audit and replan records "
            "(no escape_tracking: v1 marker; HACF v0.6.4)"
        )
    elif marked < read:
        untracked = (
            f"only {marked} of {read} audited slices of v{version} carry the "
            "escape_tracking: v1 marker"
        )
    if untracked:
        counted.update(replans=None, replans_reason=untracked)
    return {
        "slices": value(
            counted,
            f"slice table in {base}/PROGRESS.md; git: HACF-Replan-Of trailers and "
            "'resolve: replan' subjects",
        ),
        "first_pass_audit_pct": missing(untracked)
        if untracked
        else value(round(100 * clean / read, 1), source),
        "audit_rework_rounds": missing(untracked) if untracked else value(failed, source),
        "audited": len(audited),
        "pooled": (clean, read),
        "ids": frozenset(slice_id for slice_id, _, _ in rows),
    }


def release_features(section: object, version: str, audited: object) -> dict:
    """Return the features: CHANGELOG '### Added' bullets, else the audited slices."""
    added = None if section is None else bullets(section, "Added")
    if added is not None:
        return value(added, f"CHANGELOG.md [{version}] '### Added' bullets at the tag")
    if audited is not None:
        return value(audited, f"fallback: audited slices in missions/release-{version}/PROGRESS.md")
    if section is not None:
        return missing(
            f"no Added heading in CHANGELOG.md [{version}] and no slice table for v{version}"
        )
    return missing(f"no CHANGELOG.md section and no slice table for v{version}")


def known_issues(section: object, version: str) -> dict:
    """Return the '### Known issues' bullets of the version's CHANGELOG section."""
    if section is None:
        return missing(f"no CHANGELOG.md section for v{version}")
    count = bullets(section, "Known issues")
    if count is None:
        return missing(f"no Known issues heading in CHANGELOG.md [{version}]")
    return value(count, f"CHANGELOG.md [{version}] '### Known issues' bullets at the tag")


def read_text(git: Git, path: pathlib.Path) -> object:
    """Return an on-disk record's text, or None when it is not a plain file."""
    try:
        if path.is_symlink() or not path.is_file():
            return None
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        raise git.fail(f"cannot read spec/archive/{path.parent.name}/{path.name}") from None


def red_team(git: Git, version: str) -> dict:
    """Count the unique F-ids of the release's archived red-team reports, and the resolved."""
    archive = git.root / "spec" / "archive"
    wanted = re.compile(rf"(?<![0-9])v?{version.replace('.', '_')}$")
    try:
        names = sorted(os.listdir(archive))
    except (FileNotFoundError, NotADirectoryError):
        names = []
    except OSError:
        raise git.fail("cannot list spec/archive") from None
    findings = resolved = 0
    reports = with_prd = 0
    for name in names:
        match = ARCHIVE_RE.fullmatch(name)
        folder = archive / name
        if not (match and wanted.search(match.group(1))) or folder.is_symlink():
            continue
        report = read_text(git, folder / "RedTeam_Report.md")
        if report is None:
            continue
        ids = set(FINDING_RE.findall(report))
        reports += 1
        findings += len(ids)
        prd = read_text(git, folder / "SuperPRD.md")
        if prd is not None:
            with_prd += 1
            resolved += len(ids & set(FINDING_RE.findall(prd)))
    if not reports:
        return missing(f"no spec/archive folder with a RedTeam_Report.md for v{version}")
    content = {"findings": findings, "resolved": resolved if with_prd else None}
    if not with_prd:
        content["resolved_reason"] = REASON_NO_PRD
    return value(
        content,
        "unique F-ids in the release's spec/archive RedTeam_Report.md; resolved: those also in "
        "the folder's SuperPRD.md",
    )


def graph_nodes(text: str) -> object:
    """Return {id: (kind, status)} of a lore graph, or None when it is not in its written form.

    The form is hyper_lore.py's block YAML: under the top-level `nodes:` key, each item starts
    `- id: <ID>` and has its own `  type:` and `  status:` lines. Deeper lines are not read.
    """
    found: dict = {}
    items: list = []
    inside = opened = False
    for line in text.split("\n"):
        if not inside:
            if line == "nodes: []" and not opened:
                opened = True
            elif line == "nodes:" and not opened:
                inside = opened = True
            elif line.startswith("nodes:"):
                return None
            continue
        if line.startswith("- "):
            if not line.startswith("- id: "):
                return None
            items.append({})
        elif line.startswith(" ") or not line:
            if not items and line:
                return None
        else:  # the next top-level key ends the list
            if not re.match(r"[A-Za-z_]\w*:", line):
                return None
            inside = False
            continue
        field = GRAPH_FIELD_RE.fullmatch(line)
        if field:
            items[-1].setdefault(field.group(1), field.group(2).strip())
    for item in items:
        status = item.get("status")
        if not (item.get("id") and item.get("type") and status in GRAPH_STATUSES):
            return None
        if item["id"] in found:
            return None
        found[item["id"]] = (GRAPH_TYPES.get(item["type"], item["type"]), status)
    return found if opened else None


def lore_files(git: Git, commit: str, statuses: dict) -> object:
    """Return {id: (kind, status)} from the lore files at `commit`, or None without lore/."""
    entries = git.entries(commit, "lore")
    if not entries:
        return None
    found = {}
    for kind_of_object, oid, path in entries:
        for kind, pattern in LORE_RE.items():
            match = pattern.fullmatch(path)
            if not match or kind_of_object != "blob":
                continue
            if oid not in statuses:
                head = "\n".join(git.text("cat-file", "blob", oid).split("\n")[:60])
                status = LORE_STATUS_RE.search(head)
                statuses[oid] = status.group(1).upper() if status else ""
            found[match.group(1)] = (kind, statuses[oid])
    return found


@dataclasses.dataclass
class Lore:
    """The lore of one tag: its ids, the approved and countable ones, or why they are unknown."""

    ids: frozenset = frozenset()
    approved: frozenset = frozenset()
    proposable: frozenset = frozenset()  # the ids that count as proposed when they are new
    kinds: dict = dataclasses.field(default_factory=dict)
    source: str = ""
    reason: str = ""


def lore_at(git: Git, commit: str, version: str, statuses: dict) -> Lore:
    """Read one tag's lore: the graph when the tag has one, else each file's Status line."""
    graph = git.file(commit, LORE_GRAPH)
    if graph is not None:
        nodes = graph_nodes(graph)
        if nodes is None:
            reason = f"lore graph at v{version} is not in hyper_lore.py's written form"
            return Lore(reason=reason)
        approved = {key for key, (_, status) in nodes.items() if status == "CANON"}
        proposable = {key for key, (_, status) in nodes.items() if status in GRAPH_LIVE}
        source = GRAPH_SOURCE
    else:
        nodes = lore_files(git, commit, statuses)
        if nodes is None:
            # nothing to count: an empty baseline for the next release
            return Lore(reason=f"no lore/ at v{version}")
        if not any(status for _, status in nodes.values()):
            return Lore(reason=f"no lore graph and no Status line at v{version}")
        approved = {key for key, (_, status) in nodes.items() if status in ACCEPTED}
        proposable = set(nodes)
        source = LINE_SOURCE
    return Lore(
        ids=frozenset(nodes),
        approved=frozenset(approved),
        proposable=frozenset(proposable),
        kinds={key: kind for key, (kind, _) in nodes.items()},
        source=source,
    )


def lore_metrics(now: Lore, before: Lore) -> tuple:
    """Return the 'ADRs / stories' and 'Lore approved / proposed' metrics."""
    if now.reason:
        return missing(now.reason), missing(now.reason)
    totals = {kind: sum(1 for key in now.approved if now.kinds[key] == kind) for kind in LORE_RE}
    adrs_stories = value(totals, now.source)
    # an unread previous tag is no baseline; a tag without any lore is an empty one
    if before.reason and not before.reason.startswith("no lore/ "):
        return adrs_stories, missing(f"no baseline: {before.reason}")
    change = {
        "approved": len(now.approved - before.approved),
        "proposed": len(now.proposable - before.ids),
    }
    return adrs_stories, value(change, f"{now.source}; against the previous release")


# ------------------------------------------------------------------------------------ telemetry


def read_input(path: str, option: str) -> str:
    """Return a given file's text; one that cannot be read is an error, named by basename."""
    try:
        return pathlib.Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        raise ReportError(f"{os.path.basename(path)}: the {option} file cannot be read") from None


def read_scorecards(folder: str) -> list:
    """Return (file name, scorecard) of every hacf.telemetry/v1 JSON file directly in folder."""
    base = pathlib.Path(folder)
    try:
        names = sorted(name for name in os.listdir(base) if name.endswith(".json"))
    except OSError:
        raise ReportError(f"{base.name}: the --telemetry dir cannot be listed") from None
    found = []
    for name in names:
        if not (base / name).is_file():
            continue
        try:
            card = json.loads((base / name).read_bytes())
        except OSError:
            raise ReportError(f"{name}: a --telemetry file cannot be read") from None
        except (ValueError, RecursionError):
            continue
        if isinstance(card, dict) and card.get("schema") == SCORECARD_SCHEMA:
            found.append((name, card))
    return found


def instant(text: object) -> object:
    """Return an ISO-8601 time as an aware datetime, or None."""
    if not isinstance(text, str):
        return None
    try:
        moment = datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=datetime.timezone.utc)
    return moment


def same_window(card: dict, window: dict) -> bool:
    """Tell whether a scorecard's window equals the release window to the second."""
    theirs = card.get("window")
    if not isinstance(theirs, dict):
        return False
    for key in ("since", "until"):
        moment = instant(theirs.get(key))
        if moment is None or moment.replace(microsecond=0) != instant(window[key]):
            return False
    return True


def money(amounts: list) -> object:
    """Sum dollar amounts; None when any is None."""
    if any(amount is None for amount in amounts):
        return None
    return round(sum(amounts), 6)


def count_of(item: object) -> int:
    """Require a non-negative integer."""
    if isinstance(item, bool) or not isinstance(item, int) or item < 0:
        raise ValueError("not a count")
    return item


def scorecard_value(card: dict) -> dict:
    """Return the 'tokens_cost' value of one scorecard.

    Raises:
        ValueError, KeyError, TypeError, AttributeError: when it is not in the scorecard's form.
    """
    by_seat: dict = {}
    total = 0
    amounts: list = []
    for seat in card["seats"]:
        tokens, costs = 0, []
        for entry in seat["slices"]:
            tokens += count_of(entry["total"])
            usd = entry.get("usd")
            if not (usd is None or (isinstance(usd, (int, float)) and not isinstance(usd, bool))):
                raise ValueError("not an amount")
            costs.append(usd)
        by_seat[str(seat["seat"])] = {"tokens": tokens, "usd": money(costs)}
        total += tokens
        amounts += costs
    unattributed = None
    if "samples" in card:
        counts = [entry["context_without_session"] for entry in card["samples"].values()]
        if all(count is not None for count in counts):
            unattributed = sum(count_of(count) for count in counts)
    content = {
        "tokens": total,
        "usd": money(amounts),
        "price_basis": PRICE_BASIS,
        "by_seat": by_seat,
        "unattributed_samples": unattributed,
    }
    if content["usd"] is None:
        reasons = [str(why) for why in card.get("unknowns", []) if "usd unknown" in str(why)]
        content["usd_reasons"] = reasons or [REASON_NO_PRICES]
    return content


def holds_no_data(card: dict, tokens: int) -> bool:
    """Return True when a scorecard has no token, no context turn and no context sample.

    Raises:
        ValueError, KeyError, TypeError, AttributeError: when it is not in the scorecard's form.
    """
    return (
        tokens == 0
        and all(count_of(entry["turns"]) == 0 for entry in card.get("context", {}).values())
        and all(count_of(entry["context"]) == 0 for entry in card.get("samples", {}).values())
    )


def tokens_metric(sources: Sources, rig: object, name: str, version: str, window: dict) -> dict:
    """Return the release's tokens and cost, from its own scorecard only."""
    if sources.scorecards is None:
        return missing(REASON_TOKENS)
    if rig is None:
        return missing(f"no --rig for {name}")
    labelled = [
        (file, card)
        for file, card in sources.scorecards
        if card.get("label") == f"v{version}" and card.get("rig") == rig
    ]
    matching = [(file, card) for file, card in labelled if same_window(card, window)]
    if not matching:
        if labelled:
            return missing("scorecard window differs")
        return missing(f"no scorecard v{version} for rig {rig}")
    if len(matching) > 1:
        return missing(f"{len(matching)} scorecards for v{version}")
    file, card = matching[0]
    try:
        for why in card.get("unknowns", []):
            if str(why).startswith("token totals unknown"):
                return missing(str(why))
        content = scorecard_value(card)
        if holds_no_data(card, content["tokens"]):
            return missing(REASON_NO_TELEMETRY_DATA)
    except (ValueError, KeyError, TypeError, AttributeError):
        raise ReportError(f"{file}: not a readable {SCORECARD_SCHEMA} scorecard") from None
    return value(content, f"{SCORECARD_SCHEMA} scorecard {file}")


def telemetry_hint(rig: object, version: str, window: dict) -> str:
    """Return the collect_metrics.py arguments that would make a release's scorecard."""
    return (
        f"--rig {rig or '<rig>'} --label v{version} --since {window['since']} "
        f"--until {window['until']} --repo <project root> --prices <prices.yml>"
    )


# ---------------------------------------------------------------------------------------- queue


def kept_rows(text: object) -> tuple:
    """Return (rows with only QUEUE_KEYS, "") or (None, why the list cannot be used)."""
    try:
        rows = json.loads(text)
    except (ValueError, RecursionError):
        rows = None
    if not (isinstance(rows, list) and all(isinstance(row, dict) for row in rows)):
        return None, "rig queue list failed: bad json"
    if len(rows) >= QUEUE_LIMIT:
        return None, f"queue list truncated at {QUEUE_LIMIT}"
    return [{key: row.get(key) for key in QUEUE_KEYS} for row in rows], ""


def rig_queue() -> tuple:
    """Run the read-only queue list; return what kept_rows returns."""
    try:
        done = subprocess.run(list(QUEUE_ARGV), capture_output=True, timeout=QUEUE_TIMEOUT)
    except subprocess.TimeoutExpired:
        return None, "rig queue list failed: timeout"
    except OSError:
        return None, "rig queue list failed: 127"  # the shell's code for a command not run
    if done.returncode != 0:
        return None, f"rig queue list failed: {done.returncode}"
    return kept_rows(done.stdout)


def queue_counts(sources: Sources, rig: object, name: str, releases: list) -> tuple:
    """Count a project's rows per release.

    Args:
        sources: the given sources.
        rig: the project's rig, or None.
        name: the project's name.
        releases: (version, since, until) of each release, times in epoch seconds.

    Returns:
        ({version: counts}, rows after the latest tag, "") or (None, None, why not).
    """
    if sources.queue is None:
        return None, None, sources.queue_reason
    if rig is None:
        return None, None, f"no --rig for {name}"
    counts = {version: {"questions": 0, "answers": 0, "decisions": 0} for version, _, _ in releases}
    latest = tuple(int(part) for part in releases[-1][0].split(".")) if releases else ()
    after = tagged = 0
    for row in sources.queue:
        tags = row["tags"] if isinstance(row["tags"], list) else []
        tags = [tag for tag in tags if isinstance(tag, str)]
        sessions = (row["sourceSession"], row["destinationSession"])
        if f"rig:{rig}" not in tags and not any(str(s).endswith(f"@{rig}") for s in sessions):
            continue
        question = any(tag in QUESTION_TAGS for tag in tags)
        answer = any(tag in ANSWER_TAGS for tag in tags)
        tagged += question or answer
        named = [m.group(1) for m in map(RELEASE_TAG_RE.fullmatch, tags) if m]
        version = next((found for found in named if found in counts), None)
        if version is None:
            if releases and any(tuple(int(p) for p in n.split(".")) > latest for n in named):
                after += 1  # it names a release that has no tag yet
                continue
            moment = instant(row["tsCreated"])
            if moment is None or not releases:
                continue
            when = moment.timestamp()
            if when > releases[-1][2]:
                after += 1
                continue
            version = next((v for v, since, until in releases if since < when <= until), None)
            if version is None:
                continue
        counts[version]["questions"] += question
        counts[version]["answers"] += answer
        counts[version]["decisions"] += row["humanIntent"] == "decision"
    if not tagged:
        return None, None, REASON_UNTAGGED
    return counts, after, ""


# ------------------------------------------------------------------------------ junit and gates


def read_junit(path: str) -> dict:
    """Return the passing and collected tests of one pytest junit XML file."""
    name = os.path.basename(path)
    try:
        root = xml.etree.ElementTree.parse(path).getroot()
        suites = [root] if root.tag == "testsuite" else root.findall("testsuite")
        if root.tag not in ("testsuite", "testsuites") or not suites:
            raise ValueError("no test suite")
        collected = passing = 0
        for suite in suites:
            tests, *others = (count_of(int(suite.attrib[key])) for key in JUNIT_COUNTS)
            collected += tests
            passing += count_of(tests - sum(others))
    except OSError:
        raise ReportError(f"{name}: the --junit file cannot be read") from None
    except (xml.etree.ElementTree.ParseError, KeyError, ValueError, LookupError):
        raise ReportError(f"{name}: not a pytest junit XML file with its counts") from None
    return {"passing": passing, "collected": collected}


def tests_metric(sources: Sources, version: str) -> dict:
    """Return a release's tests passing and collected, its junit files added together."""
    files = sources.junit.get(version)
    if not files:
        return missing(REASON_TESTS)
    names = ", ".join(name for name, _ in files)
    return value(add_values([counts for _, counts in files]), f"junit {names}")


def read_gates(path: str) -> list:
    """Return (gate, slice) of every line of one gates.jsonl."""
    name = os.path.basename(path)
    found = []
    for number, line in enumerate(read_input(path, "--gates-file").split("\n"), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except (ValueError, RecursionError):
            record = None
        if not (
            isinstance(record, dict) and all(isinstance(record.get(key), str) for key in GATE_KEYS)
        ):
            raise ReportError(f"{name}: line {number} is not a gate record")
        found.append((record["gate"], record["slice"]))
    return found


def gates_metric(sources: Sources, ids: object, version: str) -> dict:
    """Count the release's Phase 3 gates: those naming a slice of its slice table."""
    if sources.gates is None:
        return missing(REASON_PHASE3)
    if ids is None:
        base = f"missions/release-{version}/PROGRESS.md"
        return missing(f"no slice table in {base} at v{version}")
    count = sum(1 for gate, name in sources.gates if gate == PHASE3_GATE and name in ids)
    return value(count, f"{PHASE3_GATE} lines of the gates files naming a slice of the table")


# ----------------------------------------------------------------------------------- collection


def parse_collected(output: str) -> object:
    """Return (selected, deselected) of pytest's last collection summary line, or None."""
    counts = None
    for line in output.splitlines():
        match = COLLECTED_RE.match(line)
        if not match:
            continue
        selected, total, deselected = (int(part or 0) for part in match.groups())
        if match.group(3) is None and match.group(2) is not None:
            deselected = total - selected
        counts = (selected, deselected)
    return counts


def extract(archive: bytes, target: str) -> None:
    """Extract a git archive into target, refusing members that could leave it."""
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as bundle:
        members = bundle.getmembers()
        links = {member.name.rstrip("/") for member in members if member.issym()}
        for member in members:
            parts = pathlib.PurePosixPath(member.name).parts
            through_link = any("/".join(parts[:i]) in links for i in range(1, len(parts)))
            if member.name.startswith("/") or ".." in parts or through_link:
                raise ValueError("unsafe archive member")
            if not (member.isfile() or member.isdir() or member.issym()):
                raise ValueError("unsafe archive member")
            if member.issym():
                end = os.path.normpath(os.path.join(os.path.dirname(member.name), member.linkname))
                if os.path.isabs(member.linkname) or end == ".." or end.startswith("../"):
                    raise ValueError("unsafe archive member")
        if hasattr(tarfile, "data_filter"):
            bundle.extractall(target, filter="data")
        else:
            bundle.extractall(target)


def sandbox_collect(tmp: str, interpreter: str, args: list, tag: str) -> tuple:
    """Run one `pytest --collect-only` in the sandbox; return (counts or None, reason).

    The counts are parse_collected's (selected, deselected).
    """
    argv = [
        "bwrap",
        "--ro-bind", "/", "/",
        "--tmpfs", "/tmp",
        "--bind", tmp, tmp,  # after the tmpfs, so a temp dir under /tmp stays visible
        "--dev", "/dev",
        "--proc", "/proc",
        "--unshare-net",
        "--die-with-parent",
        "--chdir", tmp,
        "env", f"PYTHONPATH={tmp}/src:{tmp}", "PYTHONDONTWRITEBYTECODE=1",
        interpreter, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", *args,
    ]  # fmt: skip
    try:
        done = subprocess.run(argv, capture_output=True, timeout=COLLECT_TIMEOUT)
    except subprocess.TimeoutExpired:
        return None, f"collection failed at {tag}: timeout"
    except OSError:
        return None, f"collection failed at {tag}: bwrap did not start"
    if done.returncode not in (0, 5):
        return None, f"collection failed at {tag}: exit {done.returncode}"
    counts = parse_collected(done.stdout.decode("utf-8", "replace"))
    if counts is None:
        return None, f"collection failed at {tag}: no summary line"
    return counts, ""


def declared_markers(git: Git, commit: str) -> tuple:
    """Return (marker names declared in the tag's pyproject.toml, why there are none)."""
    if tomllib is None:
        return set(), "markers need Python 3.11 (tomllib)"
    text = git.file(commit, "pyproject.toml")
    if text is None:
        return set(), "no pyproject.toml at the tag"
    try:
        listed = tomllib.loads(text)["tool"]["pytest"]["ini_options"]["markers"]
        names = {re.split(r"[:(]", str(item), maxsplit=1)[0].strip() for item in listed}
    except (ValueError, KeyError, TypeError):
        return set(), "no readable pytest markers in pyproject.toml at the tag"
    return names, ""


def collect_cases(git: Git, tag: str, commit: str, options: Options) -> dict:
    """Return {name: (counts, why not)} per kind and for 'all', plus 'markers' per kind."""
    names = (*KINDS, "all")
    result = {name: (None, REASON_NO_COLLECT) for name in names}
    result["markers"] = {kind: ({}, "collection not run") for kind in KINDS}
    if options.collect_python is None:
        return result

    def give_up(reason: str) -> dict:
        result.update({name: (None, reason) for name in names})
        return result

    if shutil.which("bwrap") is None:
        return give_up("--collect-cases needs bwrap")
    wanted = {kind: options.kinds[kind] for kind in KINDS}
    wanted["all"] = options.test_paths
    dirs = {
        name: [d for d in paths if git.has_files(commit, [d.strip("/")])]
        for name, paths in wanted.items()
    }
    declared, why = declared_markers(git, commit)
    archive = git.raw("archive", "--format=tar", commit)[1]
    tmp = tempfile.mkdtemp()
    try:
        if os.path.realpath(tmp).startswith(os.path.realpath(git.root) + os.sep):
            return give_up("the temp dir is inside the project")
        try:
            extract(archive, tmp)
        except (ValueError, OSError, tarfile.TarError):
            return give_up(f"collection failed at {tag}: the archive could not be extracted")
        for name in names:
            if dirs[name]:
                result[name] = sandbox_collect(tmp, options.collect_python, dirs[name], tag)
            else:
                result[name] = (None, f"no test dir at {tag}")
        for kind in KINDS:
            counts, notes = {}, []
            for marker in KIND_MARKERS[kind]:
                if marker not in declared:
                    notes.append(why or f"marker {marker} not declared")
                elif dirs["all"]:
                    found, failure = sandbox_collect(
                        tmp, options.collect_python, ["-m", marker, *dirs["all"]], tag
                    )
                    if found is None:
                        notes.append(f"marker {marker}: {failure}")
                    else:
                        counts[marker] = found[0]
            result["markers"][kind] = (counts, "; ".join(dict.fromkeys(notes)))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return result


def counts_value(functions: object, collected: tuple, no_functions: str) -> dict:
    """Return '{functions, cases}', with 'deselected' beside known cases, else each reason."""
    counts, why = collected
    content = {"functions": functions, "cases": None if counts is None else counts[0]}
    if functions is None:
        content["functions_reason"] = no_functions
    if counts is None:
        content["cases_reason"] = why
    else:
        content["deselected"] = counts[1]
    return content


def kind_metric(kind: str, functions: object, collected: dict, dirs: list) -> dict:
    """Return one kind's '{functions, cases, by_marker}' metric."""
    counts, why = collected[kind]
    absent = f"no {' or '.join(dirs)} at the tag"
    if functions is None and counts is None:
        return missing(absent)
    by_marker, marker_note = collected["markers"][kind]
    source = f"git grep for test functions under {', '.join(dirs)} at the tag"
    source += "; cases: " + (why if counts is None else COLLECTED_SOURCE.format(at="the tag"))
    if marker_note:
        source += f"; by_marker: {marker_note}"
    content = counts_value(functions, collected[kind], absent)
    return value({**content, "by_marker": by_marker}, source)


# ------------------------------------------------------------------------------------ the model


def release_record(git: Git, release: tuple, previous: object, since: int, state: dict) -> tuple:
    """Return one release object, the pooled audit counts and its slice ids."""
    options = state["options"]
    version, tag, commit = release
    until = git.commit_time(commit)
    base = previous[2] if previous else git.empty_tree()
    span = f"{previous[2]}..{commit}" if previous else commit
    excludes = [*EXCLUDES, *options.excludes]
    test_excludes = [*excludes, *TEST_EXCLUDES]
    section = changelog_section(git.file(commit, "CHANGELOG.md"), version)
    mission = mission_metrics(git, version, commit, span)
    lore_now = lore_at(git, commit, version, state["statuses"])
    adrs_stories, lore_change = lore_metrics(lore_now, state["lore"])
    state["lore"] = lore_now
    sources, rig = state["sources"], state["rig"]
    window = {"since": utc(since), "until": utc(until)}
    collected = collect_cases(git, tag, commit, options)
    kinds = {
        kind: kind_metric(
            kind,
            count_functions(git, commit, options.kinds[kind], test_excludes),
            collected,
            options.kinds[kind],
        )
        for kind in KINDS
    }
    state["tests"] = (
        count_functions(git, commit, options.test_paths, test_excludes),
        collected["all"],
    )
    commits = int(git.text("rev-list", "--count", span, "--").strip())
    record = {
        "version": version,
        "tag": tag,
        "commit": commit,
        "window": window,
        "telemetry_hint": telemetry_hint(rig, version, window),
        "per_release": {
            "date": value(utc(until)[:10], "committer date of the tag's commit (UTC)"),
            "days": value(
                days_between(since, until),
                "committer times: previous release (or root commit) to the tag",
            ),
            "commits": value(commits, "git rev-list --count between release tags"),
            "source_lines": line_counts(git, base, commit, options.source_paths, excludes),
            "test_lines": line_counts(git, base, commit, options.test_paths, test_excludes),
            **kinds,
            "features": release_features(section, version, mission["audited"]),
            "slices": mission["slices"],
            "first_pass_audit_pct": mission["first_pass_audit_pct"],
            "adrs_stories": adrs_stories,
            "tokens_cost": tokens_metric(sources, rig, git.root.name, version, window),
        },
        "quality": {
            "tests_passing_collected": tests_metric(sources, version),
            "lint_format_types": missing(REASON_NO_RECORD),
            "licence_check": missing(REASON_NO_RECORD),
            "red_team_findings": red_team(git, version),
            "audit_rework_rounds": mission["audit_rework_rounds"],
            "regressions_caught": missing(REASON_NO_SOURCE),
            "known_issues_carried": known_issues(section, version),
        },
        "human": {
            "questions": missing(REASON_QUEUE),  # project_record fills the queue cells
            "decisions": missing(REASON_QUEUE),
            "phase3_gates": gates_metric(sources, mission["ids"], version),
            "lore": lore_change,
        },
    }
    return record, mission["pooled"], mission["ids"]


def add_tokens(values: list) -> dict:
    """Add the releases' tokens_cost values; a null in any one leaves that part null."""
    seats: dict = {}
    for item in values:
        for seat, numbers in item["by_seat"].items():
            seats.setdefault(seat, []).append(numbers)
    unattributed = [item["unattributed_samples"] for item in values]
    total = {
        "tokens": sum(item["tokens"] for item in values),
        "usd": money([item["usd"] for item in values]),
        "price_basis": PRICE_BASIS,
        "by_seat": {
            seat: {
                "tokens": sum(n["tokens"] for n in numbers),
                "usd": money([n["usd"] for n in numbers]),
            }
            for seat, numbers in seats.items()
        },
        "unattributed_samples": add_values(unattributed),
    }
    if total["usd"] is None:
        reasons = [why for item in values for why in item.get("usd_reasons", [])]
        total["usd_reasons"] = list(dict.fromkeys(reasons))
    return total


def add_values(values: list) -> object:
    """Sum numbers, or objects key by key; a null in any one leaves that part null.

    A null part keeps the reasons the objects give for it (`<part>_reason`), and the deselected
    cases are summed only beside a known case count.
    """
    if isinstance(values[0], dict):
        parts = [k for k in values[0] if k != "deselected" and not k.endswith("_reason")]
        total = {key: add_values([item[key] for item in values]) for key in parts}
        for key in parts:
            reasons = [item[f"{key}_reason"] for item in values if f"{key}_reason" in item]
            if total[key] is None and reasons:
                total[f"{key}_reason"] = "; ".join(dict.fromkeys(reasons))
        if total.get("cases") is not None:
            total["deselected"] = sum(item.get("deselected", 0) for item in values)
        return total
    if any(item is None for item in values):
        return None
    return sum(values)


def summed(metrics: list, labels: list, source: str) -> dict:
    """Sum one column; 'not recorded', naming the labels, when any entry is."""
    if not metrics:
        return missing(REASON_NO_TAG)
    gaps = [label for label, metric in zip(labels, metrics) if metric["value"] is None]
    if gaps:
        return missing(f"{NOT_RECORDED} for {', '.join(gaps)}")
    contents = [metric["value"] for metric in metrics]
    first = contents[0]
    adder = add_tokens if isinstance(first, dict) and "price_basis" in first else add_values
    return value(adder(contents), source)


def total_rows(releases: list, pooled: list, span: dict, queue_reason: str) -> dict:
    """Return the Total row of every per-release section."""
    totals = {}
    tags = [release["tag"] for release in releases]
    for section, _, columns in RELEASE_SECTIONS:
        row = totals[section] = {}
        for key, _ in columns:
            metrics = [release[section][key] for release in releases]
            reasons = {metric.get("reason") for metric in metrics}
            if not metrics:
                row[key] = missing(REASON_NO_TAG)
            elif key == "date":
                row[key] = value(
                    {"first": metrics[0]["value"], "last": metrics[-1]["value"]},
                    "the first and latest release dates",
                )
            elif key == "days":
                row[key] = span
            elif key in LATEST:
                row[key] = metrics[-1]
            elif len(reasons) == 1 and reasons <= {
                REASON_TOKENS,
                REASON_TESTS,
                REASON_NO_RECORD,
                REASON_NO_SOURCE,
                REASON_PHASE3,
                queue_reason,
            }:
                row[key] = metrics[0]  # a column with no source keeps its reason
            elif key == "first_pass_audit_pct":
                gaps = [t for t, m in zip(tags, metrics) if m["value"] is None]
                if gaps:
                    row[key] = missing(f"{NOT_RECORDED} for {', '.join(gaps)}")
                else:
                    clean, read = (sum(part) for part in zip(*pooled))
                    row[key] = value(
                        round(100 * clean / read, 1), "pooled over every release's audited slices"
                    )
            else:
                row[key] = summed(metrics, tags, "sum over the releases")
                if key == "slices" and row[key]["value"] is not None:
                    gaps = [t for t, m in zip(tags, metrics) if m["value"]["replans"] is None]
                    if gaps:  # the Total names the releases, not each release's own reason
                        why = f"{NOT_RECORDED} for {', '.join(gaps)}"
                        row[key]["value"]["replans_reason"] = why
    return totals


def human_headline(counts: object, reason: str) -> dict:
    """Return the headline's questions and answers over every release."""
    if counts is None:
        return missing(reason)
    if not counts:
        return missing(REASON_NO_TAG)
    total = {key: sum(c[key] for c in counts.values()) for key in ("questions", "answers")}
    return value(total, "queue rows by the hacf:question/hacf:answer tag rule, over the releases")


def headline_row(releases: list, span: dict, tests: object, human: dict) -> dict:
    """Return the §1 headline of one project."""
    tags = [release["tag"] for release in releases]

    def column(section_key: str, part: object = None) -> dict:
        metrics = [release["per_release"][section_key] for release in releases]
        total = summed(metrics, tags, "sum over the releases")
        if total["value"] is None or part is None:
            return total
        return value(part(total["value"]), total["source"])

    tests_metric = missing(REASON_NO_TAG)
    if tests is not None:
        functions, collected = tests
        counts, why = collected
        absent = "no test dir at the latest tag"
        if functions is None and counts is None:
            tests_metric = missing(absent)
        else:
            source = "git grep for test functions at the latest tag; cases: "
            at_latest = COLLECTED_SOURCE.format(at="the latest tag")
            source += why if counts is None else at_latest
            tests_metric = value(counts_value(functions, collected, absent), source)
    return {
        "releases": value(len(releases), "git tags vX.Y.Z"),
        "span_days": span,
        "source_lines_net": column("source_lines", lambda v: v["added"] - v["removed"]),
        "test_lines_net": column("test_lines", lambda v: v["added"] - v["removed"]),
        "tests": tests_metric,
        "features_shipped": column("features"),
        "slices": column("slices", lambda v: v["slices"]),
        "human_decisions": human,
    }


def project_record(git: Git, rig: object, head: str, options: Options, sources: Sources) -> dict:
    """Return one project object of the report."""
    tags = release_tags(git)
    state = {
        "options": options,
        "sources": sources,
        "rig": rig,
        "statuses": {},
        "lore": Lore(reason="no lore/ before the first release"),
        "tests": None,
    }
    releases, pooled, windows, ids = [], [], [], set()
    span = missing(REASON_NO_TAG)
    if tags:
        commits = [commit for _, _, commit in tags]

        def earliest_root(*of: str) -> int:
            roots = git.text("rev-list", "--max-parents=0", *of, "--").split()
            return min(git.commit_time(root) for root in roots)

        since = earliest_root(commits[0])
        previous = None
        for release in tags:
            record, counts, slices = release_record(git, release, previous, since, state)
            releases.append(record)
            pooled.append(counts)
            ids |= slices or set()
            until = git.commit_time(release[2])
            windows.append((release[0], since, until))
            since = until
            previous = release
        span = value(
            days_between(earliest_root(*commits), git.commit_time(commits[-1])),
            "committer times: the earliest root commit to the latest tag",
        )
    counted, after, why = queue_counts(sources, rig, git.root.name, windows)
    for release in releases:
        human = release["human"]
        if counted is None:
            human["questions"] = missing(why)
            human["decisions"] = missing(why)
        else:
            numbers = counted[release["version"]]
            source = "queue rows of the release, by tags and creation time"
            human["questions"] = value(numbers["questions"], source)
            human["decisions"] = value(
                {
                    "decisions": numbers["decisions"],
                    "recommended": None,
                    "not_recommended": None,
                },
                "queue rows of the release with humanIntent decision",
            )
    record = {
        "name": git.root.name,
        "rig": rig,
        "head": head,
        "headline": headline_row(releases, span, state["tests"], human_headline(counted, why)),
        "releases": releases,
        "totals": total_rows(releases, pooled, span, why or REASON_QUEUE),
    }
    if after is not None:
        record["after_latest_tag"] = after
    if sources.gates is not None:
        record["gates_unmapped"] = sum(
            1 for gate, name in sources.gates if gate == PHASE3_GATE and name not in ids
        )
    return record


def host_record(projects: list) -> dict:
    """Return the host roll-up of the projects' headlines."""
    names = [project["name"] for project in projects]
    headline = {}
    for key, _ in HEADLINE_COLUMNS:
        metrics = [project["headline"][key] for project in projects]
        gaps = [name for name, metric in zip(names, metrics) if metric["value"] is None]
        if gaps:
            headline[key] = missing(f"{NOT_RECORDED} for {', '.join(gaps)}")
        elif key == "span_days":
            headline[key] = value(max(m["value"] for m in metrics), "the largest project span")
        else:
            headline[key] = value(
                add_values([m["value"] for m in metrics]), "sum over the projects"
            )
    return {"projects": names, "headline": headline}


# ------------------------------------------------------------------------------------ rendering


def scalar(item: object) -> str:
    """Render one number or string."""
    if item is None:
        return NOT_RECORDED
    if isinstance(item, bool) or isinstance(item, str):
        return str(item)
    if isinstance(item, int):
        return f"{item:,}"
    return f"{item:,.1f}"


def cell(key: str, metric: dict) -> str:
    """Render one metric as its column's cell text."""
    content = metric["value"]
    if content is None:
        return NOT_RECORDED
    if not isinstance(content, dict):
        return scalar(content)
    if key == "tokens_cost":
        usd = content.get("usd")
        amount = NOT_RECORDED if usd is None else f"${usd:,.2f}"
        return f"{scalar(content.get('tokens'))} / {amount} {content.get('price_basis')}"
    if key == "decisions":
        halves = " / ".join(
            scalar(content.get(name)) for name in ("recommended", "not_recommended")
        )
        return f"{scalar(content.get('decisions'))} ({halves})"
    if key in BRACKETED:
        first, second = BRACKETED[key]
        return f"{scalar(content.get(first))} ({scalar(content.get(second))})"
    if key in PAIRS:
        first, second, separator = PAIRS[key]
        text = f"{scalar(content.get(first))}{separator}{scalar(content.get(second))}"
        deselected = content.get("deselected")
        return f"{text} ({scalar(deselected)} deselected)" if deselected else text
    return ", ".join(f"{name}: {scalar(item)}" for name, item in content.items())


def tables(model: dict) -> tuple:
    """Return the report's tables and the (cell name, metric) pairs they hold.

    A table is (section title, project name or None, header, rows); a row is a list of
    (text, is not recorded), its first cell the row label.
    """
    seen = []

    def row(label: str, name: str, columns: tuple, metrics: dict) -> list:
        cells = [(label, False)]
        for key, header in columns:
            seen.append((f"{name} {header}", key, metrics[key]))
            cells.append((cell(key, metrics[key]), metrics[key]["value"] is None))
        return cells

    headline_rows = [
        row(p["name"], f"{p['name']} Headline", HEADLINE_COLUMNS, p["headline"])
        for p in model["projects"]
    ]
    if model["host"] is not None:
        headline_rows.append(row("Host", "Host", HEADLINE_COLUMNS, model["host"]["headline"]))
    header = ["Project", *(title for _, title in HEADLINE_COLUMNS)]
    found = [(HEADLINE_TITLE, [(None, header, headline_rows)])]
    for section, title, columns in RELEASE_SECTIONS:
        header = ["Version", *(name for _, name in columns)]
        per_project = []
        for p in model["projects"]:
            rows = [
                row(r["tag"], f"{p['name']} {r['tag']}", columns, r[section]) for r in p["releases"]
            ]
            rows.append(row("**Total**", f"{p['name']} Total", columns, p["totals"][section]))
            per_project.append((p["name"], header, rows))
        found.append((title, per_project))
    return found, seen


def unknown_reasons(key: str, content: object) -> list:
    """Return the reasons of a recorded value's unknown parts, those its cell shows."""
    if not isinstance(content, dict):
        return []
    if key == "decisions":
        return [REASON_OPTION] if content.get("recommended") is None else []
    if key == "tokens_cost":
        return content.get("usd_reasons", [])
    shown = BRACKETED.get(key) or PAIRS.get(key, ())[:2]
    named = [f"{part}_reason" for part in shown if content.get(part) is None]
    return [content[name] for name in named if name in content]


def appendix(seen: list) -> list:
    """Return the 'Not recorded' and 'Sources' lists as (heading, lines)."""
    reasons, sources = {}, {}
    for name, key, metric in seen:
        content = metric["value"]
        if content is None:
            reasons.setdefault(metric["reason"], []).append(name)
            continue
        sources[metric["source"]] = None
        # a value with an unknown part lists why, once per reason
        for why in dict.fromkeys(unknown_reasons(key, content)):
            reasons.setdefault(why, []).append(name)
    return [
        ("Not recorded", [(reason, "; ".join(names)) for reason, names in reasons.items()]),
        ("Sources", [(source, "") for source in sources]),
    ]


def section_notes(title: str, model: dict, name: object) -> list:
    """Return the notes printed under one project's table of a section."""
    if title != RELEASE_SECTIONS[0][1]:
        return []
    notes = []
    for project in model["projects"]:
        if project["name"] != name:
            continue
        for release in project["releases"]:
            content = release["per_release"]["tokens_cost"]["value"]
            count = content.get("unattributed_samples") if content else None
            if count:
                notes.append(
                    f"{release['tag']}: {count} token samples without a session: "
                    "their tokens are not recorded"
                )
    return list(dict.fromkeys(notes))


def generated_line(model: dict) -> str:
    """Return the report's 'Generated ...' line."""
    return f"Generated {model['generated_at']} by progress-report.py (read-only, zero-token)."


def render_markdown(model: dict) -> str:
    """Render the report as Markdown from the JSON object alone."""

    def line(cells: list) -> str:
        return "| " + " | ".join(c.replace("|", "\\|") for c in cells) + " |"

    out = ["# HACF progress report", "", generated_line(model)]
    found, seen = tables(model)
    for title, per_project in found:
        out += ["", f"## {title}"]
        for name, header, rows in per_project:
            if name is not None:
                out += ["", f"### {name}"]
            out += ["", line(header), "|" + "---|" * len(header)]
            out += [line([text for text, _ in cells]) for cells in rows]
            notes = section_notes(title, model, name)
            if notes:
                out += ["", *(f"- {note}" for note in notes)]
        if title in LEGENDS:
            out += ["", *(f"- {line}" for line in LEGENDS[title])]
    for title, lines in appendix(seen):
        out += ["", f"## {title}", ""]
        out += [f"- {first}" + (f" (cells: {rest})" if rest else "") for first, rest in lines]
    return "\n".join(out).rstrip("\n") + "\n"


STYLE = """:root{color-scheme:light dark}
body{font:14px system-ui,sans-serif;margin:16px;background:#fff;color:#1a1a1a}
div.t{overflow-x:auto}
table{border-collapse:collapse}
th,td{border:1px solid #ccc;padding:4px 8px;text-align:left}
tr.total td{font-weight:600}
td.nr{color:#767676}
@media (prefers-color-scheme:dark){body{background:#16181c;color:#e6e6e6}
th,td{border-color:#444}td.nr{color:#999}}"""


def render_html(model: dict) -> str:
    """Render the report as one self-contained HTML page from the JSON object alone."""
    esc = html.escape
    out = [
        "<!doctype html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        "<title>HACF progress report</title>",
        f"<style>{STYLE}</style>",
        "</head>",
        "<body>",
        "<h1>HACF progress report</h1>",
        f"<p>{esc(generated_line(model))}</p>",
    ]
    found, seen = tables(model)
    for title, per_project in found:
        out.append(f"<h2>{esc(title)}</h2>")
        for name, header, rows in per_project:
            if name is not None:
                out.append(f"<h3>{esc(name)}</h3>")
            out.append('<div class="t"><table>')
            out.append("<tr>" + "".join(f"<th>{esc(h)}</th>" for h in header) + "</tr>")
            for cells in rows:
                total = cells[0][0] == "**Total**"
                parts = []
                for index, (text, absent) in enumerate(cells):
                    if index == 0 and total:
                        parts.append("<td><strong>Total</strong></td>")
                    else:
                        mark = ' class="nr"' if absent else ""
                        parts.append(f"<td{mark}>{esc(text)}</td>")
                opening = '<tr class="total">' if total else "<tr>"
                out.append(opening + "".join(parts) + "</tr>")
            out.append("</table></div>")
            out += [f"<p>{esc(note)}</p>" for note in section_notes(title, model, name)]
        out += [f"<p>{esc(line)}</p>" for line in LEGENDS.get(title, ())]
    for title, lines in appendix(seen):
        out.append(f"<h2>{esc(title)}</h2>")
        out.append("<ul>")
        for first, rest in lines:
            text = first + (f" (cells: {rest})" if rest else "")
            out.append(f"<li>{esc(text)}</li>")
        out.append("</ul>")
    out += ["</body>", "</html>"]
    return "\n".join(out) + "\n"


# ------------------------------------------------------------------------------------- validate


def check_keys(found: object, keys: tuple, path: str, problems: list, optional: tuple = ()) -> bool:
    """Require an object with exactly these keys, and no other but the optional ones."""
    if not isinstance(found, dict):
        problems.append(f"{path}: not an object")
        return False
    for key in keys:
        if key not in found:
            problems.append(f"{path}.{key}: missing")
    for key in found:
        if key not in keys and key not in optional:
            problems.append(f"{path}.{key}: unknown key")
    return set(found) - set(optional) == set(keys)


def check_metric(metric: object, path: str, problems: list) -> None:
    """Require one of the two metric shapes."""
    if isinstance(metric, dict):
        if set(metric) == {"value", "source"}:
            content = metric["value"]
            if (
                isinstance(content, (int, float, str, dict))
                and not isinstance(content, bool)
                and isinstance(metric["source"], str)
                and metric["source"]
            ):
                return
        elif set(metric) == {"value", "status", "reason"}:
            if (
                metric["value"] is None
                and metric["status"] == NOT_RECORDED
                and isinstance(metric["reason"], str)
                and metric["reason"]
            ):
                return
    problems.append(f"{path}: not a metric (value with source, or null with status and reason)")


def check_section(found: object, columns: tuple, path: str, problems: list) -> None:
    """Require a section object holding one metric per column."""
    keys = tuple(key for key, _ in columns)
    check_keys(found, keys, path, problems)
    if isinstance(found, dict):
        for key in keys:
            if key in found:
                check_metric(found[key], f"{path}.{key}", problems)


def check_sections(found: object, path: str, problems: list) -> None:
    """Require the three per-release sections inside an object."""
    for section, _, columns in RELEASE_SECTIONS:
        if isinstance(found, dict) and section in found:
            check_section(found[section], columns, f"{path}.{section}", problems)


def problems_of(model: object) -> list:
    """Return one '<json path>: <reason>' per problem of a report object."""
    problems: list = []
    if not isinstance(model, dict):
        return ["$: not an object"]
    check_keys(model, ("schema", "generated_at", "projects", "host"), "$", problems)
    if model.get("schema") != SCHEMA:
        problems.append(f"$.schema: not {SCHEMA}")
    stamp = model.get("generated_at")
    if not (isinstance(stamp, str) and re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", stamp)):
        problems.append("$.generated_at: not YYYY-MM-DDTHH:MM:SSZ")
    projects = model.get("projects")
    if not isinstance(projects, list):
        problems.append("$.projects: not a list")
        projects = []
    section_names = tuple(section for section, _, _ in RELEASE_SECTIONS)
    for i, project in enumerate(projects):
        at = f"$.projects[{i}]"
        keys = ("name", "rig", "head", "headline", "releases", "totals")
        counts = ("after_latest_tag", "gates_unmapped")
        check_keys(project, keys, at, problems, counts)
        if not isinstance(project, dict):
            continue
        for key in counts:
            number = project.get(key, 0)
            if isinstance(number, bool) or not isinstance(number, int):
                problems.append(f"{at}.{key}: not an integer")
        for key in ("name", "head"):
            if key in project and not isinstance(project[key], str):
                problems.append(f"{at}.{key}: not a string")
        if not isinstance(project.get("rig"), (str, type(None))):
            problems.append(f"{at}.rig: not a string or null")
        if "headline" in project:
            check_section(project["headline"], HEADLINE_COLUMNS, f"{at}.headline", problems)
        if "totals" in project:
            check_keys(project["totals"], section_names, f"{at}.totals", problems)
            check_sections(project["totals"], f"{at}.totals", problems)
        releases = project.get("releases", [])
        if not isinstance(releases, list):
            problems.append(f"{at}.releases: not a list")
            releases = []
        for j, release in enumerate(releases):
            where = f"{at}.releases[{j}]"
            keys = ("version", "tag", "commit", "window", *section_names)
            check_keys(release, keys, where, problems, ("telemetry_hint",))
            if not isinstance(release, dict):
                continue
            if not isinstance(release.get("telemetry_hint", ""), str):
                problems.append(f"{where}.telemetry_hint: not a string")
            for key in ("version", "tag", "commit"):
                if key in release and not isinstance(release[key], str):
                    problems.append(f"{where}.{key}: not a string")
            if "window" in release:
                check_keys(release["window"], ("since", "until"), f"{where}.window", problems)
            check_sections(release, where, problems)
    host = model.get("host")
    if host is not None:
        check_keys(host, ("projects", "headline"), "$.host", problems)
        if isinstance(host, dict):
            names = host.get("projects", [])
            if not (isinstance(names, list) and all(isinstance(n, str) for n in names)):
                problems.append("$.host.projects: not a list of names")
            if "headline" in host:
                check_section(host["headline"], HEADLINE_COLUMNS, "$.host.headline", problems)
    return problems


def validate(path: str) -> int:
    """Check a progress.json; print 'ok' or one line per problem."""
    try:
        model = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    except OSError:
        print("$: the file cannot be read")
        return 1
    except ValueError:
        print("$: not JSON")
        return 1
    problems = problems_of(model)
    print("\n".join(problems) if problems else "ok")
    return 1 if problems else 0


# ------------------------------------------------------------------------------------------ CLI


def parser() -> argparse.ArgumentParser:
    """Return the command line parser."""
    p = argparse.ArgumentParser(
        prog=f"{PROG}.py",
        allow_abbrev=False,
        description="Write HACF's read-only, zero-token progress report.",
    )
    p.add_argument("--project", action="append", default=[], metavar="ROOT")
    p.add_argument("--host", action="store_true", help="add the roll-up over the projects")
    p.add_argument("--out-dir", metavar="DIR", help="an existing dir outside every project")
    p.add_argument("--validate", metavar="PROGRESS_JSON", help="check a progress.json only")
    p.add_argument("--rig", action="append", default=[], metavar="ROOT=RIG")
    p.add_argument("--source-path", action="append", metavar="PATHSPEC", help="default: src")
    p.add_argument("--test-path", action="append", metavar="PATHSPEC", help="default: tests")
    p.add_argument("--exclude", action="append", default=[], metavar="GLOB")
    p.add_argument("--kind", action="append", default=[], metavar="NAME=DIR[,DIR...]")
    p.add_argument("--collect-cases", action="store_true", help="count collected cases (bwrap)")
    p.add_argument("--collect-python", metavar="INTERPRETER")
    p.add_argument("--telemetry", metavar="DIR", help="hacf.telemetry/v1 scorecards")
    queue = p.add_mutually_exclusive_group()
    queue.add_argument("--queue", choices=("rig",), help="run `rig queue list`, read-only")
    queue.add_argument("--queue-export", metavar="FILE", help="that command's JSON, saved")
    p.add_argument("--junit", action="append", default=[], metavar="X.Y.Z=FILE")
    p.add_argument("--gates-file", action="append", default=[], metavar="GATES_JSONL")
    return p


def relative(option: str, paths: list) -> list:
    """Refuse an empty or absolute path: outputs must never name a local path."""
    for path in paths:
        if not path or path.startswith(("/", "~")) or ".." in path.split("/"):
            raise UsageError(f"{option} takes paths relative to the project root")
    return paths


def options_of(args: argparse.Namespace) -> Options:
    """Check the options that apply to every project."""
    if args.collect_cases != bool(args.collect_python):
        raise UsageError("--collect-cases and --collect-python go together")
    kinds = {kind: list(dirs) for kind, dirs in KIND_DIRS.items()}
    for item in args.kind:
        name, found, dirs = item.partition("=")
        if not found or name not in KINDS:
            raise UsageError("--kind takes unit|integration|e2e=DIR[,DIR...]")
        kinds[name] = relative("--kind", dirs.split(","))
    interpreter = args.collect_python
    if interpreter and os.sep in interpreter:
        interpreter = os.path.abspath(interpreter)
    return Options(
        source_paths=relative("--source-path", args.source_path or ["src"]),
        test_paths=relative("--test-path", args.test_path or ["tests"]),
        excludes=relative("--exclude", args.exclude),
        kinds=kinds,
        collect_python=interpreter,
    )


def rigs_of(args: argparse.Namespace, roots: list) -> dict:
    """Return {project real path: rig name} from --rig."""
    known = {os.path.realpath(root) for root in roots}
    rigs = {}
    for item in args.rig:
        root, found, rig = item.rpartition("=")
        if not found or not rig:
            raise UsageError("--rig takes ROOT=RIG")
        if os.path.realpath(root) not in known:
            raise UsageError("--rig names a root that is not a --project")
        rigs[os.path.realpath(root)] = rig
    return rigs


def junit_of(args: argparse.Namespace) -> list:
    """Check --junit and --gates-file; return --junit's (version, file) pairs."""
    for flag, given in (("--junit", args.junit), ("--gates-file", args.gates_file)):
        if given and len(args.project) != 1:
            raise UsageError(f"{flag} needs exactly one --project")
    pairs = []
    for item in args.junit:
        version, found, path = item.partition("=")
        if not (found and path and re.fullmatch(r"\d+\.\d+\.\d+", version, re.ASCII)):
            raise UsageError("--junit takes X.Y.Z=FILE")
        pairs.append((version, path))
    return pairs


def read_sources(args: argparse.Namespace, junit: list) -> Sources:
    """Read every given source, before any project."""
    sources = Sources()
    if args.telemetry is not None:
        sources.scorecards = read_scorecards(args.telemetry)
    for version, path in junit:
        entry = (os.path.basename(path), read_junit(path))
        sources.junit.setdefault(version, []).append(entry)
    if args.gates_file:
        sources.gates = [gate for path in args.gates_file for gate in read_gates(path)]
    if args.queue_export is not None:
        text = read_input(args.queue_export, "--queue-export")
        sources.queue, sources.queue_reason = kept_rows(text)
    elif args.queue:
        sources.queue, sources.queue_reason = rig_queue()
    return sources


def generated_at() -> str:
    """Return the report time; SOURCE_DATE_EPOCH fixes it."""
    fixed = os.environ.get("SOURCE_DATE_EPOCH")
    if not fixed:
        return utc(datetime.datetime.now(datetime.timezone.utc).timestamp())
    try:
        return utc(int(fixed))
    except (ValueError, OverflowError, OSError):
        raise UsageError("SOURCE_DATE_EPOCH is not an epoch time") from None


def write_outputs(out_dir: pathlib.Path, texts: dict) -> None:
    """Write each file under a temp name in out_dir, then rename all into place."""
    staged = []
    try:
        for name, text in texts.items():
            temp = out_dir / f".{name}.{os.getpid()}.tmp"
            with open(temp, "x", encoding="utf-8", newline="\n") as handle:
                staged.append((temp, out_dir / name))
                handle.write(text)
        for temp, final in staged:
            os.replace(temp, final)
    finally:
        for temp, _ in staged:
            temp.unlink(missing_ok=True)


def report(args: argparse.Namespace) -> None:
    """Build the model of every project and write the three outputs."""
    junit = junit_of(args)
    options = options_of(args)
    roots = [pathlib.Path(os.path.abspath(root)) for root in args.project]
    rigs = rigs_of(args, roots)
    out_dir = pathlib.Path(args.out_dir)
    out_real = os.path.realpath(out_dir)
    for root in roots:
        real = os.path.realpath(root)
        if os.path.commonpath([real, out_real]) == real:
            raise UsageError("--out-dir must be outside every --project")
    stamp = generated_at()
    if not (os.path.isdir(out_dir) and os.access(out_dir, os.R_OK | os.W_OK | os.X_OK)):
        raise ReportError(f"{roots[0].name}: --out-dir is not a readable, writable directory")
    sources = read_sources(args, junit)
    projects = []
    for root in roots:
        git = Git(root)
        if not root.is_dir():
            raise git.fail("not a git work tree")
        head = check_work_tree(git)
        rig = rigs.get(os.path.realpath(root))
        projects.append(project_record(git, rig, head, options, sources))
    model = {
        "schema": SCHEMA,
        "generated_at": stamp,
        "projects": projects,
        "host": host_record(projects) if args.host else None,
    }
    texts = dict(
        zip(
            OUTPUTS,
            (
                json.dumps(model, indent=2, ensure_ascii=False) + "\n",
                render_markdown(model),
                render_html(model),
            ),
        )
    )
    try:
        write_outputs(out_dir, texts)
    except OSError:
        raise ReportError(f"{roots[0].name}: the outputs could not be written") from None


def main(argv: object = None) -> int:
    """Run the command line; return the exit code."""
    cli = parser()
    args = cli.parse_args(argv)
    if args.validate is not None:
        if args.project or args.out_dir:
            cli.error("--validate takes no --project or --out-dir")
        return validate(args.validate)
    if not args.project or not args.out_dir:
        cli.error("--project and --out-dir are required")
    try:
        report(args)
    except UsageError as error:
        cli.error(str(error))
    except ReportError as error:
        print(f"{PROG}: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.dont_write_bytecode = True
    sys.exit(main())
