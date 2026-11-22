"""Contract tests for the v0.6.0 user documentation (S15a2, MiniPRD_27 Tests 1-5).

The docs are prose, so the checks are greps and a link resolver over a fixed scanned set:
README.md, docs/*.md, skills-info.md, Agentic-Memory.md, DEVELOPMENT.md and the project
template DEVELOPMENT.md. Stdlib and PyYAML only; nothing here writes inside the repository and
no http(s) link is fetched.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from conftest import REPO_ROOT

README = REPO_ROOT / "README.md"
LORE_MD = REPO_ROOT / "docs" / "Lore.md"
ORCHESTRATION = REPO_ROOT / ".agents" / "config" / "orchestration.yml"


def _scanned() -> tuple[Path, ...]:
    return (
        README,
        *sorted((REPO_ROOT / "docs").glob("*.md")),
        REPO_ROOT / "skills-info.md",
        REPO_ROOT / "Agentic-Memory.md",
        REPO_ROOT / "DEVELOPMENT.md",
        REPO_ROOT / ".agents" / "schemas" / "project-templates" / "DEVELOPMENT.md",
    )


FORBIDDEN = (
    re.compile(r"(?<![\w./-])python\s"),
    re.compile(r"SuperPRD\.md"),
    re.compile(r"productContext"),
    re.compile(r"systemPatterns"),
    re.compile(r"spec/handoffs"),
    re.compile(r"final_truth"),
    re.compile(r"Final Truth"),
    re.compile(r"compaction_engine"),
    re.compile(r"interrupt_detector"),
    re.compile(r"hyper_fix"),
)
# S14's model-id regex (dev/tests/skills/test_supporting_contract.py).
MODEL_ID = re.compile(r"\bclaude-(?:\d[\w.-]*|(?:opus|sonnet|haiku|fable|instant)-[\w.-]+)")
NEGATION = re.compile(r"\b(?:not|never|no|isn't|nor)\b", re.IGNORECASE)
GLOBAL_KEY = (
    re.compile(r"gpg\s+--import"),
    re.compile(r"--recv-keys"),
    re.compile(r"--keyserver"),
    re.compile(
        r"git\s+config\s+--global\s+\S*(?:user\.signingkey|gpg\.ssh\.allowedSignersFile)",
        re.IGNORECASE,
    ),
)
UNINSTALL_WARNING = "delete any saved copy of HACF-uninstall.sh"
FENCE = re.compile(r"^\s*(```|~~~)")
HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
LINK = re.compile(r"!?\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
ANCHOR_ID = re.compile(r"<a\s+(?:id|name)=\"([^\"]+)\"")


def _rel(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _prose_lines(text: str) -> list[tuple[int, str]]:
    """Return (line number, line) outside fenced code blocks."""
    out, fenced = [], False
    for number, line in enumerate(text.splitlines(), 1):
        if FENCE.match(line):
            fenced = not fenced
            continue
        if not fenced:
            out.append((number, line))
    return out


def _slug(heading: str) -> str:
    """Return GitHub's anchor slug of a heading's text."""
    text = re.sub(r"`([^`]*)`", r"\1", heading)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"<[^>]+>", "", text).strip().lower()
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def _anchors(path: Path) -> set[str]:
    """Return every heading slug (GitHub de-duplication included) and <a id> of a file."""
    text = _text(path)
    found: set[str] = set(ANCHOR_ID.findall(text))
    seen: dict[str, int] = {}
    for _, line in _prose_lines(text):
        match = HEADING.match(line)
        if not match:
            continue
        slug = _slug(match.group(2))
        count = seen.get(slug, 0)
        found.add(slug if count == 0 else f"{slug}-{count}")
        seen[slug] = count + 1
    return found


def _section(path: Path, heading_re: re.Pattern) -> list[tuple[str, list[str]]]:
    """Return (heading, body lines up to the next heading of the same or higher level)."""
    lines = _text(path).splitlines()
    out = []
    for index, line in enumerate(lines):
        match = HEADING.match(line)
        if not match or not heading_re.search(match.group(2)):
            continue
        level = len(match.group(1))
        body = []
        for later in lines[index + 1 :]:
            other = HEADING.match(later)
            if other and len(other.group(1)) <= level:
                break
            body.append(later)
        out.append((match.group(2), body))
    return out


def _sentences(text: str) -> list[str]:
    flat = re.sub(r"\s+", " ", text)
    return re.split(r"(?<=[.!?])\s+|\s+-\s+|\|", flat)


def test_docs_grep_contract():
    """Test 1: no retired vocabulary, bare python, stale model id or unnegated boundary claim."""
    models = yaml.safe_load(_text(ORCHESTRATION))["models"]
    current = set(models.values())
    hits = []
    for path in _scanned():
        text = _text(path)
        for number, line in enumerate(text.splitlines(), 1):
            for pattern in FORBIDDEN:
                if pattern.search(line):
                    hits.append(f"{_rel(path)}:{number}: {pattern.pattern!r}: {line.strip()}")
            for match in MODEL_ID.finditer(line):
                token = match.group(0).rstrip(".,;:-")
                if token not in current:
                    hits.append(f"{_rel(path)}:{number}: model id {token!r} not in models")
        for sentence in _sentences(text):
            if "security boundary" in sentence.lower() and not NEGATION.search(sentence):
                hits.append(f"{_rel(path)}: unnegated 'security boundary': {sentence.strip()}")
    assert not hits, "docs grep contract:\n" + "\n".join(hits)

    sections = _section(README, re.compile(r"New Project", re.IGNORECASE))
    assert sections, "README.md has no New Project section"
    for _, body in sections:
        joined = "\n".join(body)
        assert "git init" in joined
        assert "HACF-install.sh" in joined
        assert "use it as your project root" not in joined.lower()


def test_docs_no_global_key_import():
    """Test 2: no doc tells the user to import a signing key or set signers globally (R11)."""
    hits = []
    for path in _scanned():
        for number, line in enumerate(_text(path).splitlines(), 1):
            for pattern in GLOBAL_KEY:
                if pattern.search(line):
                    hits.append(f"{_rel(path)}:{number}: {line.strip()}")
    assert not hits, "global key import or signer config:\n" + "\n".join(hits)


def test_docs_uninstall_warning_leads():
    """Test 3: README's uninstall and upgrade sections open with the R10 warning."""
    pattern = re.compile(r"\b(Uninstall|Upgrad)", re.IGNORECASE)
    sections = _section(README, pattern)
    names = [heading for heading, _ in sections]
    assert any(re.search(r"uninstall", h, re.IGNORECASE) for h in names), names
    assert any(re.search(r"upgrad", h, re.IGNORECASE) for h in names), names
    for heading, body in sections:
        first = next((line for line in body if line.strip()), "")
        assert UNINSTALL_WARNING in first, f"README.md section {heading!r} opens with {first!r}"


def test_docs_internal_links():
    """Test 4: every relative link and #anchor in the scanned set resolves."""
    broken = []
    for path in _scanned():
        for number, line in _prose_lines(_text(path)):
            line = re.sub(r"`[^`]*`", "", line)
            for target in LINK.findall(line):
                if re.match(r"^[a-z][a-z0-9+.-]*:", target, re.IGNORECASE):
                    continue  # http(s), mailto: never fetched
                file_part, _, anchor = target.partition("#")
                dest = path if not file_part else (path.parent / file_part).resolve()
                where = f"{_rel(path)}:{number}: {target}"
                if not dest.exists():
                    broken.append(f"{where}: no such file")
                    continue
                if anchor and dest.suffix == ".md" and anchor not in _anchors(dest):
                    broken.append(f"{where}: no heading or <a id> {anchor!r}")
    assert not broken, "broken internal links:\n" + "\n".join(broken)


def test_lore_md_topics():
    """Test 5: docs/Lore.md covers the gate, its limits, CODEOWNERS and the MCP entries."""
    assert LORE_MD.is_file(), "docs/Lore.md is missing"
    text = _text(LORE_MD)
    flat = re.sub(r"\s+", " ", text)
    for needle in (
        "hook",
        "ticket",
        "TTY",
        "default or acceptEdits",
        "forged ticket",
        "lore_hypergraph.yml",
        "attribution",
        "CODEOWNERS",
        "lore/canon/**",
        "lore/CANON.md",
        "info/attributes",
        "uvx codebase-memory-mcp@0.12.1",
    ):
        assert needle in flat, f"docs/Lore.md does not name {needle!r}"
    codeowners = [line for line in text.splitlines() if "lore_hypergraph.yml" in line]
    assert codeowners, "no CODEOWNERS line for lore_hypergraph.yml"
