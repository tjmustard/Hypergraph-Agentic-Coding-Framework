"""Contract tests for the S14 supporting skills sweep (MiniPRD_25).

The supporting skills are prose, so the checks are greps over a fixed scanned set: the 15
supporting SKILL.md files, three META.yml files and the hyper-process-document bridge. Nothing
here writes inside the repository.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from conftest import REPO_ROOT

SKILLS = REPO_ROOT / ".agents" / "skills"
ORCHESTRATION = REPO_ROOT / ".agents" / "config" / "orchestration.yml"
BRIDGE = REPO_ROOT / ".claude" / "commands" / "hyper-process-document.md"
PROCESS_SKILL = SKILLS / "hyper-process-document" / "SKILL.md"

SUPPORTING = (
    "hyper-clear",
    "hyper-sop",
    "hyper-handoff",
    "hyper-troubleshooting",
    "hyper-process-document",
    "hyper-refresh-memory",
    "hyper-session-update",
    "hyper-document",
    "hyper-consult-cto",
    "hyper-contextualize",
    "hyper-create-issue",
    "hyper-deepdive",
    "hyper-peer-review",
    "hyper-stitch-design",
    "hyper-publish",
)
META_SKILLS = ("hyper-handoff", "hyper-contextualize", "hyper-publish")
SCANNED = (
    tuple(SKILLS / name / "SKILL.md" for name in SUPPORTING)
    + tuple(SKILLS / name / "META.yml" for name in META_SKILLS)
    + (BRIDGE,)
)

FORBIDDEN = (
    re.compile(r"SuperPRD\.md"),
    re.compile(r"productContext"),
    re.compile(r"systemPatterns"),
    re.compile(r"spec/handoffs"),
    re.compile(r"(?<![\w./-])python\s"),
)
MODEL_ID = re.compile(r"\bclaude-(?:\d[\w.-]*|(?:opus|sonnet|haiku|fable|instant)-[\w.-]+)")


def _rel(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def _models() -> dict:
    models = yaml.safe_load(ORCHESTRATION.read_text(encoding="utf-8"))["models"]
    assert isinstance(models, dict) and models, f"{_rel(ORCHESTRATION)}: no models map"
    return models


def _frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---"), f"{_rel(path)}: does not start with ---"
    lines = text.splitlines()
    try:
        end = lines.index("---", 1)
    except ValueError:
        raise AssertionError(f"{_rel(path)}: no closing ---") from None
    data = yaml.safe_load("\n".join(lines[1:end]))
    assert isinstance(data, dict), f"{_rel(path)}: frontmatter is not a mapping"
    return data


def test_supporting_skills_grep_contract():
    """No retired store name or bare `python` command appears in the scanned set."""
    hits = []
    for path in SCANNED:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for pattern in FORBIDDEN:
                for match in pattern.finditer(line):
                    hits.append(f"{_rel(path)}:{number}: {match.group(0)!r}")
    assert not hits, "stale vocabulary or bare interpreter:\n" + "\n".join(hits)


def test_supporting_meta_model_ids_current():
    """Every model id is current, and each META.yml pins models[assigned_model]."""
    models = _models()
    current = set(models.values())
    stale = []
    for path in SCANNED:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for match in MODEL_ID.finditer(line):
                token = match.group(0).rstrip(".,;:-")
                if token not in current:
                    stale.append(f"{_rel(path)}:{number}: {token!r}")
    assert not stale, "model ids not in orchestration.yml models:\n" + "\n".join(stale)
    for name in META_SKILLS:
        meta_path = SKILLS / name / "META.yml"
        meta = yaml.safe_load(meta_path.read_text(encoding="utf-8"))
        assigned = meta["assigned_model"]
        assert assigned in models, f"{_rel(meta_path)}: unknown assigned_model {assigned!r}"
        assert meta["model_version"] == models[assigned], (
            f"{_rel(meta_path)}: model_version {meta['model_version']!r} != {models[assigned]!r}"
        )


def test_supporting_skills_have_frontmatter():
    """Every SKILL.md has YAML frontmatter with a non-empty name and description."""
    skills = sorted(SKILLS.glob("*/SKILL.md"))
    assert skills, "no SKILL.md files found"
    for path in skills:
        data = _frontmatter(path)
        for key in ("name", "description"):
            value = data.get(key)
            assert isinstance(value, str) and value.strip(), f"{_rel(path)}: empty {key}"


def test_process_document_bridge_matches_skill():
    """The bridge description matches the skill's, and the bridge names the skill."""
    bridge = _frontmatter(BRIDGE)
    skill = _frontmatter(PROCESS_SKILL)
    assert bridge["description"] == skill["description"]
    assert ".agents/skills/hyper-process-document/SKILL.md" in BRIDGE.read_text(encoding="utf-8")
