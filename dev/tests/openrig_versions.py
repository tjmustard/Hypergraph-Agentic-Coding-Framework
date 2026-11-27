"""Derived fake OpenRig versions for tests (OPR.99.0.12.2, mission R114-R125).

The tests used the real 0.7.22 as the "in range, not listed" rig, which broke the day a compat run
listed it. These helpers derive a version that no contract can list yet, from the contract itself.
"""

from __future__ import annotations

from typing import Any


def _first_range(contract: Any) -> tuple[int, int, int]:
    """Return the contract's first (major, minor, min_patch) range; ValueError when it has none."""
    ranges = getattr(contract, "range", ())
    if not ranges:
        raise ValueError("the contract has no range")
    major, minor, min_patch = ranges[0]
    return int(major), int(minor), int(min_patch)


def _first_patch(contract: Any) -> int:
    """The first patch no accepted entry of the range's minor line (any accepted_by) holds."""
    major, minor, min_patch = _first_range(contract)
    highest = min_patch
    for entry in contract.accepted:
        parts = entry.version.split(".")
        if (int(parts[0]), int(parts[1])) == (major, minor):
            highest = max(highest, int(parts[2]))
    return highest + 1000


def unlisted_patches(contract: Any, n: int) -> list[str]:
    """Return n consecutive in-range versions that the contract does not list."""
    major, minor, _ = _first_range(contract)
    first = _first_patch(contract)
    return [f"{major}.{minor}.{first + i}" for i in range(n)]


def unlisted_in_range(contract: Any) -> tuple[str, str]:
    """Return (version, rig_version_output) of an in-range version the contract does not list."""
    major, minor, _ = _first_range(contract)
    version = unlisted_patches(contract, 1)[0]
    patch = version.split(".")[2]
    return version, f"{version} (fake{major}{minor}{patch})"
