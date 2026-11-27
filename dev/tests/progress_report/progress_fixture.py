"""Builds the progress report's fixture project and reads cells back from its three outputs.

The project is a throwaway git repository under `tmp_path` with fixed commit dates, a
machine-independent identity and the tags v0.1.0 and v0.2.0. `build(tmp_path, omit=...)` leaves out
one source at a time. Every number the tests assert follows from the files written here. Lore ids
are made at runtime, so no shipped-lore literal is written into this file.
"""

from __future__ import annotations

import hashlib
import html
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
TOOL = REPO_ROOT / "openrig" / "hacf" / "bin" / "progress-report.py"
SCHEMA_FILE = REPO_ROOT / "openrig" / "hacf" / "schemas" / "progress-report-v1.json"
EPOCH = "1767744000"  # 2026-01-07T00:00:00Z
BEGIN = "<!-- BEGIN HACF GENERATED: slice-table -->"
END = "<!-- END HACF GENERATED: slice-table -->"

# a builder's `executing` Outcomes line: the marker that makes a slice's records tracked
MARKER = "- t executing - base: abc escape_tracking: v1\n"

SECTIONS = {
    "headline": "## 1. Headline (whole project to date)",
    "per_release": "## 2. Per release",
    "quality": "## 3. Quality and safety (per release)",
    "human": "## 4. Human in the loop (per release)",
}


def load_tool():
    """Import progress-report.py in-process (its name is not an identifier)."""
    spec = importlib.util.spec_from_file_location("progress_report_tool", TOOL)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def adr(number: int) -> str:
    """Return a fixture ADR id, made at runtime."""
    return f"ADR-{number:04d}"


def story(number: int) -> str:
    """Return a fixture story id, made at runtime."""
    return f"STORY-{number:04d}"


class Repo:
    """A fixture git repository with deterministic commits."""

    def __init__(self, root: Path) -> None:
        """Initialise the repository on branch main."""
        self.root = root
        root.mkdir(parents=True)
        self.git("init", "-q", "-b", "main")

    def env(self, when: str = "2026-01-01T00:00:00+0000") -> dict[str, str]:
        """Return the environment of a fixture git call: fixed identity, dates and no config."""
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        env.update(
            GIT_AUTHOR_NAME="Test",
            GIT_AUTHOR_EMAIL="test@example.invalid",
            GIT_COMMITTER_NAME="Test",
            GIT_COMMITTER_EMAIL="test@example.invalid",
            GIT_AUTHOR_DATE=when,
            GIT_COMMITTER_DATE=when,
            GIT_CONFIG_GLOBAL=os.devnull,
            GIT_CONFIG_NOSYSTEM="1",
        )
        return env

    def git(self, *args: str, when: str = "2026-01-01T00:00:00+0000") -> str:
        """Run git in the repository and return stdout."""
        return subprocess.run(
            ["git", "-C", str(self.root), *args],
            env=self.env(when),
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        ).stdout

    def write(self, rel: str, text: str | bytes) -> None:
        """Write one work-tree file, making its directories."""
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(text, bytes):
            path.write_bytes(text)
        else:
            path.write_text(text, encoding="utf-8")

    def commit(self, subject: str, when: str, files: dict | None = None) -> None:
        """Write files, stage everything and commit at `when`."""
        for rel, text in (files or {}).items():
            self.write(rel, text)
        self.git("add", "-A", when=when)
        self.git("commit", "-q", "--allow-empty", "-m", subject, when=when)


def table(rows: list[tuple[str, str]]) -> str:
    """Return a release PROGRESS.md with the generated slice table of (slice, status) rows."""
    lines = [
        "# Progress",
        "",
        BEGIN,
        "| id | slice | hacf_status | depends_on | last event |",
        "|---|---|---|---|---|",
    ]
    lines += [
        f"| OPR.1.{i} | {name} | {status} |  | x |" for i, (name, status) in enumerate(rows, 1)
    ]
    return "\n".join([*lines, END, ""])


def lore_doc(status: str) -> str:
    """Return a lore file with a Status line."""
    return f"---\ntitle: t\nstatus: {status}\n---\n\n# Doc\n"


CHANGELOG_1 = """# Changelog

## [Unreleased]

## [0.1.0] - 2026-01-03

### Added
- first feature
  - a nested bullet, not a feature
- second feature

### Known issues
- one known issue
"""

CHANGELOG_2 = CHANGELOG_1.replace(
    "## [Unreleased]\n",
    "## [Unreleased]\n\n## [v0.2.0] - 2026-01-06\n\n### ADDED\n- third feature\n\n"
    "### Fixed\n- a fix, not a feature\n",
)


def build(
    tmp_path: Path, omit: str | None = None, name: str = "demo", tracked: bool = False
) -> Path:
    """Build the fixture project, leaving out the source `omit` names; return its root.

    With `tracked`, every slice record carries the escape_tracking: v1 marker.
    """
    marker = MARKER if tracked else ""
    repo = Repo(tmp_path / name)
    first: dict = {"README.md": "demo\n"}
    if omit != "src":
        first["src/app.py"] = "a = 1\nb = 2\nc = 3\n"
        first["src/uv.lock"] = "lock\n" * 5
        first["src/blob.bin"] = b"\x00\x01\x02\x00"
    repo.commit("root", "2026-01-01T00:00:00+0000", first)

    files: dict = {}
    if omit != "tests":
        files["tests/unit/test_a.py"] = "def test_one():\n    pass\n\n\ndef test_two():\n    pass\n"
        files["tests/unit/helper.py"] = "x = 1\n"
        files["tests/e2e/test_flow.py"] = "async def test_flow():\n    pass\n"
        files["tests/fixtures/test_fx.py"] = "def test_fixture_data():\n    pass\n"
    if omit not in ("changelog", "changelog_and_progress"):
        text = CHANGELOG_1
        if omit == "version_section":
            text = text.replace("## [0.1.0]", "## [0.0.9]")
        files["CHANGELOG.md"] = text
    rows = [("01-a", "audited"), ("02-b", "audited"), ("03-c", "executed")]
    if omit not in ("release_progress", "changelog_and_progress"):
        files["missions/release-0.1.0/PROGRESS.md"] = (
            "# Progress\n\nno table here\n" if omit == "table_block" else table(rows)
        )
    if omit != "slice_progress":
        base = "missions/release-0.1.0/slices"
        files[f"{base}/01-a/PROGRESS.md"] = f"## Outcomes\n\n{marker}- t audited - ok\n"
        files[f"{base}/02-b/PROGRESS.md"] = (
            f"## Outcomes\n\n{marker}- t audit_failed - x\n- t audited\n"
        )
    if omit != "lore":
        files[f"lore/ADRs/{adr(1)}-one.md"] = lore_doc("ACCEPTED")
        files[f"lore/ADRs/{adr(2)}-two.md"] = lore_doc("PROPOSED")
        files[f"lore/STORIES/{story(1)}-one.md"] = lore_doc("PROVISIONAL")
    repo.commit("work for 0.1.0", "2026-01-03T00:00:00+0000", files)
    if omit != "tag":
        repo.git("tag", "v0.1.0")

    second: dict = {}
    if omit != "src":
        second["src/app.py"] = "a = 1\nb = 20\nc = 3\nd = 4\ne = 5\n"
    repo.commit(
        "replan one\n\nHACF-Replan-Of: OPR.1.1",
        "2026-01-04T00:00:00+0000",
        second,
    )
    third: dict = {}
    if omit != "tests":
        third["tests/integration/test_i.py"] = "def test_integration():\n    pass\n"
    repo.commit("demo resolve: replan 0.2.0", "2026-01-05T00:00:00+0000", third)
    last: dict = {}
    if omit not in ("changelog", "changelog_and_progress"):
        text = CHANGELOG_2
        if omit == "version_section":
            text = text.replace("## [0.1.0]", "## [0.0.9]")
        last["CHANGELOG.md"] = text
    if omit not in ("release_progress", "changelog_and_progress", "table_block"):
        last["missions/release-0.2.0/PROGRESS.md"] = table([("01-d", "audited")])
    if omit != "slice_progress":
        last["missions/release-0.2.0/slices/01-d/PROGRESS.md"] = (
            f"## Outcomes\n\n{marker}- t audited\n"
        )
    if omit != "lore":
        last[f"lore/ADRs/{adr(2)}-two.md"] = "# Two\n\n**Status:** Accepted\n"
        last[f"lore/STORIES/{story(2)}-two.md"] = lore_doc("Approved")
    repo.commit("work for 0.2.0", "2026-01-06T12:00:00+0000", last)
    if omit != "tag":
        repo.git("tag", "v0.2.0")

    if omit != "archive":
        for folder, report, prd in (
            ("20260103_000000_release_0_1_0", "F1 F2 F3 and F1 again\n", "F1 and F3 resolved\n"),
            ("20260106_000000_v0_2_0", "### F4\n", "F4 resolved, F40 is not F4's twin\n"),
        ):
            repo.write(f"spec/archive/{folder}/RedTeam_Report.md", report)
            if omit != "superprd":
                repo.write(f"spec/archive/{folder}/SuperPRD.md", prd)
    return repo.root


def tree_hash(root: Path) -> dict[str, str]:
    """Return the sha256 (or link target) of every file under root, `.git` included."""
    out = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if path.is_symlink():
            out[rel] = "link " + os.readlink(path)
        elif path.is_file():
            out[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
        else:
            out[rel] = "dir"
    return out


class Report:
    """The three outputs of one run."""

    def __init__(self, out: Path) -> None:
        """Read progress.json, progress.md and progress.html from `out`."""
        self.out = out
        self.json_text = (out / "progress.json").read_text(encoding="utf-8")
        self.data = json.loads(self.json_text)
        self.md = (out / "progress.md").read_text(encoding="utf-8")
        self.html = (out / "progress.html").read_text(encoding="utf-8")
        self.project = self.data["projects"][0]

    def metric(self, section: str, key: str, row: str) -> dict:
        """Return a JSON metric; `row` is a version, 'Total' or 'Headline'."""
        if section == "headline":
            return self.project["headline"][key]
        if row == "Total":
            return self.project["totals"][section][key]
        release = next(r for r in self.project["releases"] if r["tag"] == row)
        return release[section][key]

    def md_cell(self, section: str, column: str, row: str) -> str:
        """Return the Markdown cell at (section, column header, row label)."""
        block = self.md.split(SECTIONS[section] + "\n", 1)[1].split("\n## ", 1)[0]
        rows = [
            [c.strip() for c in re.split(r"(?<!\\)\|", line)[1:-1]]
            for line in block.splitlines()
            if line.startswith("|") and not line.startswith("|---")
        ]
        header = rows[0]
        wanted = "demo" if section == "headline" else {"Total": "**Total**"}.get(row, row)
        return next(r for r in rows[1:] if r[0] == wanted)[header.index(column)]

    def html_cell(self, section: str, column: str, row: str) -> tuple[str, str]:
        """Return (td attributes, text) of the HTML cell at (section, column header, row label)."""
        title = html.escape(SECTIONS[section].removeprefix("## "))
        block = self.html.split(f"<h2>{title}</h2>", 1)[1].split("<h2>", 1)[0]
        header = [html.unescape(c) for c in re.findall(r"<th>(.*?)</th>", block)]
        wanted = "demo" if section == "headline" else row
        for line in re.findall(r"<tr[^>]*>(.*?)</tr>", block, re.S):
            cells = re.findall(r"<td([^>]*)>(.*?)</td>", line, re.S)
            if cells and re.sub(r"<[^>]+>", "", cells[0][1]) == wanted:
                attrs, text = cells[header.index(column)]
                return attrs, html.unescape(re.sub(r"<[^>]+>", "", text))
        raise AssertionError(f"no HTML row {wanted!r} in {section}")
