"""Static safety scan of .agents/scripts (stdlib ast only; no vermin, no mypy).

The scan reports, for every Python file under .agents/scripts:
- unsafe execution: a `shell=` keyword that is not literally False, `os.system`, `eval`, `exec`,
  and argv list literals of the form `['bash'|'sh', '-c', <non-literal>]`;
- 3.11-only syntax and APIs, because the skills run these scripts on the system Python 3.10.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from conftest import SCRIPTS_DIR

SHELLS = {"bash", "sh"}
SHELL_COMMAND_FLAG = re.compile(r"-[A-Za-z]*c[A-Za-z]*")  # -c, and combined flags like -ec

# Module attributes (and from-imports) that exist only from Python 3.11.
PY311_ONLY_ATTRS = {
    "asyncio": {"TaskGroup", "timeout"},
    "contextlib": {"chdir"},
    "datetime": {"UTC"},
    "enum": {"StrEnum", "verify", "member", "nonmember"},
    "hashlib": {"file_digest"},
    "logging": {"getLevelNamesMapping"},
    "operator": {"call"},
    "typing": {
        "Self",
        "Never",
        "LiteralString",
        "assert_never",
        "reveal_type",
        "Required",
        "NotRequired",
        "dataclass_transform",
    },
}
PY311_ONLY_MODULES = {"tomllib"}
PY311_ONLY_BUILTINS = {"ExceptionGroup", "BaseExceptionGroup"}


def _is_str(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def _shell_name(node: ast.AST) -> bool:
    return _is_str(node) and Path(node.value).name in SHELLS


def _module_aliases(tree: ast.AST) -> dict[str, str]:
    """Map each local name bound to `os` or a watched module to that module's name."""
    aliases = {name: name for name in (*PY311_ONLY_ATTRS, "os")}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in aliases and alias.asname:
                    aliases[alias.asname] = alias.name
    return aliases


def _os_system_names(tree: ast.AST) -> set[str]:
    """Return the local names that `from os import system [as x]` binds."""
    return {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "os"
        for alias in node.names
        if alias.name == "system"
    }


def _call_name(func: ast.AST, aliases: dict[str, str], from_os: set[str]) -> str:
    """Return 'eval', 'exec' or 'os.system' when func names one of them, else ''."""
    if isinstance(func, ast.Name):
        if func.id in ("eval", "exec"):
            return func.id
        if func.id in from_os:
            return "os.system"
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        owner = func.value.id
        if owner == "builtins" and func.attr in ("eval", "exec"):
            return func.attr
        if aliases.get(owner) == "os" and func.attr == "system":
            return "os.system"
    return ""


def scan_source(source: str, name: str = "<source>") -> list[str]:
    """Return one 'name:line: message' finding per unsafe or 3.11-only construct in source."""
    try:
        tree = ast.parse(source, filename=name, feature_version=(3, 10))
    except SyntaxError as exc:
        return [f"{name}:{exc.lineno}: does not parse as Python 3.10 ({exc.msg})"]

    findings: list[str] = []

    def report(node: ast.AST, message: str) -> None:
        findings.append(f"{name}:{getattr(node, 'lineno', 0)}: {message}")

    aliases = _module_aliases(tree)
    from_os = _os_system_names(tree)
    for node in ast.walk(tree):
        # -- unsafe execution ------------------------------------------------
        if isinstance(node, ast.Call):
            called = _call_name(node.func, aliases, from_os)
            if called:
                report(node, f"call to {called}")
            for keyword in node.keywords:
                if keyword.arg == "shell" and not (
                    isinstance(keyword.value, ast.Constant) and keyword.value.value is False
                ):
                    report(keyword.value, "shell= is not literally False")
        elif isinstance(node, (ast.List, ast.Tuple)) and len(node.elts) >= 3:
            first, flag, command = node.elts[:3]
            if (
                _shell_name(first)
                and _is_str(flag)
                and SHELL_COMMAND_FLAG.fullmatch(flag.value)
                and not _is_str(command)
            ):
                report(node, f"argv [{first.value!r}, {flag.value!r}, <non-literal>]")
        # -- 3.11-only syntax and APIs ----------------------------------------
        if type(node).__name__ == "TryStar":
            report(node, "except* is Python 3.11+")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in PY311_ONLY_MODULES:
                    report(node, f"import {alias.name} is Python 3.11+")
        elif isinstance(node, ast.ImportFrom) and node.module:
            module = node.module.split(".")[0]
            if module in PY311_ONLY_MODULES:
                report(node, f"from {node.module} import is Python 3.11+")
            for alias in node.names:
                if alias.name in PY311_ONLY_ATTRS.get(node.module, ()):
                    report(node, f"{node.module}.{alias.name} is Python 3.11+")
        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            module = aliases.get(node.value.id)
            if module and node.attr in PY311_ONLY_ATTRS.get(module, ()):
                report(node, f"{module}.{node.attr} is Python 3.11+")
        elif isinstance(node, ast.Name) and node.id in PY311_ONLY_BUILTINS:
            report(node, f"{node.id} is Python 3.11+")
    return findings


def _scripts() -> list[Path]:
    return sorted(path for path in SCRIPTS_DIR.rglob("*.py") if "__pycache__" not in path.parts)


def test_static_safety_scripts_clean():
    """Every script under .agents/scripts is free of unsafe and 3.11-only constructs."""
    assert _scripts(), f"no scripts found under {SCRIPTS_DIR}"
    findings = []
    for path in _scripts():
        rel = path.relative_to(SCRIPTS_DIR.parent.parent).as_posix()
        findings += scan_source(path.read_text(encoding="utf-8"), rel)
    assert findings == []


@pytest.mark.parametrize(
    "source",
    [
        "import subprocess\nsubprocess.run('ls', shell=True)\n",
        "import subprocess\nflag = True\nsubprocess.run('ls', shell=flag)\n",
        "import os\nos.system('ls')\n",
        "import os as o\no.system('ls')\n",
        "from os import system\nsystem('ls')\n",
        "eval('1 + 1')\n",
        "exec('x = 1')\n",
        "import builtins\nbuiltins.eval('1')\n",
    ],
)
def test_static_safety_reports_unsafe_calls(source):
    """shell=, os.system, eval and exec are reported."""
    assert scan_source(source), source


@pytest.mark.parametrize(
    "source",
    [
        "import subprocess\nsubprocess.run(['ls'], shell=False)\n",
        "import subprocess\nsubprocess.run(['ls'], check=True)\n",
        "import os\nos.getcwd()\n",
        "def evaluate(x):\n    return x\nevaluate(1)\n",
    ],
)
def test_static_safety_allows_safe_calls(source):
    """Ordinary subprocess and os calls are not reported."""
    assert scan_source(source) == []


def test_static_safety_bash_c():
    """A shell -c argv with a non-literal command is reported; a literal one is not."""
    reported = [
        "cmd = 'true'\nargv = ['bash', '-c', f'echo {cmd}']\n",
        "cmd = 'true'\nargv = ['sh', '-c', cmd]\n",
        "argv = ('bash', '-c', ' '.join(['a', 'b']))\n",
        "cmd = 'true'\nargv = ['/bin/bash', '-ec', cmd]\n",
    ]
    for source in reported:
        assert scan_source(source), source
    assert scan_source("argv = ['bash', '-c', 'true']\n") == []
    assert scan_source("argv = ['bash', 'script.sh', name]\n") == []


@pytest.mark.parametrize(
    "source",
    [
        "import tomllib\n",
        "from tomllib import loads\n",
        "import datetime\nnow = datetime.datetime.now(datetime.UTC)\n",
        "from datetime import UTC\n",
        "import datetime as dt\ndt.UTC\n",
        "from enum import StrEnum\n",
        "from typing import Self\n",
        "import typing\nx: typing.Self\n",
        "raise ExceptionGroup('x', [ValueError()])\n",
        "try:\n    pass\nexcept* ValueError:\n    pass\n",
        "import asyncio\nasyncio.TaskGroup()\n",
        "import hashlib\nhashlib.file_digest\n",
        "from contextlib import chdir\n",
        "import asyncio\nasyncio.timeout(1)\n",
        "import operator\noperator.call(print)\n",
        "import logging\nlogging.getLevelNamesMapping()\n",
        "from typing import Never\n",
        "from typing import LiteralString\n",
        "from typing import assert_never\n",
        "import typing\ntyping.reveal_type(1)\n",
        "from typing import Required\n",
        "from typing import NotRequired\n",
        "from typing import dataclass_transform\n",
        "import enum\nenum.verify\n",
        "from enum import member\n",
        "from enum import nonmember\n",
    ],
)
def test_static_safety_py311_apis(source):
    """Each 3.11-only API, import or syntax is reported."""
    assert scan_source(source), source


def test_static_safety_py310_apis_allowed():
    """Equivalents that exist on 3.10 (and typing_extensions) are not reported."""
    source = (
        "from datetime import datetime, timezone\n"
        "import typing\n"
        "from typing import Optional\n"
        "from typing_extensions import Self\n"
        "import enum\n"
        "x: typing.Optional[int] = None\n"
        "now = datetime.now(timezone.utc)\n"
        "class Color(enum.Enum):\n    RED = 1\n"
    )
    assert scan_source(source) == []
