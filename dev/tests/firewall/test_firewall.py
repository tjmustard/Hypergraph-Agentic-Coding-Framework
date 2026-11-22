"""Deterministic tests for hyper_firewall.py (S11): rendering, the settings merge, ignore blocks.

Every test works in a temp git repository; the shipped .agentignore is only read.
"""

from __future__ import annotations

import json
from pathlib import Path

import hyper_firewall
import pytest
from conftest import REPO_ROOT, SCRIPTS_DIR

GOLDEN = Path(__file__).parent / "golden" / "claude_rules.json"
SHIPPED = (REPO_ROOT / ".agentignore").read_text(encoding="utf-8")
USER_SETTINGS = {
    "statusLine": {"type": "command", "command": "my-status"},
    "permissions": {"allow": ["Bash(ls *)"], "deny": ["Read(/secrets/**)"]},
    "env": {"MY_VAR": "1"},
    "attribution": {"commit": "", "pr": ""},
    "hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "echo hi"}]}]},
}


@pytest.fixture
def firewall(tmp_git_repo, run_script):
    """Return a function that runs hyper_firewall.py in the temp repository."""
    tmp_git_repo.write(".agentignore", SHIPPED)

    def run(*args: str):
        return run_script(SCRIPTS_DIR / "hyper_firewall.py", *args, cwd=tmp_git_repo.path)

    return run


def _settings(repo) -> dict:
    return json.loads((repo.path / ".claude/settings.json").read_text(encoding="utf-8"))


def _backups(repo) -> list[Path]:
    root = repo.path / ".agents/.backup"
    return sorted(root.rglob("*")) if root.exists() else []


def test_render_claude_rules_golden():
    """The shipped .agentignore plus the fixed rules render to the golden JSON, byte for byte."""
    rendered = json.dumps(hyper_firewall.render_claude(SHIPPED), indent=2) + "\n"
    assert rendered == GOLDEN.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "pattern, path",
    [
        ("spec/archive/", "/spec/archive/**"),
        ("node_modules/", "/**/node_modules/**"),
        (".env", "/**/.env"),
        ("/build/", "/build/**"),
        ("/top.txt", "/top.txt"),
        ("a/b.txt", "/a/b.txt"),
        (".git/", "/.git/**"),
    ],
)
def test_translate_patterns(pattern, path):
    """Each gitignore shape translates per subsystems.md#firewall."""
    assert hyper_firewall.translate(pattern) == path


def test_negation_warned_and_skipped(firewall, tmp_git_repo):
    """A `!` pattern gets no rule and a warning, and stays verbatim in the ignore-file block."""
    tmp_git_repo.write(".agentignore", "secret/\n!secret/keep\n")
    proc = firewall("render", "--ides", "claude,cursor")
    assert proc.returncode == 0, proc.stderr
    assert "negation !secret/keep" in proc.stdout
    assert not any("keep" in rule for rule in _settings(tmp_git_repo)["permissions"]["deny"])
    assert "!secret/keep" in (tmp_git_repo.path / ".cursorignore").read_text(encoding="utf-8")


def test_merge_preserves_user_settings(firewall, tmp_git_repo):
    """Every user key and rule survives; the HACF rules and hook are added; a re-run is a no-op."""
    tmp_git_repo.write(".claude/settings.json", json.dumps(USER_SETTINGS))
    (tmp_git_repo.path / ".claude/settings.local.json").write_text("{not json", encoding="utf-8")
    proc = firewall("render", "--ides", "claude")
    assert proc.returncode == 0, proc.stderr
    merged = _settings(tmp_git_repo)
    for key in ("statusLine", "env", "attribution"):
        assert merged[key] == USER_SETTINGS[key]
    assert merged["permissions"]["allow"] == ["Bash(ls *)"]
    assert merged["permissions"]["deny"][0] == "Read(/secrets/**)"
    assert merged["hooks"]["SessionStart"] == USER_SETTINGS["hooks"]["SessionStart"]
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert set(golden["permissions"]["deny"]) <= set(merged["permissions"]["deny"])
    assert merged["permissions"]["ask"] == golden["permissions"]["ask"]
    assert merged["hooks"]["PreToolUse"] == golden["hooks"]["PreToolUse"]
    local = tmp_git_repo.path / ".claude/settings.local.json"
    assert local.read_text(encoding="utf-8") == "{not json"
    backups = [path for path in _backups(tmp_git_repo) if path.is_file()]
    assert len(backups) == 1 and json.loads(backups[0].read_text()) == USER_SETTINGS

    before = (tmp_git_repo.path / ".claude/settings.json").read_bytes()
    proc = firewall("render", "--ides", "claude")
    assert proc.returncode == 0 and "no change" in proc.stdout
    assert (tmp_git_repo.path / ".claude/settings.json").read_bytes() == before
    assert len([path for path in _backups(tmp_git_repo) if path.is_file()]) == 1


def test_merge_settings_fragment_keeps_user_values(firewall, tmp_git_repo):
    """A fragment fills gaps but never overrides a user value; it reports only what it added."""
    tmp_git_repo.write(".claude/settings.json", json.dumps(USER_SETTINGS))
    fragment = tmp_git_repo.path / "fragment.json"
    fragment.write_text(
        json.dumps(
            {
                "statusLine": {"type": "command", "command": "other"},
                "env": {"MY_VAR": "2", "NEW": "3"},
                "permissions": {"allow": ["Bash(ls *)", "Bash(touch *)"]},
            }
        ),
        encoding="utf-8",
    )
    proc = firewall("merge-settings", str(fragment))
    assert proc.returncode == 0, proc.stderr
    merged = _settings(tmp_git_repo)
    assert merged["statusLine"] == USER_SETTINGS["statusLine"]
    assert merged["env"] == {"MY_VAR": "1", "NEW": "3"}
    assert merged["permissions"]["allow"] == ["Bash(ls *)", "Bash(touch *)"]
    owned = json.loads(proc.stdout.split("owned: ")[1])
    assert owned["settings_rules"]["allow"] == ["Bash(touch *)"]


def test_removes_only_stale_hacf_rules(firewall, tmp_git_repo):
    """Only manifest-owned stale entries go, and only in buckets the fragment names."""
    old_hook = "python3 old_hook.py"
    settings = {
        "permissions": {
            "allow": ["Bash(owned-allow *)"],
            "deny": ["Read(/old/**)", "Read(/oldish/**)"],
        },
        "hooks": {
            "PreToolUse": [
                {"matcher": "Bash", "hooks": [{"type": "command", "command": old_hook}]},
                {"matcher": "Bash", "hooks": [{"type": "command", "command": "user_hook"}]},
            ]
        },
    }
    tmp_git_repo.write(".claude/settings.json", json.dumps(settings))

    assert firewall("render", "--ides", "claude").returncode == 0
    untouched = _settings(tmp_git_repo)
    assert "Read(/old/**)" in untouched["permissions"]["deny"]  # no manifest: nothing is removed
    assert any(old_hook in str(entry) for entry in untouched["hooks"]["PreToolUse"])

    tmp_git_repo.write(
        ".agents/hacf-manifest.json",
        json.dumps(
            {
                "settings_rules": {
                    "allow": ["Bash(owned-allow *)"],
                    "deny": ["Read(/old/**)", "Read(/spec/archive/**)"],
                },
                "hooks": {"PreToolUse": [old_hook]},
            }
        ),
    )
    proc = firewall("render", "--ides", "claude")
    assert proc.returncode == 0, proc.stderr
    merged = _settings(tmp_git_repo)
    assert "Read(/old/**)" not in merged["permissions"]["deny"]
    assert "Read(/oldish/**)" in merged["permissions"]["deny"]
    assert "Read(/spec/archive/**)" in merged["permissions"]["deny"]
    assert merged["permissions"]["allow"] == ["Bash(owned-allow *)"]  # a bucket render never names
    commands = [
        hook["command"] for entry in merged["hooks"]["PreToolUse"] for hook in entry["hooks"]
    ]
    assert old_hook not in commands and "user_hook" in commands
    assert hyper_firewall.HOOK_COMMAND in commands
    owned = json.loads(proc.stdout.split("owned: ")[1])
    assert "Read(/old/**)" not in owned["settings_rules"]["deny"]
    assert "Read(/oldish/**)" not in owned["settings_rules"]["deny"]


@pytest.mark.parametrize(
    "text",
    ['{"permissions": {"allow": [', '{"env": {}, "env": {}}', "[]", '{"permissions": []}'],
    ids=["truncated", "duplicate-key", "not-an-object", "bad-permissions"],
)
def test_invalid_json_aborts(firewall, tmp_git_repo, text):
    """Invalid or unusable settings exit 5, with the file and the backup dir unchanged."""
    tmp_git_repo.write(".claude/settings.json", text)
    proc = firewall("render", "--ides", "claude")
    assert proc.returncode == 5, proc.stdout + proc.stderr
    assert (tmp_git_repo.path / ".claude/settings.json").read_text(encoding="utf-8") == text
    assert _backups(tmp_git_repo) == []


def test_settings_symlink_refused(firewall, tmp_git_repo):
    """A symlinked settings file is refused rather than replaced by a regular file."""
    real = tmp_git_repo.path / "real.json"
    real.write_text("{}\n", encoding="utf-8")
    (tmp_git_repo.path / ".claude").mkdir()
    (tmp_git_repo.path / ".claude/settings.json").symlink_to(real)
    assert firewall("render", "--ides", "claude").returncode == 5
    assert real.read_text(encoding="utf-8") == "{}\n"


def test_ignore_blocks(firewall, tmp_git_repo):
    """Selected IDEs get exact markers; user lines, EOL, BOM and final newline are kept."""
    files = {
        ".cursorignore": b"user-line\n",
        ".rooignore": b"\xef\xbb\xbfroo-user\r\nsecond\r\n",
        ".codeiumignore": b"no-final-newline",
    }
    for name, data in files.items():
        (tmp_git_repo.path / name).write_bytes(data)
    proc = firewall("render", "--ides", "cursor,roo,windsurf,antigravity,cline,universal")
    assert proc.returncode == 0, proc.stderr
    path = tmp_git_repo.path
    begin, end = (
        b"# BEGIN HACF MANAGED BLOCK: agentignore",
        b"# END HACF MANAGED BLOCK: agentignore",
    )

    cursor = (path / ".cursorignore").read_bytes()
    assert cursor.startswith(b"user-line\n" + begin + b"\n")
    assert cursor.endswith(end + b"\n") and b"\nspec/archive/\n" in cursor

    roo = (path / ".rooignore").read_bytes()
    assert roo.startswith(b"\xef\xbb\xbfroo-user\r\nsecond\r\n" + begin + b"\r\n")
    assert roo.count(b"\n") == roo.count(b"\r\n")

    codeium = (path / ".codeiumignore").read_bytes()
    assert codeium.startswith(b"no-final-newline\n" + begin) and codeium.endswith(end)

    assert (path / ".geminiignore").read_bytes().startswith(begin + b"\n")
    assert not (path / ".clineignore").exists()
    assert not (path / ".claude").exists()
    for label in (".cursorignore", ".rooignore", ".codeiumignore", ".geminiignore"):
        assert f"{label} (best-effort" in proc.stdout
    assert "Block Ignored File Access" in proc.stdout

    snapshot = {name: (path / name).read_bytes() for name in files}
    assert firewall("render", "--ides", "cursor,roo,windsurf").returncode == 0
    assert {name: (path / name).read_bytes() for name in files} == snapshot


@pytest.mark.parametrize(
    "text",
    [
        "# BEGIN HACF MANAGED BLOCK: agentignore\nx\n",
        "x\n# END HACF MANAGED BLOCK: agentignore\n",
        "# BEGIN HACF MANAGED BLOCK: agentignore\n# BEGIN HACF MANAGED BLOCK: other\n"
        "# END HACF MANAGED BLOCK: other\n# END HACF MANAGED BLOCK: agentignore\n",
        "# BEGIN HACF MANAGED BLOCK: agentignore\n# END HACF MANAGED BLOCK: agentignore\n" * 2,
    ],
    ids=["no-end", "no-begin", "nested", "duplicate"],
)
def test_ignore_block_bad_markers_write_nothing(firewall, tmp_git_repo, text):
    """Bad markers in any target exit 5, and no other selected target is written either."""
    tmp_git_repo.write(".rooignore", text)
    proc = firewall("render", "--ides", "claude,cursor,roo")
    assert proc.returncode == 5, proc.stdout + proc.stderr
    assert (tmp_git_repo.path / ".rooignore").read_text(encoding="utf-8") == text
    assert not (tmp_git_repo.path / ".cursorignore").exists()
    assert not (tmp_git_repo.path / ".claude").exists()


def test_dry_run_writes_nothing(firewall, tmp_git_repo):
    """--dry-run reports the targets and writes none of them."""
    proc = firewall("render", "--ides", "claude,cursor", "--dry-run")
    assert proc.returncode == 0, proc.stderr
    assert "would write: .claude/settings.json" in proc.stdout
    assert not (tmp_git_repo.path / ".claude").exists()
    assert not (tmp_git_repo.path / ".cursorignore").exists()


def test_render_report_states_limits(firewall):
    """The report never claims a boundary and names the limits and remaining bypasses."""
    proc = firewall("render", "--ides", "claude")
    assert "not a security boundary" in proc.stdout
    assert "Python or Node subprocesses" in proc.stdout
    assert "forged ticket" in proc.stdout and "direct Python write" in proc.stdout


def test_unknown_ide_is_usage_error(firewall):
    """An unknown IDE id exits 2."""
    assert firewall("render", "--ides", "emacs").returncode == 2


def test_check_drift_and_broad_allows(firewall, tmp_git_repo):
    """Exit 1 on drift, 0 when rendered, and warn about broad allows with file:line."""
    settings = {"permissions": {"allow": ["Bash(git *)"]}}
    tmp_git_repo.write(".claude/settings.json", json.dumps(settings, indent=2))
    tmp_git_repo.write(
        ".claude/settings.local.json",
        json.dumps({"permissions": {"allow": ["Bash(python3 *)"]}}, indent=2),
    )
    assert firewall("check").returncode == 1

    assert firewall("render", "--ides", "claude,cursor").returncode == 0
    proc = firewall("check")
    assert proc.returncode == 0, proc.stdout
    assert "Bash(git *) in .claude/settings.json:4" in proc.stdout
    assert "Bash(python3 *) in .claude/settings.local.json:4" in proc.stdout

    tmp_git_repo.write(".agentignore", SHIPPED + "\nnew-dir/\n")
    proc = firewall("check")
    assert proc.returncode == 1
    assert ".claude/settings.json differs" in proc.stdout
    assert ".cursorignore differs" in proc.stdout


def test_check_without_claude_dir_has_no_claude_target(firewall):
    """A project with no .claude/ passes check; a missing .agentignore does not."""
    assert firewall("check").returncode == 0


def test_check_missing_agentignore(firewall, tmp_git_repo):
    """A missing .agentignore is drift for check, and render refuses without it."""
    (tmp_git_repo.path / ".agentignore").unlink()
    assert firewall("check").returncode == 1
    assert firewall("render").returncode == 5
