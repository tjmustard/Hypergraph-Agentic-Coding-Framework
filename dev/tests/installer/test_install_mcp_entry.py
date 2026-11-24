"""An upgrade leaves the .mcp.json code-graph entry as HACF last wrote it (OPR.99.0.10.4).

v064-upgrade-path US-013 (NEW-1, F21, C8 items 1 and 3). Offline: the temp upstream and the
shims of conftest; the code-graph install itself is never consented, so nothing is installed
and the method comes from `.agents/local/hacf.yml` (MR 1).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml

from .test_install import report_lines

CG = "--code-graph=codebase-memory-mcp"
MCP = ".mcp.json"
HACF_YML = ".agents/local/hacf.yml"
MANIFEST_REL = ".agents/hacf-manifest.json"
BARE = {"type": "stdio", "command": "codebase-memory-mcp", "args": []}
UVX_OLD = {"type": "stdio", "command": "uvx", "args": ["codebase-memory-mcp@0.12.0"]}
UVX_NOW = {"type": "stdio", "command": "uvx", "args": ["codebase-memory-mcp@0.12.1"]}
USER = {"type": "stdio", "command": "uvx", "args": ["codebase-memory-mcp@0.12.1", "--mine"]}


def servers(project: Path) -> dict:
    """The .mcp.json mcpServers object."""
    return json.loads((project / MCP).read_text(encoding="utf-8")).get("mcpServers", {})


def set_method(project: Path, method: str) -> None:
    """Record code_graph.install_method in .agents/local/hacf.yml."""
    path = project / HACF_YML
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    data.setdefault("code_graph", {})["install_method"] = method
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def set_entry(project: Path, entry: dict, indent: int = 2) -> bytes:
    """Write .mcp.json with this codebase-memory-mcp entry (and a foreign one); return its bytes."""
    doc = {"mcpServers": {"foreign": {"command": "foreign-mcp"}, "codebase-memory-mcp": entry}}
    data = (json.dumps(doc, indent=indent) + "\n").encode()
    (project / MCP).write_bytes(data)
    return data


def entry_line(run) -> str:
    """The report's one code-graph MCP entry line."""
    (line,) = [x for x in report_lines(run.output) if "code graph MCP entry:" in x]
    return line.strip().removeprefix("- ")


def installed(project: Path, run_install) -> None:
    """A first install with the code-graph backend selected (no consent: nothing installed)."""
    run = run_install(project, "-y", "--ides=claude", CG)
    assert run.returncode == 0, run.output
    assert servers(project)["codebase-memory-mcp"] == UVX_NOW
    assert entry_line(run) == "code graph MCP entry: written: uvx codebase-memory-mcp@0.12.1"


def test_a_bare_uv_tool_entry_is_byte_identical(project: Path, run_install) -> None:
    """(a) A bare entry with install_method uv-tool: the re-install leaves .mcp.json's bytes."""
    installed(project, run_install)
    set_method(project, "uv-tool")
    before = set_entry(project, BARE, indent=4)  # unusual formatting: it must survive too
    run = run_install(project, "-y", "--ides=claude", CG)
    assert run.returncode == 0, run.output
    after = (project / MCP).read_bytes()
    assert hashlib.sha256(after).hexdigest() == hashlib.sha256(before).hexdigest()
    assert entry_line(run) == "code graph MCP entry: unchanged: codebase-memory-mcp"


def test_b_old_hacf_uvx_entry_is_upgraded(project: Path, run_install) -> None:
    """(b) A HACF `uvx …@0.12.0` entry with uvx recorded is upgraded to the current pin."""
    installed(project, run_install)
    set_method(project, "uvx")
    set_entry(project, UVX_OLD)
    run = run_install(project, "-y", "--ides=claude", CG)
    assert run.returncode == 0, run.output
    assert servers(project)["codebase-memory-mcp"] == UVX_NOW
    assert servers(project)["foreign"] == {"command": "foreign-mcp"}
    assert entry_line(run) == "code graph MCP entry: written: uvx codebase-memory-mcp@0.12.1"


def test_c_user_entry_is_kept(project: Path, run_install) -> None:
    """(c) A user's entry (different args) is kept, byte for byte, and the report says so."""
    installed(project, run_install)
    before = set_entry(project, USER)
    run = run_install(project, "-y", "--ides=claude", CG)
    assert run.returncode == 0, run.output
    assert (project / MCP).read_bytes() == before
    line = entry_line(run)
    assert line == "code graph MCP entry: kept (user's): uvx codebase-memory-mcp@0.12.1 --mine"


def test_d_no_mcp_entry_then_plain_rerun(project: Path, run_install) -> None:
    """(d) --no-mcp-entry on a fresh install, then a plain re-run: no entry both times."""
    run = run_install(project, "-y", "--ides=claude", CG, "--no-mcp-entry")
    assert run.returncode == 0, run.output
    assert not (project / MCP).exists() or "codebase-memory-mcp" not in servers(project)
    manifest = json.loads((project / MANIFEST_REL).read_text(encoding="utf-8"))
    assert manifest["mcp_entry"] == "skipped"
    assert entry_line(run) == "code graph MCP entry: skipped: uvx codebase-memory-mcp@0.12.1"
    run = run_install(project, "-y", "--ides=claude", CG)
    assert run.returncode == 0, run.output
    assert not (project / MCP).exists() or "codebase-memory-mcp" not in servers(project)
    manifest = json.loads((project / MANIFEST_REL).read_text(encoding="utf-8"))
    assert manifest["mcp_entry"] == "skipped"
    assert entry_line(run).startswith("code graph MCP entry: skipped: ")


def test_d_no_mcp_entry_leaves_an_existing_entry(project: Path, run_install) -> None:
    """--no-mcp-entry leaves an existing entry alone, even an old HACF one."""
    installed(project, run_install)
    before = set_entry(project, UVX_OLD)
    run = run_install(project, "-y", "--ides=claude", CG, "--no-mcp-entry")
    assert run.returncode == 0, run.output
    assert (project / MCP).read_bytes() == before


def test_e_mcp_entry_reverses_the_skip(project: Path, run_install) -> None:
    """(e) --mcp-entry after a recorded skip writes the entry and clears the record."""
    run = run_install(project, "-y", "--ides=claude", CG, "--no-mcp-entry")
    assert run.returncode == 0, run.output
    run = run_install(project, "-y", "--ides=claude", CG, "--mcp-entry")
    assert run.returncode == 0, run.output
    assert servers(project)["codebase-memory-mcp"] == UVX_NOW
    manifest = json.loads((project / MANIFEST_REL).read_text(encoding="utf-8"))
    assert "mcp_entry" not in manifest
    assert entry_line(run) == "code graph MCP entry: written: uvx codebase-memory-mcp@0.12.1"


def test_help_names_both_flags(project: Path, run_install) -> None:
    """--help lists --no-mcp-entry and --mcp-entry."""
    run = run_install(project, "--help")
    assert "--no-mcp-entry" in run.output and "--mcp-entry" in run.output
