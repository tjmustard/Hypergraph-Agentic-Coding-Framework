"""openrig/hacf/roles.yml, bin/seat-rules.py and OPS.md: per-role tool denies, applied per seat.

Mission v063-measure-context slice 2 (OPR.99.0.7.2). The real files are read only; each tampered
case is a copy of roles.yml, rig.yaml and agents/hacf/agent.yaml in tmp_path, in the same layout,
so the script finds agent.yaml and rig.yaml beside the roles.yml it is given. No `rig` command runs.
"""

from __future__ import annotations

import json
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
RIG = REPO_ROOT / "openrig" / "hacf"
SCRIPT = RIG / "bin" / "seat-rules.py"
ROLES = RIG / "roles.yml"
OPS = RIG / "OPS.md"
FORBIDDEN = ("claude_permissions", "claude_mcp", "claude_settings_fragment", "claudeMdExcludes")
DENY = [
    "AskUserQuestion",
    "NotebookEdit",
    "EnterPlanMode",
    "ExitPlanMode",
    "EnterWorktree",
    "ExitWorktree",
]
MCP_NOTE = (
    "mcp: off is recorded, not applied: no rig seat verb sets MCP loading on an existing seat "
    "(OpenRig 0.7.21); unknown"
)


def run(*args: str) -> subprocess.CompletedProcess:
    """Run seat-rules.py with args under this interpreter."""
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], capture_output=True, text=True, timeout=60
    )


@pytest.fixture
def copy(tmp_path: Path) -> Path:
    """A copy of the three files the script reads, in the real layout. Returns its roles.yml."""
    dest = tmp_path / "hacf"
    (dest / "agents" / "hacf").mkdir(parents=True)
    shutil.copy2(ROLES, dest / "roles.yml")
    shutil.copy2(RIG / "rig.yaml", dest / "rig.yaml")
    shutil.copy2(RIG / "agents" / "hacf" / "agent.yaml", dest / "agents" / "hacf" / "agent.yaml")
    return dest / "roles.yml"


def edit_yaml(path: Path, change) -> None:
    """Load the YAML at path, apply change to it, and write it back."""
    data = yaml.safe_load(path.read_text())
    change(data)
    path.write_text(yaml.safe_dump(data, sort_keys=False))


def table() -> dict:
    """Return the real roles.yml."""
    return yaml.safe_load(ROLES.read_text())


# --- the real files ---------------------------------------------------------------------------


def test_check_passes_on_the_real_files() -> None:
    """--check passes on the real roles.yml, agent.yaml and rig.yaml."""
    result = run("--check")
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "ok"


def test_table_covers_the_seven_profiles() -> None:
    """roles.yml has the hacf.roles/v1 schema and exactly agent.yaml's seven profiles."""
    profiles = yaml.safe_load((RIG / "agents" / "hacf" / "agent.yaml").read_text())["profiles"]
    data = table()
    assert data["schema"] == "hacf.roles/v1"
    assert sorted(data["roles"]) == sorted(profiles)
    assert len(data["roles"]) == 7


def test_role_json_is_exactly_the_tables_denies() -> None:
    """--role execute --json prints only allow (empty) and the table's denies."""
    result = run("--role", "execute", "--json")
    assert result.returncode == 0, result.stderr
    rules = json.loads(result.stdout)
    assert rules == {"allow": [], "deny": table()["roles"]["execute"]["tools_deny"]}
    assert rules["deny"] == DENY


def test_every_role_denies_ask_user_question_and_keeps_the_work_tools() -> None:
    """Every role denies AskUserQuestion and none of the tools a seat works with."""
    kept = {
        "Agent",
        "Bash",
        "Edit",
        "Write",
        "Read",
        "Grep",
        "Glob",
        "Skill",
        "WebFetch",
        "WebSearch",
    }
    for name, entry in table()["roles"].items():
        rules = json.loads(run("--role", name, "--json").stdout)
        assert list(rules) == ["allow", "deny"], name
        assert rules["allow"] == [], name
        assert "AskUserQuestion" in rules["deny"], name
        assert not kept & set(rules["deny"]), name
        assert kept <= set(entry["tools_allow"]), name


def test_all_json_has_seven_roles() -> None:
    """--all --json prints the rules of all seven roles."""
    result = run("--all", "--json")
    assert result.returncode == 0, result.stderr
    rules = json.loads(result.stdout)
    assert len(rules) == 7
    assert all(r == {"allow": [], "deny": DENY} for r in rules.values())


def test_mcp_off_prints_the_stderr_line() -> None:
    """A role with mcp: off prints the not-applied line on stderr."""
    result = run("--role", "audit", "--json")
    assert result.returncode == 0
    assert result.stderr.strip() == MCP_NOTE


def test_real_rig_yaml_is_key_free() -> None:
    """The real rig.yaml holds none of the four per-member Claude keys."""
    text = (RIG / "rig.yaml").read_text()
    for key in FORBIDDEN:
        assert key not in text, key


def test_script_is_executable_in_the_repository() -> None:
    """seat-rules.py is executable on disk and, once tracked, in git's index."""
    assert SCRIPT.stat().st_mode & stat.S_IXUSR
    staged = subprocess.run(
        ["git", "ls-files", "-s", "--", "openrig/hacf/bin/seat-rules.py"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout
    if staged:  # tracked: git must record the executable bit too
        assert staged.startswith("100755"), staged


# --- the allowlist check on tampered copies ---------------------------------------------------


def check(roles: Path) -> subprocess.CompletedProcess:
    """Run --check against the roles.yml at roles."""
    return run("--roles", str(roles), "--check")


def test_extra_agent_skill_for_scribe_fails(copy: Path) -> None:
    """An agent.yaml skill the scribe role does not list is named."""
    agent = copy.parent / "agents" / "hacf" / "agent.yaml"
    edit_yaml(agent, lambda d: d["profiles"]["scribe"]["uses"]["skills"].append("sneaky-skill"))
    result = check(copy)
    assert result.returncode == 1
    assert "'scribe'" in result.stdout and "'sneaky-skill'" in result.stdout


def test_tool_in_both_lists_fails(copy: Path) -> None:
    """A tool in both tools_allow and tools_deny is named."""
    edit_yaml(copy, lambda d: d["roles"]["audit"]["tools_deny"].append("Bash"))
    result = check(copy)
    assert result.returncode == 1
    assert "role 'audit': 'Bash' is in both tools_allow and tools_deny" in result.stdout


def test_role_without_ask_user_question_fails(copy: Path) -> None:
    """A role that does not deny AskUserQuestion is named."""
    edit_yaml(copy, lambda d: d["roles"]["lead"]["tools_deny"].remove("AskUserQuestion"))
    result = check(copy)
    assert result.returncode == 1
    assert "role 'lead': tools_deny lacks AskUserQuestion" in result.stdout


@pytest.mark.parametrize("rule", ["mcp__x", "Bash(git push:*)"])
def test_mcp_or_parenthesized_deny_fails(copy: Path, rule: str) -> None:
    """An mcp__ or parenthesized deny is named."""
    edit_yaml(copy, lambda d: d["roles"]["resolve"]["tools_deny"].append(rule))
    result = check(copy)
    assert result.returncode == 1
    assert f"role 'resolve': rule '{rule}' is an MCP or parenthesized rule" in result.stdout


def test_missing_role_fails(copy: Path) -> None:
    """An agent.yaml profile with no role is named."""
    edit_yaml(copy, lambda d: d["roles"].pop("red-team"))
    result = check(copy)
    assert result.returncode == 1
    assert "agent.yaml profile 'red-team' has no role in roles.yml" in result.stdout


def test_role_not_in_agent_or_rig_fails(copy: Path) -> None:
    """A role that is no agent.yaml profile and no rig.yaml member is named."""
    edit_yaml(copy, lambda d: d["roles"].__setitem__("extra", dict(d["roles"]["lead"])))
    result = check(copy)
    assert result.returncode == 1
    assert "roles.yml role 'extra' is not a profile in agent.yaml" in result.stdout
    assert "roles.yml role 'extra' is used by no rig.yaml member" in result.stdout


def test_check_names_every_problem_at_once(copy: Path) -> None:
    """--check lists every problem, not only the first."""

    def change(d):
        """Break two roles at once."""
        d["roles"]["lead"]["tools_deny"].remove("AskUserQuestion")
        d["roles"]["audit"]["tools_deny"].append("mcp__x")

    edit_yaml(copy, change)
    lines = check(copy).stdout.splitlines()
    assert any("'lead'" in line for line in lines)
    assert any("'audit'" in line and "mcp__x" in line for line in lines)


# --- the key-free check ------------------------------------------------------------------------


def test_member_claude_mcp_fails(copy: Path) -> None:
    """claude_mcp on a member is a forbidden key."""
    edit_yaml(
        copy.parent / "rig.yaml",
        lambda d: d["pods"][1]["members"][0].__setitem__("claude_mcp", "none"),
    )
    result = check(copy)
    assert result.returncode == 1
    assert "rig.yaml.pods[1].members[0].claude_mcp: forbidden key" in result.stdout


def test_top_level_claude_permissions_fails(copy: Path) -> None:
    """claude_permissions at the top level is a forbidden key."""
    edit_yaml(
        copy.parent / "rig.yaml", lambda d: d.__setitem__("claude_permissions", {"deny": ["X"]})
    )
    result = check(copy)
    assert result.returncode == 1
    assert "rig.yaml.claude_permissions: forbidden key" in result.stdout


@pytest.mark.parametrize("key", ["claude_settings_fragment", "claudeMdExcludes"])
def test_other_forbidden_keys_fail(copy: Path, key: str) -> None:
    """The other two forbidden keys are named too."""
    edit_yaml(copy.parent / "rig.yaml", lambda d: d["pods"][0]["members"][0].__setitem__(key, {}))
    result = check(copy)
    assert result.returncode == 1
    assert f".{key}: forbidden key" in result.stdout


# --- refusals: exit 2 --------------------------------------------------------------------------


def test_unknown_role_exits_2() -> None:
    """An unknown role exits 2 with one stderr line."""
    result = run("--role", "janitor", "--json")
    assert result.returncode == 2
    assert result.stdout == ""
    assert len(result.stderr.strip().splitlines()) == 1
    assert "unknown role 'janitor'" in result.stderr


def test_wrong_schema_exits_2(copy: Path) -> None:
    """A wrong schema exits 2 for --role and --check."""
    edit_yaml(copy, lambda d: d.__setitem__("schema", "hacf.roles/v0"))
    for args in (("--role", "lead", "--json"), ("--check",)):
        result = run("--roles", str(copy), *args)
        assert result.returncode == 2, args
        assert "schema" in result.stderr and len(result.stderr.strip().splitlines()) == 1


def test_missing_file_exits_2(tmp_path: Path) -> None:
    """A missing roles.yml exits 2 in every mode."""
    for args in (("--role", "lead", "--json"), ("--all", "--json"), ("--check",)):
        result = run("--roles", str(tmp_path / "absent.yml"), *args)
        assert result.returncode == 2, args
        assert "cannot read roles.yml" in result.stderr


def test_invalid_yaml_exits_2(copy: Path) -> None:
    """A roles.yml that is not YAML exits 2."""
    copy.write_text("schema: [unclosed\n")
    result = run("--roles", str(copy), "--role", "lead", "--json")
    assert result.returncode == 2
    assert "not valid YAML" in result.stderr


# --- OPS.md -------------------------------------------------------------------------------------


def test_ops_md_holds_the_recipe() -> None:
    """OPS.md names the apply, launch, probe and rollback steps and every role."""
    text = OPS.read_text()
    for needle in (
        "rig seat set-permissions",
        "rig seat status",
        "rig seat launch",
        "--fresh --stop --reason",
        "--fresh --plan --json",
        "orch-lead goes last",
        "Turn-1 probe",
        "Rollback, per seat",
        "**`unknown`**",
        "contextUsage.totalInputTokens",
        "seat-rules.py --role <role> --json",
    ):
        assert needle in text, needle
    for role in table()["roles"]:
        assert f"| `{role}` |" in text, role


def test_ops_md_probe_does_not_expect_zero_mcp_tools() -> None:
    """The probe records mcp__ tools, not zero of them: the recipe leaves MCP alone."""
    text = OPS.read_text()
    flat = " ".join(text.split())
    assert "no `mcp__` tool" not in flat
    assert not re.search(r"Expected:[^.]*\bno\b[^.]*mcp__", flat)
    assert "Expected: none of the denied tools." in flat
    assert "Record whether any `mcp__` tool is listed" in flat
    assert "loaded or deferred (named only)" in flat


def test_ops_md_never_instructs_rig_up_or_down() -> None:
    """Every OPS.md line that mentions rig up or rig down also says never."""
    lines = [line for line in OPS.read_text().splitlines() if re.search(r"rig (up|down)\b", line)]
    assert lines, "OPS.md should say never to rig up / rig down"
    for line in lines:
        assert re.search(r"\bnever\b", line, re.IGNORECASE), line


def test_shipped_files_name_no_home_path() -> None:
    """The new shipped files name no home directory path."""
    for path in (ROLES, SCRIPT, OPS):
        assert "/" + "home/" not in path.read_text(), path
