"""Tests of dev/release_export.py's `scan` and the profile's scan/allowlist sections (slice 3).

Offline and synthetic. Each case plants files in a commit made with plumbing only (no working
tree), points a one-release profile at it and runs `scan --version 0.1.0`. The private address
of the digest cases is a fake one, made up here; its digest is computed here and written to a
temporary digest file, and no output may contain the address itself.

No private literal and no digest of one is written here (F1): the strict-window cases plant a
stand-in literal and write its `window … strict` line to a temporary digest file, the digest
computed here; the home roots are built from parts.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
import yaml
from test_build import (
    HOME_ROOT,
    SCAN,
    TOOL,
    USERS_ROOT,
    World,
    clean_env,
    g,
    make_world,
    refused,
    tool,
)

FAKE_ADDRESS = "Hidden.Person\x40Private-Mail.zz"  # mixed case: the digest is of the lower case
STAND_IN = "zq-standin-project"  # stands in for a private literal of the digest file
HOME = HOME_ROOT.encode()


def sha(text: str | bytes) -> str:
    """The sha256 hex of a string or bytes."""
    return hashlib.sha256(text.encode() if isinstance(text, str) else text).hexdigest()


def plant(world: World, files: dict[str, bytes], parent: str | None = None) -> str:
    """A commit on top of `parent` (default: release 0.3.0) with `files` added, via plumbing."""
    parent = parent or world.sources["0.3.0"]
    with tempfile.TemporaryDirectory() as tmp:
        env = clean_env(GIT_INDEX_FILE=os.path.join(tmp, "index"))
        g(world.src, "read-tree", parent, env=env)
        for path, data in files.items():
            blob = (
                subprocess.run(
                    ["git", "-C", str(world.src), "hash-object", "-w", "--stdin"],
                    input=data,
                    capture_output=True,
                    env=clean_env(),
                    check=True,
                )
                .stdout.decode()
                .strip()
            )
            g(world.src, "update-index", "--add", "--cacheinfo", f"100644,{blob},{path}", env=env)
        tree = g(world.src, "write-tree", env=env)
    who = clean_env(
        GIT_AUTHOR_NAME="Dev",
        GIT_AUTHOR_EMAIL="dev@example.org",
        GIT_COMMITTER_NAME="Dev",
        GIT_COMMITTER_EMAIL="dev@example.org",
        GIT_AUTHOR_DATE="1725000000 +0000",
        GIT_COMMITTER_DATE="1725000000 +0000",
    )
    return g(world.src, "commit-tree", tree, "-p", parent, "-m", "planted", env=who)


def scan_profile(world: World, source: str, **changes) -> Path:
    """A one-release profile scanning `source` as version 0.1.0."""
    releases = [{"version": "0.1.0", "source": source}]
    return world.profile(releases=releases, **changes)


@pytest.fixture
def world(tmp_path: Path) -> World:
    """The synthetic world of test_build.py."""
    return make_world(tmp_path)


@pytest.fixture
def digests(tmp_path: Path) -> Path:
    """A digest file holding the fake address's digest, labelled `fake`."""
    path = tmp_path / "digests.sha256"
    path.write_text(f"# test digests\n{sha(FAKE_ADDRESS.lower())} fake\n")
    return path


def window_line(literal: str, label: str = "stand-in", level: str = "strict") -> str:
    """A digest-file `window` line for a stand-in literal, its digest computed here."""
    return f"window {sha(literal)} {len(literal.encode())} {label} {level}"


def with_lines(digests: Path, *lines: str) -> Path:
    """A copy of the digest file with `lines` appended."""
    path = digests.with_name(digests.name + f".{len(list(digests.parent.glob('*.x*')))}.x")
    path.write_text(digests.read_text() + "".join(f"{line}\n" for line in lines))
    return path


def scan(world: World, profile: Path, digests: Path, *extra: str):
    """Run `scan --version 0.1.0`; assert the fake address is in neither stream."""
    proc = tool(
        world, "scan", "--version", "0.1.0", "--digests", str(digests), *extra, profile=profile
    )
    for stream in (proc.stdout, proc.stderr):
        assert FAKE_ADDRESS.lower() not in stream.lower()
    return proc


# ----------------------------------------------------------------------------------- strict


def test_clean_tree_passes(world: World, digests: Path) -> None:
    """The synthetic release has no strict hit: exit 0 and a zero table."""
    proc = scan(world, scan_profile(world, world.sources["0.3.0"]), digests)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "strict failures: 0" in proc.stdout
    assert "home-dir                            0        0        0" in proc.stdout


def test_home_and_users_text_hits_fail(world: World, digests: Path) -> None:
    """A planted home-root path and a macOS home-root path each fail, with file and line."""
    mac = USERS_ROOT.encode() + b"a/b\n"
    source = plant(world, {"notes.txt": b"one\nsee " + HOME + b"someone/x\n", "mac.md": mac})
    proc = scan(world, scan_profile(world, source), digests)
    assert proc.returncode == 1
    assert "FAIL home-dir  notes.txt  x1 lines 2  (no allowlist row)" in proc.stdout
    assert "FAIL users-dir  mac.md  x1 lines 1  (no allowlist row)" in proc.stdout
    assert "strict failures: 2" in proc.stdout


def test_path_name_hit_fails(world: World, digests: Path) -> None:
    """A strict literal in a path name fails as a path-name hit, never allowlisted."""
    path = f"dev/{STAND_IN}{HOME_ROOT}x.txt"  # a digest-file literal and a home root in one name
    source = plant(world, {path: b"clean\n"})
    proc = scan(world, scan_profile(world, source), with_lines(digests, window_line(STAND_IN)))
    assert proc.returncode == 1
    shown = f"dev/<stand-in>{HOME_ROOT}x.txt"  # the literal masked by its label, never printed
    assert f"FAIL stand-in  {shown} (path name)  x1 lines 1  (never allowlisted)" in proc.stdout
    assert STAND_IN not in proc.stdout + proc.stderr
    assert "a path name or object header is never allowlisted" in proc.stdout


def test_digest_file_label_is_never_allowlistable(world: World) -> None:
    """No allowlist row may name a digest-file label (the private literals live only there)."""
    for label in ("private-project", "projects-dir"):
        row = {"path": "app.py", "pattern": label, "count": 1, "reason": "r", "versions": "all"}
        required = ["private-project", "projects-dir"]
        profile = world.profile(allowlist=[row], required_digest_labels=required)
        refused(tool(world, "profile", "check", profile=profile), "may never be allowlisted")


def test_strict_section_must_hold_the_tool_literals(world: World) -> None:
    """A scan section missing one of the tool's strict literals fails `profile check`."""
    profile = world.profile(scan={**SCAN, "strict": SCAN["strict"][1:]})
    refused(tool(world, "profile", "check", profile=profile), "misses the tool's strict literal")


# ------------------------------------------------------------------------------------ email


def test_digest_hit_never_prints_the_address(world: World, digests: Path) -> None:
    """A planted private address is reported by digest label only, and fails."""
    text = f"Write to {FAKE_ADDRESS} or <{FAKE_ADDRESS.upper()}>.\n".encode()
    proc = scan(world, scan_profile(world, plant(world, {"CONTACT.md": text})), digests)
    assert proc.returncode == 1
    assert "FAIL private address (digest fake)  CONTACT.md  x2 lines 1,1  (never allowlisted)" in (
        proc.stdout
    )


def test_digest_hit_inside_a_longer_token(world: World, digests: Path) -> None:
    """An address glued to more characters is still found by digest and never printed."""
    text = f"x{FAKE_ADDRESS}\n".encode()
    proc = scan(world, scan_profile(world, plant(world, {"glued.txt": text})), digests)
    assert proc.returncode == 1
    assert "private address (digest fake)  glued.txt" in proc.stdout


def test_unlisted_email_fails_and_allowlisted_passes(world: World, digests: Path) -> None:
    """An email outside the allowlist fails; example.org, *.invalid and an email row pass."""
    files = {
        "a.txt": b"ops\x40company.zz\n",
        "b.txt": b"dev@example.org and x@hacf.invalid\n",
        "c.txt": b"Named\x40Vendor.zz\n",
    }
    row = {
        "path": "c.txt",
        "pattern": "email:named\x40vendor.zz",
        "count": 1,
        "reason": "already public",
        "versions": "all",
    }
    proc = scan(world, scan_profile(world, plant(world, files), allowlist=[row]), digests)
    assert proc.returncode == 1
    assert "FAIL email:ops\x40company.zz  a.txt  x1 lines 1  (no allowlist row)" in proc.stdout
    assert "ok   email:named\x40vendor.zz  c.txt  x1 lines 1  (already public)" in proc.stdout
    assert "b.txt" not in proc.stdout.split("advisory (never failing):")[0]


# ----------------------------------------------------------------------------------- binary


def test_binary_hit_with_and_without_a_blob_row(world: World, digests: Path) -> None:
    """A binary strict hit fails without a row, passes with its sha256, fails on a change."""
    blob = b"\x89PNG\0\x01\x02 " + HOME + b"x \0"
    source = plant(world, {"media/logo.bin": blob})
    proc = scan(world, scan_profile(world, source), digests)
    assert proc.returncode == 1
    assert f"binary blob sha256 {sha(blob)} has no allowlist row" in proc.stdout
    assert "lines" not in [ln for ln in proc.stdout.splitlines() if "logo.bin" in ln][0]

    row = {"path": "media/logo.bin", "blob_sha256": sha(blob), "reason": "r", "versions": "all"}
    proc = scan(world, scan_profile(world, source, allowlist=[row]), digests)
    assert proc.returncode == 0, proc.stdout
    assert "ok   home-dir  media/logo.bin  x1  (binary row (r))" in proc.stdout

    changed = plant(world, {"media/logo.bin": blob + b"\0more"})
    proc = scan(world, scan_profile(world, changed, allowlist=[row]), digests)
    assert proc.returncode == 1
    assert "differs from the row's" in proc.stdout


def binary_row(path: str, blob: bytes) -> dict:
    """A binary allowlist row matching `blob`."""
    return {"path": path, "blob_sha256": sha(blob), "reason": "r", "versions": "all"}


def test_binary_row_never_admits_the_projects_dir(world: World, digests: Path) -> None:
    """A digest-file strict literal in a binary fails even with a matching binary row (R9)."""
    blob = b"\x89PNG\0 " + HOME + b"x " + STAND_IN.encode() + b"\0"
    source = plant(world, {"media/logo.bin": blob})
    profile = scan_profile(world, source, allowlist=[binary_row("media/logo.bin", blob)])
    proc = scan(world, profile, with_lines(digests, window_line(STAND_IN, "projects-dir")))
    assert proc.returncode == 1, proc.stdout
    never = [ln for ln in proc.stdout.splitlines() if ln.endswith("(never allowlisted)")]
    assert len(never) == 1 and never[0].startswith("FAIL ")
    assert "media/logo.bin  x1  (never allowlisted)" in never[0]
    # the allowlistable home-root hit in the same blob is still covered by the row
    assert "ok   home-dir  media/logo.bin  x1  (binary row (r))" in proc.stdout


def test_binary_row_never_admits_an_email(world: World, digests: Path) -> None:
    """An unlisted email in a binary fails even with a matching binary row (lead's ruling)."""
    blob = b"\x89PNG\0 leak\x40corp-elsewhere.zz \0"
    source = plant(world, {"media/logo.bin": blob})
    profile = scan_profile(world, source, allowlist=[binary_row("media/logo.bin", blob)])
    proc = scan(world, profile, digests)
    assert proc.returncode == 1, proc.stdout
    assert (
        "FAIL email:leak\x40corp-elsewhere.zz  media/logo.bin  x1  (an email is allowlisted only by"
        " the email allowlist, never by a binary row)"
    ) in proc.stdout


def test_binary_row_never_admits_a_digest_hit(world: World, digests: Path) -> None:
    """A private address in a binary fails even with a matching binary row; never printed."""
    blob = b"\x89PNG\0 " + FAKE_ADDRESS.encode() + b" \0"
    source = plant(world, {"media/logo.bin": blob})
    profile = scan_profile(world, source, allowlist=[binary_row("media/logo.bin", blob)])
    proc = scan(world, profile, digests)
    assert proc.returncode == 1, proc.stdout
    assert "FAIL private address (digest fake)  media/logo.bin  x1  (never allowlisted)" in (
        proc.stdout
    )


def test_binary_row_never_admits_the_private_project(world: World, monkeypatch) -> None:
    """A digest-file strict literal in a binary fails even with a matching binary row (R9).

    In process, with a stand-in private literal held as a `window … strict` digest-file line.
    """
    from test_gate import rx

    blob = b"\x89PNG\0 " + STAND_IN.encode() + b" \0"
    profile = rx.load_profile(
        world.profile(
            releases=[{"version": "0.1.0", "source": world.sources["0.3.0"]}],
            allowlist=[binary_row("media/logo.bin", blob)],
        )
    )
    items = [rx.ScanItem("media/logo.bin", blob, "blob")]
    digests = rx.DigestFile(windows=[(sha(STAND_IN), len(STAND_IN), "stand-in", "strict")])
    result = rx.run_scan(items, profile, "0.1.0", digests)
    assert "FAIL stand-in  media/logo.bin  x1  (never allowlisted)" in result.lines
    assert result.failures >= 1


# -------------------------------------------------------------------------------- allowlist


def row(path: str, count: int, versions: str = "all") -> dict:
    """A home-dir allowlist row."""
    return {
        "path": path,
        "pattern": "home-dir",
        "count": count,
        "reason": "synthetic",
        "versions": versions,
    }


def test_count_change_fails_naming_both(world: World, digests: Path) -> None:
    """A hit count different from the row's fails and names both counts."""
    source = plant(world, {"t.py": b"".join(b"'" + HOME + c + b"'\n" for c in (b"a", b"b", b"c"))})
    proc = scan(world, scan_profile(world, source, allowlist=[row("t.py", 2)]), digests)
    assert proc.returncode == 1
    assert "FAIL home-dir  t.py  x3 lines 1,2,3  (count 3, the row says 2)" in proc.stdout
    proc = scan(world, scan_profile(world, source, allowlist=[row("t.py", 3)]), digests)
    assert proc.returncode == 0, proc.stdout
    assert "ok   home-dir  t.py  x3 lines 1,2,3  (synthetic)" in proc.stdout


def test_row_for_an_absent_path_is_na_and_unused_row_fails(world: World, digests: Path) -> None:
    """A row whose file is absent reports n/a; a row whose file has no hit fails."""
    rows = [row("gone.py", 1), row("app.py", 1), row("t.py", 1, versions="9.9.9")]
    proc = scan(world, scan_profile(world, world.sources["0.3.0"], allowlist=rows), digests)
    assert proc.returncode == 1
    assert "n/a  home-dir  gone.py  (absent at 0.1.0)" in proc.stdout
    assert "FAIL home-dir  app.py  x0  (the row expects x1)" in proc.stdout
    assert "t.py" not in proc.stdout  # its versions selector does not cover 0.1.0


def test_allowlist_row_matching_nothing_fails_profile_check(world: World) -> None:
    """`profile check` fails a row whose path is absent at every release (mission R8)."""
    profile = world.profile(allowlist=[row("nowhere.py", 1)])
    refused(tool(world, "profile", "check", profile=profile), "'nowhere.py' matches nothing")


# --------------------------------------------------------------------------------- advisory


def test_advisory_hit_does_not_fail(world: World, digests: Path) -> None:
    """An advisory pattern and a mention of an excluded path are reported, never failing."""
    source = plant(world, {"doc.md": b"line\nask TJ\nsee lore/MAP.md\n", "pic.bin": b"\0TJ\0"})
    proc = scan(world, scan_profile(world, source), digests)
    assert proc.returncode == 0, proc.stdout
    assert "advisory  initials  doc.md:2" in proc.stdout
    assert "advisory  initials  pic.bin (binary)" in proc.stdout
    assert "advisory  mentions lore/  doc.md:3" in proc.stdout


# ---------------------------------------------------------------------------------- digests


@pytest.mark.parametrize(
    ("content", "text"),
    [
        (None, "missing or unreadable"),
        ("", "holds no digest"),
        ("# only a comment\n\n", "holds no digest"),
        ("not-a-digest label\n", "malformed (line 1)"),
        (f"{'A' * 64}\n", "malformed (line 1)"),
        (f"{'a' * 64} someone@example.com\n", "malformed (line 1)"),
    ],
)
def test_bad_digest_file_refuses_before_scanning(
    world: World, tmp_path: Path, content: str | None, text: str
) -> None:
    """A missing, empty or malformed digest file exits 2 and never names its path."""
    path = tmp_path / "secret-digests"
    if content is not None:
        path.write_text(content)
    proc = tool(world, "scan", "--version", "0.1.0", "--digests", str(path),
                profile=scan_profile(world, world.sources["0.3.0"]))  # fmt: skip
    refused(proc, text)
    assert "secret-digests" not in proc.stderr


def test_digest_file_from_the_environment(world: World, digests: Path) -> None:
    """HACF_EXPORT_DIGESTS names the digest file; neither flag nor variable refuses."""
    profile = scan_profile(world, plant(world, {"x.txt": FAKE_ADDRESS.encode()}))
    env = {**os.environ, "HACF_EXPORT_DIGESTS": str(digests)}
    proc = tool(world, "scan", "--version", "0.1.0", profile=profile, env=env)
    assert proc.returncode == 1
    assert "private address (digest fake)" in proc.stdout
    assert FAKE_ADDRESS.lower() not in proc.stdout.lower()
    env.pop("HACF_EXPORT_DIGESTS")
    refused(tool(world, "scan", "--version", "0.1.0", profile=profile, env=env), "no digest file")


def test_scan_overlay_commit_and_absent_overlay_path(world: World, digests: Path) -> None:
    """--overlay-commit overrides the profile's; an absent overlay path is a note, not an error."""
    later = plant(world, {"README.md": b"see " + HOME + b"q\n"}, parent=world.overlay)
    proc = tool(world, "scan", "--version", "0.3.0", "--digests", str(digests),
                "--overlay-commit", later, profile=world.profile())  # fmt: skip
    assert proc.returncode == 1
    assert f"overlay {later}" in proc.stdout
    assert "FAIL home-dir  README.md  x1" in proc.stdout
    releases = yaml.safe_load(world.profile().read_text())["releases"]
    releases[2]["overlay"]["paths"].append("not/there.md")
    proc = tool(world, "scan", "--version", "0.3.0", "--digests", str(digests),
                profile=world.profile(releases=releases))  # fmt: skip
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "note: overlay path not/there.md is absent" in proc.stdout


def test_scan_writes_no_ref(world: World, digests: Path) -> None:
    """`scan` is read only: no ref changes."""
    refs = g(world.src, "for-each-ref")
    scan(world, scan_profile(world, world.sources["0.3.0"]), digests)
    assert g(world.src, "for-each-ref") == refs


def test_cli_entry_point_runs_under_the_interpreter() -> None:
    """The tool's --help lists the slice 3 commands."""
    out = subprocess.run(
        [sys.executable, str(TOOL), "--help"], capture_output=True, text=True, check=True
    ).stdout
    for command in ("scan", "gate", "plan", "verify-push"):
        assert command in out


def test_address_in_a_path_name_is_redacted(world: World, digests: Path) -> None:
    """A private address in a path name fails, and the FAIL line names the path redacted (R9)."""
    source = plant(world, {f"docs/{FAKE_ADDRESS}.txt": b"clean\n"})
    proc = scan(world, scan_profile(world, source), digests)  # asserts the address is in no stream
    assert proc.returncode == 1
    assert "private address (digest fake)  docs/<private address (digest fake)> (path name)" in (
        proc.stdout
    )
