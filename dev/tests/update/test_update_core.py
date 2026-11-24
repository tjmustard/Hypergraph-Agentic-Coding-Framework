"""/hyper-update (S13c): integrity policy, manifest per-file sync, blocks, retention.

Every test updates a temp project from a local bare upstream built from the working tree
(HACF_TEST=1, file://); none writes inside the HACF checkout (MiniPRD_24 §5).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from update.conftest import CONSENT_REL, TRUST_REL, Harness, sha_file, snapshot

UNSIGNED_PROMPT = "Update from the UNSIGNED upstream"
ADOPT_PROMPT = "Adopt the proposed signer set"


def consent_lines(project: Path) -> list[dict]:
    """Return the parsed lines of the consent log (empty when there is none)."""
    path = project / CONSENT_REL
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def pin(project: Path, signers: list[str]) -> None:
    """Write a signer pin into the project."""
    path = project / TRUST_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"allowed_signers": signers}) + "\n", encoding="utf-8")


# Test 1
def test_unsigned_upstream_requires_consent(hx: Harness):
    """No pinned signers: refused without a TTY (nothing written); consented on a TTY."""
    up = hx.derive({".agents/skills/hyper-update/NOTE.md": "new upstream file\n"})
    project = hx.project()
    before = snapshot(project)
    result = hx.run(project, "--upstream-url", up.url)
    assert result.returncode == 1, result.output
    assert "unsigned" in result.report["reason"]
    assert snapshot(project) == before

    result = hx.run_tty(project, "--upstream-url", up.url, answers=[(UNSIGNED_PROMPT, "y")])
    assert result.returncode == 0, result.output
    assert f"{up.url} @ {up.sha}" in result.output  # the prompt names the URL and commit (R12)
    assert result.report["integrity"] == "unsigned-consent-interactive"
    assert (project / ".agents/skills/hyper-update/NOTE.md").read_text() == "new upstream file\n"
    lines = consent_lines(project)
    assert len(lines) == 1 and lines[0]["mode"] == "interactive"


def test_unsigned_consent_declined_writes_nothing(hx: Harness):
    """Answering no on the TTY stops the update before any write."""
    project = hx.project()
    before = snapshot(project)
    result = hx.run_tty(project, "--upstream-url", hx.base.url, answers=[(UNSIGNED_PROMPT, "n")])
    assert result.returncode == 1
    assert snapshot(project) == before


# Test 2
def test_allow_unsigned_refused_without_tty_or_autonomous(hx: Harness):
    """--allow-unsigned: refused without a TTY and under HACF_AUTONOMOUS=1; else mode flag."""
    project = hx.project()
    before = snapshot(project)
    result = hx.run(project, "--upstream-url", hx.base.url, "--allow-unsigned")
    assert result.returncode == 1 and "without a TTY" in result.report["reason"]
    result = hx.run_tty(
        project, "--upstream-url", hx.base.url, "--allow-unsigned", env={"HACF_AUTONOMOUS": "1"}
    )
    assert result.returncode == 1 and "HACF_AUTONOMOUS=1" in result.report["reason"]
    assert snapshot(project) == before

    result = hx.run_tty(project, "--upstream-url", hx.base.url, "--allow-unsigned")
    assert result.returncode == 0, result.output
    assert UNSIGNED_PROMPT not in result.output
    assert result.report["integrity"] == "unsigned-consent-flag"
    assert [line["mode"] for line in consent_lines(project)] == ["flag"]


# Test 3
def test_consent_log_fields(hx: Harness):
    """The consent line holds exactly timestamp (+00:00), url, commit, mode and os_user."""
    project = hx.project()
    result = hx.run_tty(project, "--upstream-url", hx.base.url, answers=[(UNSIGNED_PROMPT, "y")])
    assert result.returncode == 0, result.output
    last = consent_lines(project)[-1]
    assert set(last) == {"timestamp", "url", "commit", "mode", "os_user"}
    assert last["timestamp"].endswith("+00:00")
    assert last["url"] == hx.base.url
    assert last["commit"] == hx.base.sha
    assert last["mode"] == "interactive"
    assert last["os_user"]
    log = (project / ".agents/logs/hyper-update.log").read_text().splitlines()[-1]
    assert json.loads(log)["integrity"] == "unsigned-consent-interactive"


# Test 4
def test_ssh_signed_upstream_verified(hx: Harness):
    """A commit signed by the pinned key passes with no prompt; another key is refused."""
    key, public = hx.ssh_key("good")
    other, _ = hx.ssh_key("other")
    signed = hx.derive({"README.md": "signed\n"}, sign_key=key)
    project = hx.project()
    pin(project, [public])
    result = hx.run(project, "--upstream-url", signed.url)
    assert result.returncode == 0, result.output
    assert result.report["integrity"] == "ssh-verified"
    assert any(
        a.startswith("gpg.ssh.allowedSignersFile=") for a in result.report["verification_argv"]
    )
    assert "-c" in result.report["verification_argv"]
    assert UNSIGNED_PROMPT not in result.output
    assert consent_lines(project) == []

    project = hx.project()
    pin(project, [public])
    before = snapshot(project)
    forged = hx.derive({"README.md": "forged\n"}, sign_key=other)
    result = hx.run_tty(
        project, "--upstream-url", forged.url, "--allow-unsigned", answers=[(ADOPT_PROMPT, "n")]
    )
    assert result.returncode == 1, result.output
    assert "no valid signature" in result.report["reason"]
    assert snapshot(project) == before

    # An OpenPGP signature is refused against an SSH pin even when the user's keyring holds the
    # key: `git verify-commit` checks a signature by its own format, not the pinned one (R11).
    home = hx.gpg_key()
    gpg_signed = hx.derive({"README.md": "gpg\n"}, gpg_home=home)
    keyring_ok = subprocess.run(
        ["git", "verify-commit", gpg_signed.sha],
        cwd=gpg_signed.bare,
        env=dict(hx.env, GNUPGHOME=str(home)),
        capture_output=True,
        check=False,
    )
    assert keyring_ok.returncode == 0, keyring_ok.stderr  # the keyring would accept it
    project = hx.project()
    pin(project, [public])
    before = snapshot(project)
    result = hx.run(project, "--upstream-url", gpg_signed.url, "-y", env={"GNUPGHOME": str(home)})
    assert result.returncode == 1, result.output
    assert result.report["signature"] == "invalid"
    assert result.report.get("integrity") is None
    assert "only an SSH signature by a pinned key" in result.report["reason"]
    assert snapshot(project) == before


# Test 5
def test_signers_pinned_tofu(hx: Harness):
    """An empty payload signer list never replaces the pin without interactive consent."""
    key, public = hx.ssh_key()
    unsigned = hx.derive({"README.md": "unsigned\n"})  # payload upstream.yml: allowed_signers []
    project = hx.project()
    pin(project, [public])
    pinned = (project / TRUST_REL).read_bytes()
    result = hx.run(project, "--upstream-url", unsigned.url, "-y")
    assert result.returncode == 1, result.output
    assert "no valid signature" in result.report["reason"]
    assert (project / TRUST_REL).read_bytes() == pinned

    result = hx.run_tty(
        project,
        "--upstream-url",
        unsigned.url,
        answers=[(ADOPT_PROMPT, "y"), (UNSIGNED_PROMPT, "y")],
    )
    assert result.returncode == 0, result.output
    assert f"- hacf-upstream {public}" in result.output or f"- {public}" in result.output
    assert json.loads((project / TRUST_REL).read_text())["allowed_signers"] == []

    fresh = hx.project()
    result = hx.run_tty(fresh, "--upstream-url", unsigned.url, answers=[(UNSIGNED_PROMPT, "y")])
    assert result.returncode == 0, result.output
    assert result.report["signer_pin"] == "first-use"
    assert json.loads((fresh / TRUST_REL).read_text())["allowed_signers"] == []


# Test 6
def test_upstream_url_env_ignored_without_hacf_test(hx: Harness):
    """HACF_UPSTREAM_URL needs HACF_TEST=1; --upstream-url wins; file:// needs HACF_TEST=1."""
    project = hx.project()
    config = project / ".agents/config/upstream.yml"
    unreachable = "https://127.0.0.1:9/hacf.git"
    config.write_text(config.read_text().replace(
        "https://github.com/tjmustard/Hypergraph-Agentic-Coding-Framework.git", unreachable
    ))  # fmt: skip
    hx.commit(project)
    result = hx.run(project, "--dry-run", env={"HACF_TEST": None, "HACF_UPSTREAM_URL": hx.base.url})
    assert result.report["url"] == unreachable
    assert f"Upstream URL: {unreachable}" in result.output
    assert "HACF_UPSTREAM_URL is ignored without HACF_TEST=1" in result.output

    result = hx.run(project, "--dry-run", env={"HACF_UPSTREAM_URL": hx.base.url})
    assert result.returncode == 0, result.output
    assert result.report["url"] == hx.base.url

    other = hx.derive({"README.md": "other\n"})
    result = hx.run(
        project, "--dry-run", "--upstream-url", other.url, env={"HACF_UPSTREAM_URL": hx.base.url}
    )
    assert result.returncode == 0, result.output
    assert result.report["url"] == other.url and result.report["commit_sha"] == other.sha

    result = hx.run(project, "--dry-run", "--upstream-url", other.url, env={"HACF_TEST": None})
    assert result.returncode == 1
    assert "is not https://" in result.report["reason"]


# Test 7
def test_downgrade_refused_without_flag(hx: Harness):
    """installed_commit newer than the upstream ref: refused, unless --allow-downgrade."""
    newer = hx.derive({"README.md": "newer\n"}, branch="next")
    project = hx.project()
    manifest_path = project / ".agents/hacf-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["installed_commit"] = newer.sha
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    hx.commit(project)
    before = snapshot(project)
    result = hx.run_tty(project, "--upstream-url", newer.url, "--allow-unsigned", "-y")
    assert result.returncode == 1, result.output
    assert "--allow-downgrade" in result.report["reason"]
    assert snapshot(project) == before

    result = hx.run_tty(
        project, "--upstream-url", newer.url, "--allow-unsigned", "--allow-downgrade", "-y"
    )
    assert result.returncode == 0, result.output
    assert json.loads(manifest_path.read_text())["installed_commit"] == hx.base.sha


# Test 8
def test_unknown_sha_agents_md_not_appended(hx: Harness):
    """A customized whole-file AGENTS.md stays byte-identical; the block goes to a sidecar."""
    custom = "# My project rules\n\nUse tabs. Never push on Fridays.\n"
    project = hx.project({"AGENTS.md": custom, "CLAUDE.local.md": "mine\n"})
    result = hx.run_tty(project, "--upstream-url", hx.base.url, "--allow-unsigned", "-y")
    assert result.returncode == 0, result.output
    assert (project / "AGENTS.md").read_text() == custom
    assert (project / "CLAUDE.local.md").read_text() == "mine\n"
    sidecar = project / "AGENTS.hacf-v0.6.0.md"
    assert sidecar.exists()
    assert "<!-- BEGIN HACF MANAGED BLOCK: hacf -->" in sidecar.read_text()
    lines = [r for r in result.report["instruction_files"] if r["path"] == "AGENTS.md"]
    assert len(lines) == 1 and lines[0]["line"].startswith("sidecar: ")


def test_marked_agents_md_only_block_changes(hx: Harness):
    """An AGENTS.md with HACF and OpenRig blocks: only the HACF block body changes (R26)."""
    head = "# Team notes\n\n<!-- BEGIN OpenRig MANAGED BLOCK: role -->\nrig text\n"
    head += "<!-- END OpenRig MANAGED BLOCK: role -->\n\n"
    text = head + "<!-- BEGIN HACF MANAGED BLOCK: hacf -->\nold body\n"
    text += "<!-- END HACF MANAGED BLOCK: hacf -->\n\ntail text\n"
    project = hx.project({"AGENTS.md": text})
    result = hx.run_tty(project, "--upstream-url", hx.base.url, "--allow-unsigned", "-y")
    assert result.returncode == 0, result.output
    new = (project / "AGENTS.md").read_text()
    assert new.startswith(head + "<!-- BEGIN HACF MANAGED BLOCK: hacf -->\n")
    assert new.endswith("<!-- END HACF MANAGED BLOCK: hacf -->\n\ntail text\n")
    assert "old body" not in new


def test_dry_run_never_runs_upstream_code(hx: Harness, tmp_path: Path):
    """A dry run plans the migration with the installed hyper_migrate.py, never the clone's.

    The dry run is the read-only step the skill runs autonomously, before any consent or
    verification (Task 21, §4, question 2), so the upstream's code must not execute.
    """
    marker = tmp_path / "upstream-migrate-ran"
    evil = hx.derive(
        {
            ".agents/scripts/hyper_migrate.py": (
                f"import pathlib\npathlib.Path({str(marker)!r}).write_text('ran')\n"
                "print('UPSTREAM PLAN')\n"
            )
        }
    )
    project = hx.project()
    before = snapshot(project)
    for env in ({"HACF_AUTONOMOUS": "1"}, {}):
        result = hx.run(project, "--upstream-url", evil.url, "--dry-run", env=env)
        assert result.returncode == 0, result.output
        assert result.report["status"] == "dry-run"
        assert result.report["signature"] == "unsigned"
        assert result.report["migration"]["ran"] is True
        assert result.report["migration"]["script"] == "installed"
        assert "UPSTREAM PLAN" not in result.output
        assert not marker.exists()
    assert snapshot(project) == before


# Test 9
def test_missing_config_uses_embedded_defaults(hx: Harness):
    """No upstream.yml: embedded defaults and the install notice; a dry run writes nothing."""
    project = hx.project()
    (project / ".agents/config/upstream.yml").unlink()
    hx.commit(project)
    before = snapshot(project)
    result = hx.run(project, "--dry-run", "--upstream-url", hx.base.url)
    assert result.returncode == 0, result.output
    assert "run HACF-install.sh to complete the upgrade" in result.output
    assert result.report["config_source"] == "embedded-defaults"
    assert result.report["ref"] == "main"
    assert result.report["status"] == "dry-run"
    assert result.report["migration"]["ran"] is True
    assert snapshot(project) == before


# Test 10
def test_preserves_user_spec_tests(hx: Harness):
    """Upstream ships spec/ and tests/: the project's files there stay byte-identical."""
    up = hx.derive(
        {
            "spec/compiled/architecture.yml": "upstream graph\n",
            "spec/active/Draft_PRD.md": "upstream draft\n",
            "tests/fixtures/sample.txt": "upstream fixture\n",
        }
    )
    mine = {
        "spec/compiled/architecture.yml": "my graph\n",
        "spec/compiled/MiniPRD_Mine.md": "my mini prd\n",
        "tests/fixtures/sample.txt": "my fixture\n",
        "tests/test_mine.py": "def test_x():\n    pass\n",
    }
    project = hx.project(mine)
    before = snapshot(project)
    result = hx.run_tty(project, "--upstream-url", up.url, "--allow-unsigned", "-y")
    assert result.returncode == 0, result.output
    after = snapshot(project)
    for rel in before:
        if rel.startswith(("spec/", "tests/")):
            assert after[rel] == before[rel], rel
    assert not (project / "spec/active/Draft_PRD.md").exists()


# Test 11
def test_user_skill_dirs_preserved(hx: Harness):
    """User and OpenRig-projected skill dirs survive; a retired, unmodified HACF file goes."""
    retired = ".agents/skills/hyper-update/META.yml"
    up = hx.derive({retired: None, ".agents/scripts/hyper_blocks.py": None})
    project = hx.project(
        {
            ".agents/skills/my-skill/SKILL.md": "---\nname: my-skill\n---\nmine\n",
            ".agents/skills/openrig-projected/SKILL.md": "---\nname: openrig\n---\nrig\n",
        }
    )
    scripts = project / ".agents/scripts/hyper_blocks.py"
    scripts.write_text(scripts.read_text() + "# local edit\n")
    hx.commit(project)
    before = snapshot(project)
    result = hx.run_tty(project, "--upstream-url", up.url, "--allow-unsigned", "-y")
    assert result.returncode == 0, result.output
    for rel in (".agents/skills/my-skill/SKILL.md", ".agents/skills/openrig-projected/SKILL.md"):
        assert (project / rel).read_bytes() == before[rel]
    assert not (project / retired).exists()
    assert scripts.read_bytes() == before[".agents/scripts/hyper_blocks.py"]  # modified: kept
    manifest = json.loads((project / ".agents/hacf-manifest.json").read_text())
    assert retired not in manifest["files"]
    assert manifest["installed_commit"] == up.sha


def test_modified_framework_file_backed_up(hx: Harness):
    """A locally modified framework file is backed up before it is overwritten."""
    rel = ".agents/skills/hyper-update/SKILL.md"
    up = hx.derive({rel: "upstream skill text\n"})
    project = hx.project()
    (project / rel).write_text("my local skill edit\n")
    hx.commit(project)
    result = hx.run_tty(project, "--upstream-url", up.url, "--allow-unsigned", "-y")
    assert result.returncode == 0, result.output
    assert (project / rel).read_text() == "upstream skill text\n"
    backup = project / result.report["backup"] / rel
    assert backup.read_text() == "my local skill edit\n"
    manifest = json.loads((project / ".agents/hacf-manifest.json").read_text())
    assert manifest["files"][rel]["sha256"] == sha_file(project / rel)


# Test 12
def test_retention_exempts_migration(tmp_path: Path):
    """Only the expired exact-timestamp backup dir is removed."""
    from hyper_update_verification import UpstreamVerification

    root = tmp_path / "project"
    backups = root / ".agents/.backup"
    now = datetime(2026, 11, 18, 12, 0, 0, tzinfo=timezone.utc)
    old = (now - timedelta(days=45)).strftime("%Y-%m-%dT%H-%M-%SZ")
    recent = (now - timedelta(days=3)).strftime("%Y-%m-%dT%H-%M-%SZ")
    names = [old, recent, f"{old}-migration", "odd-name", "2026-13-99T99-99-99Z", "2026-01-01"]
    for name in names:
        (backups / name).mkdir(parents=True)
        (backups / name / "f.txt").write_text("x\n")
    migration = root / ".agents/state/migrations" / old / "backup"
    migration.mkdir(parents=True)
    (migration / "f.txt").write_text("x\n")
    count, _ = UpstreamVerification(str(root)).cleanup_old_backups(now=now)
    assert count == 2  # the expired timestamp dir and the expired old-format day dir
    left = sorted(p.name for p in backups.iterdir())
    assert left == sorted([recent, f"{old}-migration", "odd-name", "2026-13-99T99-99-99Z"])
    assert (migration / "f.txt").exists()


def test_retention_keeps_only_exact_names_from_an_update(hx: Harness):
    """An update's retention leaves *-migration dirs and odd names in place."""
    project = hx.project()
    for name in ("2020-01-01T00-00-00Z", "2020-01-01T00-00-00Z-migration", "keep-me"):
        (project / ".agents/.backup" / name).mkdir(parents=True)
    result = hx.run_tty(project, "--upstream-url", hx.base.url, "--allow-unsigned", "-y")
    assert result.returncode == 0, result.output
    left = sorted(p.name for p in (project / ".agents/.backup").iterdir())
    assert "2020-01-01T00-00-00Z" not in left
    assert {"2020-01-01T00-00-00Z-migration", "keep-me"} <= set(left)
    shutil.rmtree(project)


def test_default_upstream_url_agrees_everywhere() -> None:
    """The shipped upstream.yml repo_url, hyper_update_core's default and the installer's agree."""
    import re

    import hyper_update_core
    import yaml
    from conftest import REPO_ROOT

    shipped = yaml.safe_load((REPO_ROOT / ".agents/config/upstream.yml").read_text())["repo_url"]
    installer = (REPO_ROOT / "HACF-install.sh").read_text(encoding="utf-8")
    default = re.search(r'^DEFAULT_REPO_URL="([^"]+)"$', installer, re.M)
    assert default is not None
    assert shipped == hyper_update_core.DEFAULT_CONFIG["repo_url"] == default.group(1)
    assert shipped.endswith("/Hypergraph-Agentic-Coding-Framework.git")
