"""Compatibility adapter for historical Dashboard scripts that touch Memory logs."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


MAINTAINER = (
    Path(__file__).resolve().parents[2]
    / "codex-skills/codex-memory-maintainer/scripts/maintain_memory.py"
)
_SPEC = importlib.util.spec_from_file_location("codex_memory_log_contract", MAINTAINER)
if not _SPEC or not _SPEC.loader:
    raise RuntimeError(f"cannot load canonical Memory log contract: {MAINTAINER}")
_MODULE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _MODULE
_SPEC.loader.exec_module(_MODULE)


def insert_memory_log_entry(vault: Path, entry: str) -> bool:
    """Insert via the canonical sharded-log transaction; exact reruns are unchanged."""
    return _MODULE.insert_entry(vault, entry)


def replace_memory_log_text(vault: Path, old: str, new: str) -> bool:
    """Update one historical canonical entry and refresh every generated view."""
    return _MODULE.replace_entry_text(vault, old, new)
