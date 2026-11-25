"""Tests of dev/release_export.py's `profile check` and `build` (release-export slice 2).

Offline: every repository is synthetic, under tmp_path. A fake base and three fake releases (each
with a CHANGELOG section, a `lore/` dir, an excluded test file and a NEVER_COPY-shaped installer
stub), an overlay commit after the last release, and a bare repository standing in for the remote.
Every command of the tool runs through `tool()`, which asserts that the source repository's HEAD,
`git status --porcelain` and index bytes are the same afterwards.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
TOOL = REPO_ROOT / "dev" / "release_export.py"
NAME = "Export Owner Ph.D."
BASE_EMAIL = "owner@example.com"

# Deliberately varied timezones, so the tests can tell a kept raw date from a converted one.
DATES = {
    "base": ("1690000000 -0700", "1690000100 -0700"),
    "0.1.0": ("1700000000 +0530", "1700000500 -0800"),
    "0.2.0": ("1710000000 +0100", "1710000600 +0900"),
    "0.3.0": ("1720000000 -0330", "1720000700 +0000"),
    "overlay": ("1730000000 +0200", "1730000100 +0200"),
}

# A valid v2 scan section (slice 4): the tool's two home roots as literal rows (built here, so
# this file holds neither), the raw-only keys, a small email allowlist and one advisory pattern.
HOME_ROOT = "/" + "home/"
USERS_ROOT = "/" + "Users/"
RAW_KEYS = (
    "apiKeySource credential_expires_at five_hour isUsingOverage messaging_socket_path".split()
)
RAW_KEYS += "overageDisabledReason overageStatus rate_limit_event rate_limit_info".split()
RAW_KEYS += "request_id seven_day unifiedWindows".split()
SCAN = {
    "strict": [
        {"label": "home-dir", "literal": HOME_ROOT},
        {"label": "users-dir", "literal": USERS_ROOT},
    ],
    "strict_keys": RAW_KEYS,
    "email_allowlist": ["example.com", "example.org", "example.net", "*.invalid"],
    "advisory": [{"label": "initials", "regex": r"\bTJ\b"}],
}


def clean_env(**extra: str) -> dict[str, str]:
    """A plain git environment for the tests' own setup calls."""
    env = {
        "PATH": os.environ["PATH"],
        "HOME": os.environ.get("HOME", "/nonexistent"),
        "LANG": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
    }
    env.update(extra)
    return env


def g(repo: Path, *args: str, env: dict[str, str] | None = None, check: bool = True) -> str:
    """Run a git command for the tests' own setup; return its stripped output."""
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        env=env or clean_env(),
        check=False,
    )
    if check and proc.returncode != 0:
        raise AssertionError(f"git {args}: {proc.stderr}")
    return proc.stdout.strip()


def message_of(repo: Path, sha: str) -> str:
    """A commit's message, byte-exact (g() strips the output)."""
    out = subprocess.run(
        ["git", "-C", str(repo), "cat-file", "commit", sha],
        capture_output=True,
        text=True,
        env=clean_env(),
        check=True,
    ).stdout
    return out.split("\n\n", 1)[1]


def commit_all(repo: Path, message: str, dates: tuple[str, str], who: str = "Dev") -> str:
    """Commit the whole working tree with fixed identities and dates; return the sha."""
    env = clean_env(
        GIT_AUTHOR_NAME=who,
        GIT_AUTHOR_EMAIL=BASE_EMAIL if who == "Owner" else "dev@example.org",
        GIT_AUTHOR_DATE=dates[0],
        GIT_COMMITTER_NAME=who,
        GIT_COMMITTER_EMAIL="dev@example.org",
        GIT_COMMITTER_DATE=dates[1],
    )
    g(repo, "add", "-A", env=env)
    g(repo, "commit", "-q", "--allow-empty", "-m", message, env=env)
    return g(repo, "rev-parse", "HEAD")


def write(repo: Path, rel: str, text: str) -> None:
    """Write a file under `repo`, creating its directories."""
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def changelog(versions: list[str]) -> str:
    """A CHANGELOG with one section per released version, newest first."""
    out = "# Changelog\n\n## [Unreleased]\n\n"
    for v in reversed(versions):
        out += f"## [{v}] - 2026-01-0{len(v)}\n\n### Added\n\n- Feature of {v}.\n\n\n"
    return out + "## [0.0.9] - 2025-12-01\n\n- The base.\n"


@dataclass
class World:
    """The synthetic private repository, its remote and the commits the profile names."""

    tmp: Path
    src: Path
    remote: Path
    base: str
    sources: dict[str, str]
    overlay: str
    side: str  # a commit on a side branch: not a descendant of any release

    def profile(self, **changes) -> Path:
        """Write a profile for this world, with top-level `changes` applied; return its path."""
        doc = {
            "schema": "hacf.release-export/v2",
            "required_digest_labels": [],
            "base": self.base,
            "identity": {"name": NAME, "email_from": "base"},
            "releases": [
                {"version": "0.1.0", "source": self.sources["0.1.0"]},
                {"version": "0.2.0", "source": self.sources["0.2.0"]},
                {
                    "version": "0.3.0",
                    "source": self.sources["0.3.0"],
                    "overlay": {"commit": self.overlay, "paths": ["README.md", "dev/tool.sh"]},
                },
            ],
            "exclude": [
                {"path": "lore/", "reason": "private records", "versions": "all"},
                {"path": "dev/tests/test_private.py", "reason": "private", "versions": "all"},
                {"path": "late.txt", "reason": "from 0.2.0", "versions": ">=0.2.0"},
            ],
            "expected_skips": [
                {"versions": "all", "nodeid": "dev/tests/t.py::test_x", "reason_contains": "x"}
            ],
            "top_level": ["CHANGELOG.md", "HACF-install.sh", "README.md", "app.py", "dev"],
            "scan": SCAN,
            "allowlist": [],
        }
        doc.update(changes)
        path = self.tmp / f"profile-{len(list(self.tmp.glob('profile-*')))}.yml"
        path.write_text(yaml.safe_dump(doc, sort_keys=False))
        return path


@pytest.fixture
def world(tmp_path: Path) -> World:
    """The synthetic world."""
    return make_world(tmp_path)


def make_world(tmp_path: Path) -> World:
    """Build the synthetic world."""
    src = tmp_path / "src"
    g(tmp_path, "init", "-q", "-b", "main", str(src))
    write(src, "README.md", "base readme\n")
    write(src, "CHANGELOG.md", changelog([]))
    write(src, "app.py", "print('base')\n")
    base = commit_all(src, "base", DATES["base"], who="Owner")
    sources = {}
    released: list[str] = []
    for n, v in enumerate(["0.1.0", "0.2.0", "0.3.0"], start=1):
        released.append(v)
        write(src, "CHANGELOG.md", changelog(released))
        write(src, "app.py", f"print('{v}')\n")
        write(src, "lore/MAP.md", f"private map {v}\n")
        write(src, "lore/adr/0001.md", "private adr\n")
        write(src, "dev/tests/test_private.py", "# reads private history\n")
        write(src, "dev/tests/test_public.py", f"def test_ok():\n    assert {n}\n")
        write(src, "dev/tool.sh", f"echo tool {v}\n")
        write(src, "late.txt", f"late {v}\n")
        write(src, "HACF-install.sh", 'NEVER_COPY=("lore/" "missions/")\n')
        sources[v] = commit_all(src, f"release {v}", DATES[v])
    write(src, "README.md", "overlaid readme\n")
    write(src, "dev/tool.sh", "echo fixed tool\n")
    overlay = commit_all(src, "overlay fixes", DATES["overlay"])
    g(src, "checkout", "-q", "-b", "side", base)
    write(src, "app.py", "print('side')\n")
    write(src, "CHANGELOG.md", changelog(["0.1.0", "0.2.0", "0.3.0"]))
    side = commit_all(src, "side", DATES["0.3.0"])
    g(src, "checkout", "-q", "main")
    write(src, "scratch.txt", "an untracked file\n")
    write(src, "app.py", "print('a working-tree edit')\n")
    remote = tmp_path / "remote.git"
    g(tmp_path, "init", "-q", "--bare", str(remote))
    g(src, "remote", "add", "origin", str(remote))
    g(src, "push", "-q", "origin", f"{base}:refs/heads/main")
    return World(tmp_path, src, remote, base, sources, overlay, side)


def snapshot(repo: Path) -> tuple[str, str, bytes]:
    """The repository's HEAD, porcelain status and index bytes."""
    head = g(repo, "rev-parse", "HEAD")
    status = g(repo, "--no-optional-locks", "status", "--porcelain")
    return head, status, (repo / ".git" / "index").read_bytes()


def tool(
    world: World, *args: str, profile: Path | None = None, repo: Path | None = None, env=None
) -> subprocess.CompletedProcess[str]:
    """Run the tool and assert the repository's HEAD, status and index bytes are unchanged."""
    repo = repo or world.src
    before = snapshot(repo)
    argv = [sys.executable, str(TOOL), "--repo", str(repo)]
    if profile is not None:
        argv += ["--profile", str(profile)]
    proc = subprocess.run(
        [*argv, *args], capture_output=True, text=True, env=env or os.environ.copy(), check=False
    )
    assert snapshot(repo) == before, "the tool changed HEAD, the status or the index"
    return proc


def build(world: World, version: str, profile: Path, *extra: str, **kw):
    """Run `build --version <version>` through `tool()`."""
    return tool(world, "build", "--version", version, *extra, profile=profile, **kw)


def created(proc: subprocess.CompletedProcess[str]) -> tuple[str, str]:
    """Assert a `created` line; return the commit and tag shas."""
    assert proc.returncode == 0, proc.stderr
    word, commit, tag = proc.stdout.split()
    assert word == "created"
    return commit, tag


def refused(proc: subprocess.CompletedProcess[str], text: str) -> None:
    """Assert a one-line refusal with exit 2 that contains `text`."""
    assert proc.returncode == 2, (proc.stdout, proc.stderr)
    assert proc.stdout == ""
    assert len(proc.stderr.strip().splitlines()) == 1
    assert text in proc.stderr


def files(repo: Path, treeish: str) -> dict[str, str]:
    """A tree's file paths mapped to their blob shas."""
    out = g(repo, "ls-tree", "-r", "--full-tree", treeish)
    return {line.split("\t")[1]: line.split()[2] for line in out.splitlines()}


def header(repo: Path, sha: str) -> dict[str, list[str]]:
    """A commit's header fields."""
    head = g(repo, "cat-file", "-p", sha).split("\n\n", 1)[0]
    fields: dict[str, list[str]] = {}
    for line in head.splitlines():
        key, _, rest = line.partition(" ")
        fields.setdefault(key, []).append(rest)
    return fields


def build_all(world: World, profile: Path) -> dict[str, tuple[str, str]]:
    """Build the three releases in order; return their commit and tag shas."""
    return {v: created(build(world, v, profile)) for v in ("0.1.0", "0.2.0", "0.3.0")}


# ---------------------------------------------------------------------------------- builds


def test_first_build(world: World) -> None:
    """The first export: parent = base, filtered tree, exact message, identity, dates, tag."""
    commit, tag = created(build(world, "0.1.0", world.profile()))
    src = world.sources["0.1.0"]
    fields = header(world.src, commit)
    assert fields["parent"] == [world.base]
    assert fields["author"] == [f"{NAME} <{BASE_EMAIL}> {DATES['0.1.0'][0]}"]
    assert fields["committer"] == [f"{NAME} <{BASE_EMAIL}> {DATES['0.1.0'][1]}"]
    expected = {
        p: b
        for p, b in files(world.src, src).items()
        if not p.startswith("lore/") and p != "dev/tests/test_private.py"
    }
    assert files(world.src, commit) == expected
    assert "late.txt" in expected  # its row starts at 0.2.0
    message = message_of(world.src, commit)
    assert message == (
        "## [0.1.0] - 2026-01-05\n\n### Added\n\n- Feature of 0.1.0.\n\n"
        f"HACF-Source-Commit: {src}\n"
    )
    assert g(world.src, "cat-file", "-t", tag) == "tag"
    assert g(world.src, "cat-file", "-p", tag) == (
        f"object {commit}\ntype commit\ntag v0.1.0\n"
        f"tagger {NAME} <{BASE_EMAIL}> {DATES['0.1.0'][1]}\n\nHACF v0.1.0"
    )
    assert g(world.src, "rev-parse", "refs/tags/v0.1.0") == tag
    assert g(world.src, "rev-parse", "v0.1.0^{commit}") == commit
    assert g(world.src, "rev-parse", "refs/heads/public/main") == commit
    assert g(world.src, "symbolic-ref", "HEAD") == "refs/heads/main"


def test_chain_overlay_and_version_selectors(world: World) -> None:
    """Later exports chain; >= rows apply from their version; overlay blobs and trailers."""
    shas = build_all(world, world.profile())
    c2, c3 = shas["0.2.0"][0], shas["0.3.0"][0]
    assert header(world.src, c2)["parent"] == [shas["0.1.0"][0]]
    assert header(world.src, c3)["parent"] == [c2]
    assert "late.txt" not in files(world.src, c2)
    tree3 = files(world.src, c3)
    over = files(world.src, world.overlay)
    assert tree3["README.md"] == over["README.md"]
    assert tree3["dev/tool.sh"] == over["dev/tool.sh"]
    assert tree3["app.py"] == files(world.src, world.sources["0.3.0"])["app.py"]
    message = message_of(world.src, c3)
    assert message.endswith(
        f"\n\nHACF-Source-Commit: {world.sources['0.3.0']}\n"
        f"HACF-Overlay: {world.overlay} README.md\n"
        f"HACF-Overlay: {world.overlay} dev/tool.sh\n"
    )
    assert g(world.src, "rev-parse", "public/main") == c3
    assert g(world.src, "rev-list", "--count", "public/main") == "4"  # base + three exports


def test_null_or_absent_overlay_refuses(world: World) -> None:
    """A null overlay commit or an absent overlay path refuses and writes no ref."""
    good = world.profile()
    build(world, "0.1.0", good)
    build(world, "0.2.0", good)
    releases = yaml.safe_load(good.read_text())["releases"]
    releases[2]["overlay"]["commit"] = None
    refused(build(world, "0.3.0", world.profile(releases=releases)), "overlay commit is null")
    releases[2]["overlay"] = {"commit": world.overlay, "paths": ["README.md", "nope.txt"]}
    refused(build(world, "0.3.0", world.profile(releases=releases)), "nope.txt is absent")
    assert g(world.src, "rev-parse", "public/main") == g(world.src, "rev-parse", "v0.2.0^{}")
    assert g(world.src, "tag", "-l", "v0.3.0") == ""


def test_rebuild_is_unchanged(world: World) -> None:
    """A rebuild with the same inputs prints `unchanged` and writes no ref."""
    profile = world.profile()
    shas = build_all(world, profile)
    refs = g(world.src, "for-each-ref")
    for version, (commit, tag) in shas.items():
        proc = build(world, version, profile)
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.split() == ["unchanged", commit, tag]
    assert g(world.src, "for-each-ref") == refs


def test_hostile_clone_builds_identical_shas(world: World) -> None:
    """Hostile git config and environment in a second clone give identical shas."""
    clone = world.tmp / "clone"  # the same history, cloned before any export exists
    g(world.tmp, "clone", "-q", "--no-hardlinks", str(world.src), str(clone))
    profile = world.profile()
    shas = build_all(world, profile)
    home = world.tmp / "hostile-home"
    home.mkdir()
    (home / ".gitconfig").write_text(
        "[commit]\n\tgpgSign = true\n[tag]\n\tgpgSign = true\n[gpg]\n\tprogram = /bin/false\n"
        "[i18n]\n\tcommitEncoding = ISO-8859-1\n"
        "[user]\n\tname = Wrong\n\temail = wrong@example.net\n"
    )
    hostile = {
        **os.environ,
        "HOME": str(home),
        "TZ": "Asia/Tokyo",
        "GIT_AUTHOR_NAME": "Hostile",
        "GIT_COMMITTER_EMAIL": "hostile@example.net",
        "GIT_AUTHOR_DATE": "1111111111 +1300",
        "GIT_CONFIG_GLOBAL": str(home / ".gitconfig"),
    }
    hostile.pop("GIT_CONFIG_NOSYSTEM", None)
    for version, (commit, tag) in shas.items():
        assert created(build(world, version, profile, repo=clone, env=hostile)) == (commit, tag)


# -------------------------------------------------------------------------------- refusals


def test_unknown_version_and_out_of_order(world: World) -> None:
    """An unknown version and an out-of-order build refuse."""
    profile = world.profile()
    refused(build(world, "9.9.9", profile), "not in the profile")
    refused(build(world, "0.2.0", profile), "0.1.0 has no export")
    assert g(world.src, "for-each-ref", "refs/heads/public", "refs/tags") == ""


def test_non_descendant_source(world: World) -> None:
    """A source that does not descend from the previous source refuses."""
    releases = yaml.safe_load(world.profile().read_text())["releases"]
    releases[1]["source"] = world.side
    profile = world.profile(releases=releases)
    build(world, "0.1.0", profile)
    refused(build(world, "0.2.0", profile), "does not descend")


def test_public_main_moved_by_hand(world: World) -> None:
    """public/main moved away from the previous export refuses the next build."""
    profile = world.profile()
    commit, _ = created(build(world, "0.1.0", profile))
    g(world.src, "update-ref", "refs/heads/public/main", world.base)
    refused(build(world, "0.2.0", profile), "public/main is not at the v0.1.0 export")
    g(world.src, "update-ref", "refs/heads/public/main", world.side)
    refused(build(world, "0.2.0", profile), "public/main is not at the v0.1.0 export")
    g(world.src, "update-ref", "refs/heads/public/main", world.sources["0.1.0"])
    refused(build(world, "0.2.0", profile), "public/main is not at the v0.1.0 export")


def test_public_main_not_from_base(world: World) -> None:
    """public/main not descending from the base, or not the first export, refuses."""
    orphan = g(
        world.src,
        "commit-tree",
        g(world.src, "rev-parse", f"{world.base}^{{tree}}"),
        "-m",
        "orphan",
        env=clean_env(
            GIT_AUTHOR_NAME="x",
            GIT_AUTHOR_EMAIL="x@example.com",
            GIT_COMMITTER_NAME="x",
            GIT_COMMITTER_EMAIL="x@example.com",
        ),
    )
    g(world.src, "update-ref", "refs/heads/public/main", orphan)
    refused(build(world, "0.1.0", world.profile()), "does not descend from the base")
    g(world.src, "update-ref", "refs/heads/public/main", world.base)
    refused(build(world, "0.1.0", world.profile()), "is not this version's export")


def test_missing_changelog_section(world: World) -> None:
    """A source CHANGELOG without the version's section refuses."""
    releases = yaml.safe_load(world.profile().read_text())["releases"]
    releases[0]["version"] = "0.0.8"
    refused(build(world, "0.0.8", world.profile(releases=releases)), "no ## [0.0.8] section")


def test_changed_inputs_refuse(world: World) -> None:
    """Different inputs for an exported version refuse without --replace-unpushed."""
    created(build(world, "0.1.0", world.profile()))
    changed = world.profile(identity={"name": "Someone Else", "email_from": "base"})
    refused(build(world, "0.1.0", changed), "exported with different inputs")


def test_replace_unpushed_refuses_a_tag_on_the_remote(world: World) -> None:
    """A version whose tag is on the remote is published."""
    commit, tag = created(build(world, "0.1.0", world.profile()))
    g(world.src, "push", "-q", "origin", "refs/tags/v0.1.0")
    changed = world.profile(identity={"name": "Someone Else", "email_from": "base"})
    refused(build(world, "0.1.0", changed, "--replace-unpushed"), "0.1.0: published")
    assert g(world.src, "rev-parse", "public/main", "v0.1.0") == f"{commit}\n{tag}"


def test_replace_unpushed_refuses_an_export_on_remote_main(world: World) -> None:
    """A version reachable from the remote's main is published; the fetch writes no ref."""
    commit, tag = created(build(world, "0.1.0", world.profile()))
    g(world.src, "push", "-q", "origin", f"{commit}:refs/heads/main")
    refs = g(world.src, "for-each-ref")
    changed = world.profile(identity={"name": "Someone Else", "email_from": "base"})
    refused(build(world, "0.1.0", changed, "--replace-unpushed"), "0.1.0: published")
    assert g(world.src, "for-each-ref") == refs  # the fetch wrote no ref


def test_replace_unpushed_allowed_when_unpublished(world: World) -> None:
    """An unpublished public/main tip is rebuilt with the new inputs."""
    profile = world.profile()
    old_commit, old_tag = created(build(world, "0.1.0", profile))
    remotes = g(world.src, "for-each-ref", "refs/remotes")
    changed = world.profile(identity={"name": "Someone Else", "email_from": "base"})
    new_commit, new_tag = created(build(world, "0.1.0", changed, "--replace-unpushed"))
    assert (new_commit, new_tag) != (old_commit, old_tag)
    assert header(world.src, new_commit)["parent"] == [world.base]
    assert g(world.src, "rev-parse", "public/main", "v0.1.0") == f"{new_commit}\n{new_tag}"
    assert g(world.src, "for-each-ref", "refs/remotes") == remotes  # the fetch wrote no ref


def test_replace_unpushed_only_the_tip(world: World) -> None:
    """--replace-unpushed refuses an export that is not the public/main tip."""
    profile = world.profile()
    build(world, "0.1.0", profile)
    build(world, "0.2.0", profile)
    changed = world.profile(identity={"name": "Someone Else", "email_from": "base"})
    refused(build(world, "0.1.0", changed, "--replace-unpushed"), "not the public/main tip")


def test_replace_unpushed_remote_error_fails_closed(world: World) -> None:
    """An unreadable remote refuses (fails closed)."""
    created(build(world, "0.1.0", world.profile()))
    g(world.src, "remote", "set-url", "origin", str(world.tmp / "missing.git"))
    changed = world.profile(identity={"name": "Someone Else", "email_from": "base"})
    refused(build(world, "0.1.0", changed, "--replace-unpushed"), "cannot read remote origin")


# --------------------------------------------------------------------------- profile check


def test_profile_check_on_a_synthetic_profile(world: World) -> None:
    """A valid synthetic profile passes `profile check`."""
    proc = tool(world, "profile", "check", profile=world.profile())
    assert (proc.returncode, proc.stdout) == (0, "profile ok\n"), proc.stderr


def test_profile_check_refusals(world: World) -> None:
    """Each invalid profile refuses with a one-line reason."""
    doc = yaml.safe_load(world.profile().read_text())
    row = {"path": "nothing/", "reason": "matches nothing", "versions": "all"}
    cases = [
        ({"exclude": [*doc["exclude"], row]}, "'nothing/' matches nothing"),
        ({"surprise": 1}, "unknown key 'surprise'"),
        ({"identity": {"name": NAME, "email_from": "base", "x": 1}}, "unknown key 'x'"),
        ({"top_level": [*doc["top_level"], "lore"]}, "'lore' is in no release's export tree"),
        (
            {"identity": {"name": f"{HOME_ROOT}someone", "email_from": "base"}},
            "strict scan literal",
        ),
        ({"releases": list(reversed(doc["releases"]))}, "not strictly increasing"),
        ({"scan": {"x": 1}}, "scan: unknown key 'x'"),
        ({"identity": {"name": "A <b>", "email_from": "base"}}, "identity.name"),
    ]
    for change, text in cases:
        refused(tool(world, "profile", "check", profile=world.profile(**change)), text)
    releases = doc["releases"]
    releases[1]["source"] = world.side
    refused(tool(world, "profile", "check", profile=world.profile(releases=releases)), "descend")


def test_profile_size_cap(world: World) -> None:
    """A profile over 256 KiB refuses."""
    big = world.tmp / "big.yml"
    big.write_text(world.profile().read_text() + "#" * (256 * 1024))
    refused(tool(world, "profile", "check", profile=big), "larger than")


# ---------------------------------------------------------------------------- static check

FORBIDDEN = {"checkout", "switch", "reset", "stash", "add", "commit", "merge", "push"}


def test_no_working_tree_subcommand_in_the_source() -> None:
    """No forbidden git subcommand appears as a string in any argv list or call of the tool."""
    tree = ast.parse(TOOL.read_text())
    words = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.List, ast.Tuple, ast.Call)):
            items = node.args if isinstance(node, ast.Call) else node.elts
            for item in items:
                if isinstance(item, ast.Constant) and isinstance(item.value, str):
                    words.add(item.value)
    assert words & FORBIDDEN == set()
    assert {"commit-tree", "mktag", "update-ref", "read-tree"} <= words  # the scan sees argv


def test_the_static_check_can_fail(tmp_path: Path) -> None:
    """The argv scan finds forbidden subcommands in a sample."""
    sample = 'git(repo, "checkout", sha)\nargv = ["git", "push"]\n'
    words = set()
    for node in ast.walk(ast.parse(sample)):
        if isinstance(node, (ast.List, ast.Tuple, ast.Call)):
            items = node.args if isinstance(node, ast.Call) else node.elts
            words |= {i.value for i in items if isinstance(i, ast.Constant)}
    assert words & FORBIDDEN == {"checkout", "push"}


def test_temp_indexes_are_removed(world: World, tmp_path: Path) -> None:
    """The temporary index directory is deleted after a build."""
    tmpdir = tmp_path / "tooltmp"
    tmpdir.mkdir()
    env = {**os.environ, "TMPDIR": str(tmpdir)}
    created(build(world, "0.1.0", world.profile(), env=env))
    assert list(tmpdir.iterdir()) == []


def test_overrides_yml_is_never_exported(world: World) -> None:
    """R88 (OPR.99.0.11.6): a source tree holding openrig/hacf/overrides.yml refuses the build."""
    # a commit on top of the 0.1.0 source holding the file, made with plumbing so the world's
    # working-tree edits stay untouched
    index = world.tmp / "overrides-index"
    env = clean_env(GIT_INDEX_FILE=str(index))
    g(world.src, "read-tree", world.sources["0.1.0"], env=env)
    blob = subprocess.run(
        ["git", "-C", str(world.src), "hash-object", "-w", "--stdin"],
        input="seats:\n  scribe:\n    model: x\n",
        capture_output=True,
        text=True,
        env=clean_env(),
        check=True,
    ).stdout.strip()
    g(
        world.src,
        "update-index",
        "--add",
        "--cacheinfo",
        f"100644,{blob},openrig/hacf/overrides.yml",
        env=env,
    )
    tree = g(world.src, "write-tree", env=env)
    ident = clean_env(
        GIT_AUTHOR_NAME="Dev",
        GIT_AUTHOR_EMAIL="dev@example.org",
        GIT_COMMITTER_NAME="Dev",
        GIT_COMMITTER_EMAIL="dev@example.org",
        GIT_AUTHOR_DATE=DATES["0.1.0"][0],
        GIT_COMMITTER_DATE=DATES["0.1.0"][1],
    )
    with_overrides = g(
        world.src,
        "commit-tree",
        tree,
        "-p",
        world.sources["0.1.0"],
        "-m",
        "tracked overrides",
        env=ident,
    )
    releases = yaml.safe_load(world.profile().read_text())["releases"]
    releases[0]["source"] = with_overrides
    proc = build(world, "0.1.0", world.profile(releases=releases))
    refused(proc, "openrig/hacf/overrides.yml")
    assert g(world.src, "tag", "-l", "v0.1.0") == ""
    # the same tree without it still builds
    created(build(world, "0.1.0", world.profile()))
