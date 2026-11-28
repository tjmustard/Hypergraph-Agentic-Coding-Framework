"""The fake transcript record, importable without a fake world."""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path


def _load():
    os.environ.setdefault("HACF_FAKE_DIR", tempfile.gettempdir())
    spec = importlib.util.spec_from_file_location(
        "advisor_fakes", Path(__file__).with_name("fakes.py")
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


record = _load().record
