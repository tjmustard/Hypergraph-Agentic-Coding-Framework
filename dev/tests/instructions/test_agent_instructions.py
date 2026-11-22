"""Contract tests for the S15a1 agent instruction files (MiniPRD_26).

The instruction files are prose, so the checks are greps over a fixed scanned set: root AGENTS.md,
CLAUDE.md and GEMINI.md, their three install templates and the four IDE rule bridges. The install
templates are block bodies: HACF-install.sh and hyper_migrate.py pass each one to hyper_blocks.py,
which wraps it in the `hacf` markers, so Test 4 and Test 5 check the wrapped form, built under
tmp_path. Nothing here writes inside the repository.
"""

from __future__ import annotations

import hashlib
import importlib.util
import re
from pathlib import Path

import hyper_blocks
import yaml
from conftest import FIXTURES_DIR, REPO_ROOT

TEMPLATES = REPO_ROOT / ".agents" / "install-templates"
ORCHESTRATION = REPO_ROOT / ".agents" / "config" / "orchestration.yml"
ROOT_AGENTS = REPO_ROOT / "AGENTS.md"
TEMPLATE_AGENTS = TEMPLATES / "AGENTS.md"
SCANNED = (
    ROOT_AGENTS,
    REPO_ROOT / "CLAUDE.md",
    REPO_ROOT / "GEMINI.md",
    TEMPLATE_AGENTS,
    TEMPLATES / "CLAUDE.md",
    TEMPLATES / "GEMINI.md",
    REPO_ROOT / ".cursor" / "rules" / "hypergraph-agent.mdc",
    REPO_ROOT / ".clinerules" / "hypergraph-agent.md",
    REPO_ROOT / ".roo" / "rules" / "hypergraph-agent.md",
    REPO_ROOT / ".windsurf" / "rules" / "hypergraph-agent.md",
)
INSTRUCTION_NAMES = ("AGENTS.md", "CLAUDE.md", "GEMINI.md")

TRAILHEAD = "Trailhead: start at lore/MAP.md, then SPEC.md, then the active mission under missions/"
FORBIDDEN = (
    re.compile(r"(?<![\w./-])python\s"),
    re.compile(re.escape(".agent/schemas")),
    re.compile(r"SuperPRD\.md"),
    re.compile(re.escape("spec/handoffs")),
    re.compile("productContext"),
    re.compile("systemPatterns"),
    re.compile("final_truth"),
    re.compile("hyper_fix"),
    re.compile(re.escape("gpg --import")),
    re.compile("GPG signature"),
)
# S14's model-id regex (dev/tests/skills/test_supporting_contract.py).
MODEL_ID = re.compile(r"\bclaude-(?:\d[\w.-]*|(?:opus|sonnet|haiku|fable|instant)-[\w.-]+)")
LIMIT_PHRASES = (
    "stale CANON",
    "origin: user-decision",
    "default or acceptEdits",
    "bypassPermissions",
    "forged ticket",
    "lore_hypergraph.yml",
    "--setting-sources user",
    "bwrap",
    "Linux",
)

# sha256 of each section of the pre-slice root CLAUDE.md (984ca46, unchanged up to S15a1's Start),
# from its heading line up to, not including, the next line that starts with '#' or equals '---'.
LOOP_SECTIONS = {
    "### Context Window Management": (
        "2034f2af0a8603f3d2bc64e212ac44b110b6cb68ce2ea886f28c9c3da990f2e1"
    ),
    "### Safe Sandboxing & Automation": (
        "c98a91d9863eab4ba0c41a65b7458237afda9d59c9e8b73e560fd7614b9c3eaa"
    ),
}
AGENTS_MAX_BYTES = 16515  # v0.5.9 root AGENTS.md (git show 984ca46:AGENTS.md | wc -c)
CODEX_LIMIT_BYTES = 32768  # Codex truncates instruction files at 32 KiB (R24)


def _rel(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _section(data: bytes, heading: str) -> bytes | None:
    """Return the bytes from the heading line up to the next '#' line or '---' line."""
    lines = data.splitlines(keepends=True)
    for start, line in enumerate(lines):
        if line.rstrip(b"\r\n") == heading.encode():
            end = start + 1
            while end < len(lines) and not (
                lines[end].startswith(b"#") or lines[end].rstrip(b"\r\n") == b"---"
            ):
                end += 1
            return b"".join(lines[start:end])
    return None


def _installed(template: Path, dest: Path) -> Path:
    """Install a template the way HACF-install.sh does on a fresh project; return the file."""
    target = dest / template.name
    outcome = hyper_blocks.decide_instruction_file(
        target, template.read_bytes(), interactive=False, assume_yes=True, root=dest
    )
    assert outcome.line().startswith("created:"), outcome.line()
    return target


def _openrig_block() -> str:
    """Return the OpenRig block the S12a fixture builder appends to AGENTS.md."""
    spec = importlib.util.spec_from_file_location(
        "build_user_project_v059", FIXTURES_DIR / "build_user_project_v059.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.OPENRIG_BLOCK


def test_instruction_files_grep_contract() -> None:
    """Test 1: no retired name or bare interpreter; model ids current; trailhead and notices."""
    models = set(yaml.safe_load(_text(ORCHESTRATION))["models"].values())
    found = []
    for path in SCANNED:
        text = _text(path)
        found += [(_rel(path), rx.pattern) for rx in FORBIDDEN if rx.search(text)]
        found += [(_rel(path), m) for m in MODEL_ID.findall(text) if m not in models]
    assert found == []
    for path in (ROOT_AGENTS, TEMPLATE_AGENTS):
        text = _text(path)
        assert TRAILHEAD in text.splitlines(), _rel(path)
        for word in ("v0.7.0", "2027-01-31", "unsigned", "SSH"):
            assert word in text, (_rel(path), word)


def test_claude_md_keeps_loop_sections() -> None:
    """Test 2: root CLAUDE.md keeps both loop sections byte-for-byte (R19)."""
    data = (REPO_ROOT / "CLAUDE.md").read_bytes()
    for heading, expected in LOOP_SECTIONS.items():
        section = _section(data, heading)
        assert section is not None, heading
        assert hashlib.sha256(section).hexdigest() == expected, heading


def test_agents_md_binding_and_limits_text() -> None:
    """Test 3: binding constraints and stated limits; 'security boundary' only negated (R7, R23)."""
    for path in (ROOT_AGENTS, TEMPLATE_AGENTS):
        text = _text(path)
        missing = [phrase for phrase in LIMIT_PHRASES if phrase not in text]
        assert missing == [], (_rel(path), missing)
    for path in SCANNED:
        text = " ".join(_text(path).split())
        for sentence in re.split(r"(?<=[.!?])\s", text):
            if "security boundary" in sentence:
                assert sentence.count("security boundary") == sentence.count(
                    "not a security boundary"
                ), (_rel(path), sentence)


def test_instruction_blocks_balanced(tmp_path: Path) -> None:
    """Test 4: no malformed HACF marker; each installed template is exactly one hacf block (R26)."""
    for path in SCANNED:
        if path.suffix == ".md":
            hyper_blocks.check_file(path)
    for name in INSTRUCTION_NAMES:
        dest = tmp_path / name.split(".")[0].lower()
        dest.mkdir()
        installed = _installed(TEMPLATES / name, dest)
        doc = hyper_blocks.parse_doc(installed.read_bytes())
        blocks = hyper_blocks.scan(doc, hyper_blocks.MARKDOWN)
        assert [(b.kind, b.block_id) for b in blocks] == [
            (hyper_blocks.MANAGED, hyper_blocks.INSTRUCTION_BLOCK_ID)
        ], name
        if name == "AGENTS.md":
            body = [line for line in hyper_blocks.body_of(doc, blocks[0]) if line.strip()]
            assert body[0].rstrip(b"\r\n").decode() == TRAILHEAD


def test_agents_md_size_bytes(tmp_path: Path) -> None:
    """Test 5: root AGENTS.md within v0.5.9's size; template + OpenRig block under 32 KiB (R24)."""
    assert len(ROOT_AGENTS.read_bytes()) <= AGENTS_MAX_BYTES
    installed = _installed(TEMPLATE_AGENTS, tmp_path)
    with installed.open("a", encoding="utf-8") as handle:
        handle.write(_openrig_block())
    hyper_blocks.check_file(installed)
    assert len(installed.read_bytes()) < CODEX_LIMIT_BYTES
