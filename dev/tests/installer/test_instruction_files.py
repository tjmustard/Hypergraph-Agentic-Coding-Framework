"""K7: AGENTS.md is installed for every IDE selection, upgrade-safe (OPR.0.6.1.2; mission SPEC R13).

Every IDE's installed instructions tell agents to read AGENTS.md, so any non-empty IDE selection
installs it through the same instruction-file decision as CLAUDE.md and GEMINI.md. A user's own
text is never changed. Case (7) is the general check that closes the K7 class: every repo-relative
path an installed instruction file points at exists, for each single-IDE install. Every run happens
in a temp project; nothing is written inside the HACF checkout.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from .conftest import (
    UNINSTALLER,
    run_uninstall_script,
)

MANIFEST_REL = ".agents/hacf-manifest.json"
BEGIN = b"<!-- BEGIN HACF MANAGED BLOCK: hacf -->"
END = b"<!-- END HACF MANAGED BLOCK: hacf -->"
SINGLE_IDES = ("claude", "antigravity", "windsurf", "cursor", "cline", "roo", "universal")

# Case (7), R15: paths the workflow creates later, each with a one-line reason. An entry here is
# an explicit, reviewed exception, never a way to silence a dangling pointer.
CREATED_LATER: dict[str, str] = {
    "spec/compiled/architecture.yml": "no ledger on a fresh install; hyper_ledger.py init makes it",
    ".agents/logs/update-consent.jsonl": "written on the first update consent",
    ".agents/.backup/": "made by the first backup",
}
# Case (7), R15: known dangling pointers. Each must still dangle, so an entry cannot go stale.
# K8 (R17a) emptied it: the templates now name HACF's published README by URL.
KNOWN_DANGLING: dict[str, str] = {}
README_URL = "https://github.com/tjmustard/Hypergraph-Agentic-Coding-Framework#readme"
INSTALL_TEMPLATES = ".agents/install-templates"
# R15b: each IDE's own directory (IDE_DEFS); a path inside an unselected IDE's dir is skipped.
IDE_DIRS = {
    "claude": ".claude/",
    "windsurf": ".windsurf/",
    "cursor": ".cursor/",
    "cline": ".clinerules/",
    "roo": ".roo/",
}

# The installed instruction files: the three root files and the IDE rule files.
ROOT_INSTRUCTIONS = ("CLAUDE.md", "AGENTS.md", "GEMINI.md")
RULE_GLOBS = (
    ".cursor/rules/*",
    ".clinerules/*",
    ".roo/rules/*",
    ".roo/rules-*/*",
    ".windsurf/rules/*",
)
BACKTICKED = re.compile(r"`([^`\n]+)`")
PATH_SUFFIXES = (".md", ".yml", ".yaml", ".json", ".py")
FORBIDDEN_CHARS = set("<>*{ ")


def manifest_of(project: Path) -> dict:
    """Return the project's parsed manifest."""
    return json.loads((project / MANIFEST_REL).read_text(encoding="utf-8"))


def report_lines(output: str) -> list[str]:
    """Return the install report's lines (between its header and the banner)."""
    start = output.index("📝  Install report")
    end = output.index("╔", start)
    return output[start:end].splitlines()[1:]


def outside_block(data: bytes) -> tuple[bytes, bytes]:
    """Return the bytes before the HACF block's BEGIN marker and after its END marker line."""
    start = data.index(BEGIN)
    end = data.index(END) + len(END)
    return data[:start], data[end:]


# ---------------------------------------------------------------------------
# (1), (2): fresh installs
# ---------------------------------------------------------------------------


def test_fresh_claude_install_creates_agents_md(project: Path, run_install) -> None:
    """(1) -y --ides=claude creates AGENTS.md with the HACF block, recorded in the manifest."""
    run = run_install(project, "-y", "--ides=claude")
    assert run.returncode == 0, run.output
    assert BEGIN in (project / "AGENTS.md").read_bytes()
    assert manifest_of(project)["blocks"].get("AGENTS.md"), manifest_of(project)["blocks"]
    assert manifest_of(project)["ides"] == ["claude"]


@pytest.mark.parametrize("ide", SINGLE_IDES)
def test_every_single_ide_installs_agents_md(project: Path, run_install, ide: str) -> None:
    """(2) AGENTS.md exists after -y --ides=<id>, for every single IDE."""
    run = run_install(project, "-y", f"--ides={ide}")
    assert run.returncode == 0, run.output
    assert BEGIN in (project / "AGENTS.md").read_bytes(), ide
    assert manifest_of(project)["ides"] == [ide]


def test_dry_run_prints_the_agents_md_decision(project: Path, run_install) -> None:
    """Task 2: --dry-run prints AGENTS.md's decision line like the other files."""
    run = run_install(project, "-y", "--ides=claude", "--dry-run")
    assert run.returncode == 0, run.output
    assert "[DRY-RUN] would apply the HACF block decision to AGENTS.md" in run.output
    assert not (project / "AGENTS.md").exists()


def test_preserve_custom_still_skips_agents_md(project: Path, run_install) -> None:
    """--preserve-custom still skips AGENTS.md (R13)."""
    run = run_install(project, "-y", "--ides=claude", "--preserve-custom")
    assert run.returncode == 0, run.output
    assert "AGENTS.md skipped (--preserve-custom)" in run.output
    assert not (project / "AGENTS.md").exists()


def test_universal_lists_agents_md_once(project: Path, run_install) -> None:
    """With claude + universal, AGENTS.md goes through the decision once, not twice."""
    decision = "[DRY-RUN] would apply the HACF block decision to AGENTS.md"
    dry = run_install(project, "-y", "--ides=claude,universal", "--dry-run")
    assert dry.returncode == 0, dry.output
    assert dry.output.count(decision) == 1, dry.output
    run = run_install(project, "-y", "--ides=claude,universal")
    assert run.returncode == 0, run.output
    assert (project / "AGENTS.md").read_bytes().count(BEGIN) == 1


# ---------------------------------------------------------------------------
# (4), (5): the user's own AGENTS.md is never changed
# ---------------------------------------------------------------------------

USER_BEFORE = b"# Our project\n\nOur own rules, before the HACF block.\n\n"
USER_AFTER = b"\n\n## Team notes\n\nOur own rules, after the HACF block.\n"


def user_agents_md_with_block(project: Path) -> None:
    """Wrap the installed AGENTS.md's HACF block in the user's own text."""
    path = project / "AGENTS.md"
    data = path.read_bytes()
    start = data.index(BEGIN)
    end = data.index(END) + len(END)
    path.write_bytes(USER_BEFORE + data[start:end] + USER_AFTER)


# ---------------------------------------------------------------------------
# (6): uninstall removes only what HACF owns
# ---------------------------------------------------------------------------


def test_uninstall_removes_block_only_agents_md(
    project: Path, scratch: Path, run_install, upstream
) -> None:
    """(6) after (1), AGENTS.md held nothing but the HACF block: uninstall removes all of it.

    The uninstaller's existing rule for a root instruction file (CLAUDE.md included) removes the
    block and reports the now-empty file instead of deleting it; this slice does not change it.
    """
    assert run_install(project, "-y", "--ides=claude").returncode == 0
    run = run_uninstall_script(scratch, upstream.url, project, "-y", script=UNINSTALLER)
    assert run.returncode == 0, run.output
    agents = project / "AGENTS.md"
    assert not agents.exists() or agents.read_bytes() == b"", agents.read_bytes()
    if agents.exists():
        assert "AGENTS.md: now empty (it held only the HACF block)" in run.output


def test_uninstall_keeps_user_text_in_agents_md(
    project: Path, scratch: Path, run_install, upstream
) -> None:
    """(6) after (4), uninstall removes the HACF block and keeps the user's text."""
    assert run_install(project, "-y", "--ides=claude").returncode == 0
    user_agents_md_with_block(project)
    assert run_install(project, "-y").returncode == 0
    run = run_uninstall_script(scratch, upstream.url, project, "-y", script=UNINSTALLER)
    assert run.returncode == 0, run.output
    data = (project / "AGENTS.md").read_bytes()
    assert BEGIN not in data and END not in data
    assert b"Our own rules, before the HACF block." in data
    assert b"Our own rules, after the HACF block." in data


# ---------------------------------------------------------------------------
# (7): every path an installed instruction file points at exists
# ---------------------------------------------------------------------------


def is_repo_path(token: str) -> bool:
    """Return True for a backticked token that names a repo-relative path (Task 4 (7))."""
    if FORBIDDEN_CHARS & set(token) or "://" in token:
        return False
    if token.startswith(("/", "~", "-", "$")) or "(" in token:
        return False
    return "/" in token or token.endswith(PATH_SUFFIXES)


def pointed_paths(text: str) -> set[str]:
    """Return every repo-relative path a backticked token in text names, with #anchors dropped."""
    paths = set()
    for token in BACKTICKED.findall(text):
        token = token.strip().split("#", 1)[0].rstrip(":,.")
        if token and is_repo_path(token):
            paths.add(token.removeprefix("./"))
    return paths


def instruction_files(project: Path) -> list[Path]:
    """Return the installed instruction files: the root three and the IDE rule files."""
    found = [project / rel for rel in ROOT_INSTRUCTIONS if (project / rel).is_file()]
    for pattern in RULE_GLOBS:
        found += sorted(path for path in project.glob(pattern) if path.is_file())
    return found


def is_top_level_dir_mention(rel: str) -> bool:
    """Return True for a lone top-level dir with a trailing slash (`node_modules/`, `.cursor/`)."""
    return rel.endswith("/") and "/" not in rel[:-1]


def resolves(project: Path, rel: str) -> bool:
    """Return True when a pointed path exists under R15's resolution rules.

    A path with a `/` must exist at that path. A bare name passes if it exists at the root or
    anywhere outside .agents/install-templates/ (so a template copy never satisfies a pointer).
    """
    if (project / rel).exists():
        return True
    if "/" in rel:
        return False
    templates = project / INSTALL_TEMPLATES
    return any(templates not in hit.parents for hit in project.rglob(rel))


def created_later(rel: str) -> bool:
    """Return True for a CREATED_LATER key, or (R15a) a bare name equal to one key's basename."""
    if rel in CREATED_LATER:
        return True
    return "/" not in rel and any(Path(key).name == rel for key in CREATED_LATER)


def in_unselected_ide_dir(rel: str, ide: str) -> bool:
    """Return True (R15b) for a path inside the own directory of an IDE other than `ide`."""
    return any(rel.startswith(top) for other, top in IDE_DIRS.items() if other != ide)


def dangling(project: Path, ide: str) -> list[str]:
    """Return "<file> -> <path>" for every unresolved pointer of the installed instruction files."""
    missing = []
    for path in instruction_files(project):
        for rel in sorted(pointed_paths(path.read_text(encoding="utf-8"))):
            if is_top_level_dir_mention(rel) or rel in KNOWN_DANGLING:
                continue
            if created_later(rel) or in_unselected_ide_dir(rel, ide):
                continue
            if not resolves(project, rel):
                missing.append(f"{path.relative_to(project)} -> {rel}")
    return missing


def test_extractor_bites() -> None:
    """(7) the extractor sees K7's own dangling pointer and skips placeholders and commands."""
    text = (
        "Read `AGENTS.md` and `spec/compiled/architecture.yml#nodes`; run `python3 x.py --y`, "
        "see `<spec>`, `missions/*/SPEC.md`, `{a,b}.md`, `https://x.io/a.md` and `uv run`."
    )
    assert pointed_paths(text) == {"AGENTS.md", "spec/compiled/architecture.yml"}
    assert is_top_level_dir_mention("node_modules/") and is_top_level_dir_mention(".cursor/")
    assert not is_top_level_dir_mention(".agents/.backup/")
    assert created_later("architecture.yml") and not created_later("other.yml")
    assert in_unselected_ide_dir(".claude/settings.json", "cursor")
    assert not in_unselected_ide_dir(".claude/settings.json", "claude")


@pytest.mark.parametrize("ide", SINGLE_IDES)
def test_every_pointed_path_exists(project: Path, run_install, ide: str) -> None:
    """(7) for each single-IDE install, every path the instruction files point at exists."""
    run = run_install(project, "-y", f"--ides={ide}")
    assert run.returncode == 0, run.output
    files = instruction_files(project)
    assert any(path.name == "AGENTS.md" for path in files), files
    missing = dangling(project, ide)
    assert missing == [], f"--ides={ide}: dangling pointers:\n" + "\n".join(missing)
    # The pointer means the project-root file (HACF's usage guide), so "still dangling" is checked
    # at that path: an unrelated installed .agents/schemas/project-templates/README.md would
    # satisfy the bare-name rule but is not what CLAUDE.md points at.
    for rel, reason in KNOWN_DANGLING.items():
        assert not (project / rel).exists(), f"{rel} no longer dangles; drop it ({reason})"
