"""Profile v2 and the private digest file (OPR.0.6.3.4): no private value or digest of one ships.

Offline and synthetic. Every private literal here is a stand-in made up in this file, and every
digest is computed here from a stand-in. Raw-only key names are built at runtime, so this file
never holds one as a quoted JSON token.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from test_build import HOME_ROOT, TOOL, USERS_ROOT, World, g, make_world, refused, tool
from test_gate import rx
from test_scan import FAKE_ADDRESS, plant, scan_profile, sha

REPO_ROOT = Path(__file__).resolve().parents[3]
SHIPPED = REPO_ROOT / "dev" / "release_export.yml"
STAND_IN = "zq-standin-secret"
STAND_IN_HOST = "zq-standin-host"
ACCOUNT_RULE = r"(?<![/\w.-])zqaccount(?![\w-])"


def key(name: str) -> str:
    """A JSON key token, built at runtime."""
    return '"' + name + '"'


@pytest.fixture
def world(tmp_path: Path) -> World:
    """The synthetic world of test_build.py."""
    return make_world(tmp_path)


def digest_file(tmp_path: Path, *lines: str) -> Path:
    """A temporary digest file: the fake address, then `lines`."""
    path = tmp_path / "digests-v2"
    path.write_text("".join(f"{line}\n" for line in [f"{sha(FAKE_ADDRESS.lower())} fake", *lines]))
    return path


def window(literal: str, label: str, level: str) -> str:
    """A digest-file `window` line for a stand-in, its digest computed here."""
    return f"window {sha(literal)} {len(literal.encode())} {label} {level}"


# ------------------------------------------------------------------------------- digest file


def test_digest_file_line_kinds_parse(tmp_path: Path) -> None:
    """Each line kind parses into its part; every label is collected."""
    path = digest_file(
        tmp_path,
        "# a comment",
        window(STAND_IN, "private-project", "strict"),
        window(STAND_IN_HOST, "host-name", "advisory"),
        f"regex account-regex advisory {ACCOUNT_RULE}",
    )
    loaded = rx.load_digests(str(path))
    assert loaded.addresses == {sha(FAKE_ADDRESS.lower()): "fake"}
    assert loaded.windows == [
        (sha(STAND_IN), len(STAND_IN), "private-project", "strict"),
        (sha(STAND_IN_HOST), len(STAND_IN_HOST), "host-name", "advisory"),
    ]
    assert [label for label, _ in loaded.regexes] == ["account-regex"]
    assert loaded.labels == {"fake", "private-project", "host-name", "account-regex"}


@pytest.mark.parametrize(
    "line",
    [
        f"window {'a' * 64} 0 label strict",
        f"window {'a' * 64} 7 Bad_Label strict",
        f"window {'a' * 64} 7 label sometimes",
        "window zz 7 label strict",
        "regex label strict zqaccount",
        "regex label advisory (unclosed",
        "just words here",
    ],
)
def test_malformed_line_names_only_its_number(world: World, tmp_path: Path, line: str) -> None:
    """A malformed line exits 2 naming its line number; the line's text never appears."""
    path = digest_file(tmp_path, line)
    proc = tool(world, "scan", "--version", "0.1.0", "--digests", str(path),
                profile=scan_profile(world, world.sources["0.3.0"]))  # fmt: skip
    refused(proc, "the digest file is malformed (line 2)")
    assert line.split()[-1] not in proc.stderr


@pytest.mark.parametrize("command", ["scan", "gate", "plan"])
def test_missing_required_label_stops_before_scanning(
    world: World, tmp_path: Path, command: str
) -> None:
    """A required label the digest file lacks stops scan, gate and plan with exit 2, naming it."""
    profile = world.profile(
        releases=[{"version": "0.1.0", "source": world.sources["0.3.0"]}],
        required_digest_labels=["fake", "account-regex"],
    )
    path = digest_file(tmp_path, window(STAND_IN, "private-project", "strict"))
    args = {"scan": ["--version", "0.1.0"], "gate": ["--version", "0.1.0"], "plan": []}[command]
    env = {**__import__("os").environ, "HACF_PY310": sys.executable}
    proc = tool(world, command, *args, "--digests", str(path), profile=profile, env=env)
    refused(proc, "the digest file lacks the required label account-regex")


# ------------------------------------------------------------------------------- digest-line


def digest_line(stdin: str, *args: str) -> subprocess.CompletedProcess[str]:
    """Run `digest-line` with `stdin`."""
    argv = [sys.executable, str(TOOL), "digest-line", *args]
    return subprocess.run(argv, input=stdin, capture_output=True, text=True, check=False)


def test_digest_line_window_and_regex(tmp_path: Path) -> None:
    """The printed lines have the file's format, load back, and never echo the input."""
    proc = digest_line(STAND_IN + "\n", "--label", "private-project", "--kind", "window",
                       "--level", "strict")  # fmt: skip
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == window(STAND_IN, "private-project", "strict") + "\n"
    assert STAND_IN not in proc.stdout + proc.stderr
    assert STAND_IN not in " ".join(proc.args)
    proc2 = digest_line(ACCOUNT_RULE, "--label", "account-regex", "--kind", "regex",
                        "--level", "advisory")  # fmt: skip
    assert proc2.returncode == 0, proc2.stderr
    assert proc2.stdout == f"regex account-regex advisory {ACCOUNT_RULE}\n"
    path = tmp_path / "d"
    path.write_text(proc.stdout + proc2.stdout)
    assert rx.load_digests(str(path)).labels == {"private-project", "account-regex"}


@pytest.mark.parametrize(
    ("stdin", "args", "text"),
    [
        ("zqpattern", ["--kind", "regex", "--level", "strict"], "advisory only"),
        ("", ["--kind", "window", "--level", "strict"], "stdin is empty"),
        ("\n", ["--kind", "window", "--level", "strict"], "stdin is empty"),
        ("(", ["--kind", "regex", "--level", "advisory"], "does not compile"),
    ],
)
def test_digest_line_refusals(stdin: str, args: list[str], text: str) -> None:
    """A strict regex, an empty stdin or a bad regex exits 2 without echoing the input."""
    proc = digest_line(stdin, "--label", "some-label", *args)
    assert proc.returncode == 2 and proc.stdout == ""
    assert text in proc.stderr
    if stdin.strip():
        assert stdin not in proc.stderr


def test_digest_line_help_says_append_by_hand() -> None:
    """--help says to append the line to the private digest file by hand."""
    proc = subprocess.run([sys.executable, str(TOOL), "digest-line", "--help"],
                          capture_output=True, text=True, check=True)  # fmt: skip
    assert "Append the printed line to the private digest file by hand" in " ".join(
        proc.stdout.split()
    )


# ---------------------------------------------------------------------------------- scanning


def test_strict_window_fails_without_printing_the_literal(world: World, tmp_path: Path) -> None:
    """A digest-file strict literal in a blob fails by label and lines, never shown."""
    source = plant(world, {"notes.txt": f"one\nsee {STAND_IN} here\n".encode()})
    path = digest_file(tmp_path, window(STAND_IN, "private-project", "strict"))
    proc = tool(world, "scan", "--version", "0.1.0", "--digests", str(path),
                profile=scan_profile(world, source))  # fmt: skip
    assert proc.returncode == 1
    assert "FAIL private-project  notes.txt  x1 lines 2  (never allowlisted)" in proc.stdout
    assert STAND_IN not in proc.stdout + proc.stderr


def test_digest_file_literal_in_a_path_is_masked_everywhere(world: World, tmp_path: Path) -> None:
    """A digest-file literal in a path name reads <label> in every line; it is never printed."""
    leaky = f"docs/{STAND_IN}/{STAND_IN_HOST}.md"
    source = plant(world, {leaky: f"x\nby {STAND_IN_HOST}\n".encode()})
    path = digest_file(
        tmp_path,
        window(STAND_IN, "private-project", "strict"),
        window(STAND_IN_HOST, "host-name", "advisory"),
    )
    proc = tool(world, "scan", "--version", "0.1.0", "--digests", str(path),
                profile=scan_profile(world, source))  # fmt: skip
    assert proc.returncode == 1
    shown = "docs/<private-project>/<host-name>.md"
    fail = f"FAIL private-project  {shown} (path name)  x1 lines 1  (never allowlisted)"
    assert fail in proc.stdout
    assert f"advisory  host-name  {shown}:2" in proc.stdout
    assert STAND_IN not in proc.stdout + proc.stderr
    assert STAND_IN_HOST not in proc.stdout + proc.stderr


def test_raw_key_escaped_twice_still_fails() -> None:
    """A raw key inside a JSON string inside a JSON string (escaped twice) is still found."""
    inner = json.dumps({"stdout": json.dumps({"request" + "_id": "x"})})
    assert raw_key_hits(json.dumps({"capture": inner}).encode())


def test_advisory_window_and_regex_report_lines_only(world: World, tmp_path: Path) -> None:
    """An advisory window and a lookaround regex report file:line, never fail, never print text."""
    text = f"a\nrun on {STAND_IN_HOST}\nby zqaccount today\nsee /x/zqaccount/y\n".encode()
    source = plant(world, {"doc.md": text})
    path = digest_file(
        tmp_path,
        window(STAND_IN_HOST, "host-name", "advisory"),
        f"regex account-regex advisory {ACCOUNT_RULE}",
    )
    proc = tool(world, "scan", "--version", "0.1.0", "--digests", str(path),
                profile=scan_profile(world, source))  # fmt: skip
    assert proc.returncode == 0, proc.stdout
    assert "advisory  host-name  doc.md:2" in proc.stdout
    assert "advisory  account-regex  doc.md:3" in proc.stdout  # not line 4: the lookbehind
    assert STAND_IN_HOST not in proc.stdout and "zqaccount" not in proc.stdout


def test_allowlist_row_naming_a_digest_file_label_is_refused(world: World) -> None:
    """A digest-file label can never be allowlisted: load refuses the row."""
    row = {"path": "app.py", "pattern": "host-name", "count": 1, "reason": "r", "versions": "all"}
    profile = world.profile(allowlist=[row], required_digest_labels=["host-name"])
    refused(tool(world, "profile", "check", profile=profile), "may never be allowlisted")


# ----------------------------------------------------------------------------------- raw keys


def raw_key_hits(data: bytes, path: str = "docs/capture.json") -> list[str]:
    """The raw-key report lines of scanning one blob with the shipped profile."""
    profile = rx.load_profile(SHIPPED)
    result = rx.run_scan([rx.ScanItem(path, data, "blob")], profile, "0.6.3", rx.DigestFile())
    return [line for line in result.lines if line.split()[1:2] == [rx.RAW_KEY]]


def test_raw_key_fails_anywhere_and_inside_escaped_stdout() -> None:
    """A raw-only JSON key fails as a key, plain or inside an escaped stdout string, anywhere."""
    plain = ("{" + key("request" + "_id") + ': "x"}\n').encode()
    assert raw_key_hits(plain) and raw_key_hits(plain, "src/elsewhere/out.json")
    embedded = json.dumps({"stdout": json.dumps({"rate_limit" + "_info": {}})}).encode()
    assert raw_key_hits(embedded)


def test_raw_key_spares_shipped_code_and_values() -> None:
    """`rate_limited` is no raw key, and a raw key name as a VALUE (an event type) is no hit."""
    assert raw_key_hits(b'failure = "rate_limited"\n') == []
    value = json.dumps({"stdout": json.dumps({"type": "rate_limit" + "_event"})}).encode()
    assert raw_key_hits(value) == []


def test_cleaned_s11_captures_hold_no_raw_key() -> None:
    """The cleaned S11 captures give 0 raw-key hits; a planted raw key in a copy hits."""
    files = sorted((REPO_ROOT / "dev/tests/fixtures/recorded/S11").glob("*.json"))
    assert files
    for path in files:
        assert raw_key_hits(path.read_bytes(), path.name) == [], path.name
    data = json.loads(files[0].read_text())
    data["stdout"] = data["stdout"] + "\n" + json.dumps({"five" + "_hour": {}})
    assert raw_key_hits(json.dumps(data).encode(), files[0].name)


def test_raw_key_allowlist_only_for_the_profile_path(world: World) -> None:
    """A raw-key allowlist row on another path is refused at load."""
    row = {"path": "app.py", "pattern": "raw-key", "count": 1, "reason": "r", "versions": "all"}
    refused(tool(world, "profile", "check", profile=world.profile(allowlist=[row])),
            "may name only dev/release_export.yml")  # fmt: skip


# ------------------------------------------------------------------------------------ profile


def test_shipped_profile_is_v2_and_names_no_private_value() -> None:
    """The shipped profile loads as v2, requires the eight labels, and holds no home root."""
    profile = rx.load_profile(SHIPPED)
    assert set(profile.required_digest_labels) == {
        "private-project", "projects-dir", "host-name", "account-address", "account-regex",
        "evidence-root", "private-address", "notifier",
    }  # fmt: skip
    assert len(profile.scan.strict_keys) == 12
    data = SHIPPED.read_bytes()
    assert HOME_ROOT.encode() not in data and USERS_ROOT.encode() not in data
    assert not re.search(rb"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])", data)
    assert {p.label: p.literal for p in profile.scan.strict} == rx.TOOL_LITERALS


@pytest.mark.parametrize(
    ("change", "text"),
    [
        ({"schema": "hacf.release-export/v1"}, "schema must be hacf.release-export/v2"),
        ({"identity": {"name": "a" * 64, "email_from": "base"}}, "64-hex"),
        ({"expected_skips": [{"versions": "all", "nodeid": "x", "reason_contains": "b" * 64}]},
         "64-hex"),
        ({"scan": {"strict": [{"label": "home-dir", "literal": HOME_ROOT, "sha256": "x"}]}},
         "sha256 key"),
    ],
)  # fmt: skip
def test_v1_and_digests_are_refused(world: World, change: dict, text: str) -> None:
    """A v1 profile, a sha256 key or a 64-hex value anywhere is refused."""
    refused(tool(world, "profile", "check", profile=world.profile(**change)), text)


def test_no_tracked_file_holds_a_digest_of_a_digest_file_literal(tmp_path: Path) -> None:
    """The generic guard: no tracked 64-hex string equals a `window` digest of the digest file."""
    path = digest_file(
        tmp_path,
        window(STAND_IN, "private-project", "strict"),
        window(STAND_IN_HOST, "host-name", "advisory"),
    )
    wanted = {d for d, _, _, _ in rx.load_digests(str(path)).windows}
    listed = subprocess.run(["git", "-C", str(REPO_ROOT), "ls-files", "-z"],
                            capture_output=True, check=True).stdout  # fmt: skip
    found = set()
    for rel in filter(None, listed.decode().split("\0")):
        file = REPO_ROOT / rel
        if file.is_file() and not file.is_symlink():
            found |= set(re.findall(rb"[0-9a-f]{64}", file.read_bytes()))
    assert not {d.encode() for d in wanted} & found


def test_untracked_pycache_never_reaches_the_export() -> None:
    """The export is built from a commit's tree, so an ignored __pycache__/ never ships."""
    assert not [p for p in rx.tree_paths(REPO_ROOT, "HEAD") if "__pycache__" in p]


def test_old_rows_end_at_0_6_2() -> None:
    """The `<=0.6.2` rows cover 0.6.0-0.6.2 and not 0.6.3; the two new rows are `all`."""
    profile = rx.load_profile(SHIPPED)
    rows = {row.path: row.versions for row in profile.exclude}
    for path in ("dev/tests/rig_spec/test_brief.py", "dev/tests/fixtures/recorded/S11/",
                 "dev/tests/firewall/test_gate_hook.py"):  # fmt: skip
        assert rows[path] == "<=0.6.2"
        assert [rx.applies(rows[path], v) for v in ("0.6.0", "0.6.1", "0.6.2", "0.6.3")] == [
            True, True, True, False]  # fmt: skip
    assert rows["dev/tests/fixtures/recorded-raw/"] == rows["dev/tests/private_records/"] == "all"


# ------------------------------------------------------------- P18: binary rows (lead ruling)


def binary(world: World, digest: str, **extra) -> Path:
    """A profile with one binary allowlist row on app.py."""
    row = {"path": "app.py", "blob_sha256": digest, "reason": "r", "versions": "all", **extra}
    return world.profile(allowlist=[row])


def test_blob_row_bound_to_a_real_blob(world: World) -> None:
    """A binary row whose blob_sha256 is the sha256 of its path at a release passes."""
    blob = g(world.src, "cat-file", "blob", f"{world.sources['0.1.0']}:app.py") + "\n"
    proc = tool(world, "profile", "check", profile=binary(world, sha(blob)))
    assert proc.returncode == 0, proc.stderr


def test_blob_row_matching_no_blob_is_refused_unechoed(world: World) -> None:
    """A blob_sha256 that is no blob of its path refuses, naming the row index, never the value."""
    fake = hashlib.sha256(b"not any blob").hexdigest()
    proc = tool(world, "profile", "check", profile=binary(world, fake))
    refused(proc, "allowlist[0]: blob_sha256 is not the sha256 of its path")
    assert fake not in proc.stderr


def test_64_hex_elsewhere_in_a_binary_row_is_refused(world: World) -> None:
    """The exemption is the blob_sha256 field only: a 64-hex reason still refuses."""
    blob = g(world.src, "cat-file", "blob", f"{world.sources['0.1.0']}:app.py") + "\n"
    profile = binary(world, sha(blob), reason="c" * 64)
    refused(tool(world, "profile", "check", profile=profile), "64-hex")


def test_sha256_key_anywhere_is_refused(world: World) -> None:
    """A key named sha256 anywhere is refused (the v1 strict shape)."""
    doc = yaml.safe_load(world.profile().read_text())
    doc["expected_skips"][0]["sha256"] = "x"
    path = world.tmp / "with-sha256-key.yml"
    path.write_text(yaml.safe_dump(doc, sort_keys=False))
    refused(tool(world, "profile", "check", profile=path), "sha256 key")


def test_profile_check_reports_a_head_only_row_as_pending(world: World) -> None:
    """An exclude row whose path exists only at HEAD passes `profile check`, printed as pending."""
    (world.src / "only-head").mkdir()
    (world.src / "only-head" / "x.txt").write_text("new\n")
    g(world.src, "add", "--", "only-head/x.txt")
    g(world.src, "-c", "user.name=Dev", "-c", "user.email=dev@example.org",
      "-c", "commit.gpgsign=false", "commit", "-q", "-m", "a path only HEAD has")  # fmt: skip
    doc = yaml.safe_load(world.profile().read_text())
    row = {"path": "only-head/", "reason": "new at HEAD", "versions": "all"}
    profile = world.profile(exclude=[*doc["exclude"], row])
    proc = tool(world, "profile", "check", profile=profile)
    assert (proc.returncode, proc.stdout) == (
        0,
        "pending: exclude row only-head/ matches only HEAD (no release row ships it yet)\n"
        "profile ok\n",
    ), proc.stderr
