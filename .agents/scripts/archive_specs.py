#!/usr/bin/env python3
"""archive_specs.py — the only writer of spec/archive/: moves finished spec files there.

Usage:
    archive_specs.py <Feature_Name>
        Move every file in spec/active/ except .gitkeep into
        spec/archive/<YYYYmmdd_HHMMSS>_<Feature_Name>/ (non-alphanumerics become `_`).
    archive_specs.py --miniprd spec/compiled/MiniPRD_<Name>.md
        Move one compiled MiniPRD to spec/archive/MiniPRD_<Name>_AUDITED.md (/hyper-audit).
    archive_specs.py --paths <path> [<path> ...] --label <label>
        Move each named file into spec/archive/<YYYYmmdd_HHMMSS>_<label>/.

spec/ is the one beside this script's .agents/; --miniprd and --paths take paths relative to
the working directory and accept only regular files (no symlinks) inside that repository. Files
move byte-for-byte, an existing target is never overwritten, and spec/archive/ and the target
folder are created only when a file is about to move. Exit codes: 0 ok (also when there is
nothing to archive), 1 error or refusal with nothing moved, 2 usage of the flag forms.
"""

import argparse
import datetime
import os
import re
import shutil
import sys

import _runtime

MINIPRD_RE = re.compile(r"^MiniPRD_(.+)\.md$")


def archive_active_specs(feature_name: str):
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    active_dir = os.path.join(base_dir, "spec", "active")
    archive_dir = os.path.join(base_dir, "spec", "archive")

    if not os.path.exists(active_dir):
        print(f"ERROR: Active directory not found at {active_dir}")
        sys.exit(1)

    files_to_move = [
        f
        for f in os.listdir(active_dir)
        if f != ".gitkeep" and os.path.isfile(os.path.join(active_dir, f))
    ]

    if not files_to_move:
        print("INFO: No active specifications found to archive.")
        sys.exit(0)

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    sanitized_name = "".join([c if c.isalnum() else "_" for c in feature_name])
    target_folder = os.path.join(archive_dir, f"{timestamp}_{sanitized_name}")

    # spec/archive/ and the target folder are created only now that a file is about to move.
    if not os.path.exists(target_folder):
        os.makedirs(target_folder)

    for filename in files_to_move:
        src = os.path.join(active_dir, filename)
        dst = os.path.join(target_folder, filename)
        shutil.move(src, dst)
        print(f"Archived: {filename} -> {target_folder}/")

    print("=" * 50)
    print("ARCHIVAL SCRIPT: SUCCESS")
    print("=" * 50)
    print("Active directory flushed.")
    print("Artifacts permanently stored at absolute path:")
    print(f"{target_folder}/")
    print("=" * 50)


def _base_dir() -> str:
    """Return the repository root: the directory holding this script's .agents/."""
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _rel(path: str, base_dir: str) -> str:
    """Return path relative to the repository root, with forward slashes."""
    return os.path.relpath(path, base_dir).replace(os.sep, "/")


def _refuse(message: str) -> int:
    """Print an error in the positional form's style and return exit code 1."""
    print(f"ERROR: {message}")
    return 1


def archive_miniprd(path_arg: str) -> int:
    """Move spec/compiled/MiniPRD_<Name>.md to spec/archive/MiniPRD_<Name>_AUDITED.md.

    This is the end state of the v0.5.9 /hyper-audit Phase 4 `mv`. A symlink, a file outside
    spec/compiled/ or an existing target is refused with nothing moved.
    """
    base_dir = _base_dir()
    compiled_dir = os.path.join(base_dir, "spec", "compiled")
    archive_dir = os.path.join(base_dir, "spec", "archive")
    src = os.path.abspath(path_arg)
    name = os.path.basename(src)
    match = MINIPRD_RE.match(name)
    if match is None or name.endswith("_AUDITED.md"):
        return _refuse(f"{path_arg} is not a compiled MiniPRD_<Name>.md; nothing was moved")
    if os.path.realpath(os.path.dirname(src)) != os.path.realpath(compiled_dir):
        return _refuse(f"{path_arg} is not in {_rel(compiled_dir, base_dir)}/; nothing was moved")
    if os.path.islink(src) or not os.path.isfile(src):
        return _refuse(
            f"{path_arg} is not a regular file (symlinks are refused); nothing was moved"
        )
    dst = os.path.join(archive_dir, f"MiniPRD_{match.group(1)}_AUDITED.md")
    if os.path.lexists(dst):
        return _refuse(f"{_rel(dst, base_dir)} already exists; nothing was moved")
    os.makedirs(archive_dir, exist_ok=True)
    shutil.move(src, dst)
    print(f"Archived: {_rel(src, base_dir)} -> {_rel(dst, base_dir)}")
    return 0


def archive_paths(paths: list, label: str) -> int:
    """Move each named file into spec/archive/<timestamp>_<label>/ (all checked first)."""
    base_dir = _base_dir()
    real_base = os.path.realpath(base_dir)
    sanitized = "".join([c if c.isalnum() else "_" for c in label])
    if not sanitized:
        return _refuse("--label must not be empty")
    planned, names = [], set()
    for path_arg in paths:
        src = os.path.abspath(path_arg)
        if os.path.islink(src) or not os.path.isfile(src):
            return _refuse(
                f"{path_arg} is not a regular file (symlinks are refused); nothing was moved"
            )
        real = os.path.realpath(src)
        if not real.startswith(real_base + os.sep) or _rel(real, real_base).startswith(".git/"):
            return _refuse(f"{path_arg} lies outside the repository's files; nothing was moved")
        name = os.path.basename(src)
        if name in names:
            return _refuse(f"two paths are named {name}; nothing was moved")
        names.add(name)
        planned.append(src)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    target_folder = os.path.join(base_dir, "spec", "archive", f"{timestamp}_{sanitized}")
    for src in planned:
        dst = os.path.join(target_folder, os.path.basename(src))
        if os.path.lexists(dst):
            return _refuse(f"{_rel(dst, base_dir)} already exists; nothing was moved")
    os.makedirs(target_folder, exist_ok=True)
    for src in planned:
        dst = os.path.join(target_folder, os.path.basename(src))
        shutil.move(src, dst)
        print(f"Archived: {_rel(src, base_dir)} -> {_rel(dst, base_dir)}")
    return 0


def _main_flags(args: list) -> int:
    """Run the --miniprd or --paths form."""
    parser = argparse.ArgumentParser(
        prog="archive_specs.py",
        description="Move finished spec files into spec/archive/.",
        allow_abbrev=False,
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--miniprd", metavar="PATH", help="spec/compiled/MiniPRD_<Name>.md")
    mode.add_argument("--paths", nargs="+", metavar="PATH", help="files to archive together")
    parser.add_argument("--label", help="the archive folder label for --paths")
    try:
        ns = parser.parse_args(args)
    except SystemExit as exc:  # --help (0) or a usage error (2)
        return exc.code if isinstance(exc.code, int) else 2
    if ns.miniprd is not None:
        if ns.label is not None:
            return _refuse("--label goes with --paths only")
        return archive_miniprd(ns.miniprd)
    if ns.label is None:
        return _refuse("--paths needs --label <label>")
    return archive_paths(ns.paths, ns.label)


def main(argv: list) -> int:
    """Dispatch: a first argument starting with '-' selects a flag form, else positional."""
    if len(argv) < 2:
        print("Usage: python archive_specs.py <Feature_Name>")
        return 1
    if argv[1].startswith("-"):
        return _main_flags(argv[1:])
    archive_active_specs(argv[1])
    return 0


if __name__ == "__main__":
    _runtime.require_runtime()
    sys.exit(main(sys.argv))
