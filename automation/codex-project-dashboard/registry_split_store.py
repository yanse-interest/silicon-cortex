#!/usr/bin/env python3
"""Journaled canonical split-store coordinator for the project Dashboard.

The manual store is the Stage-4 authority.  The replay store is rebuildable and
the historical registry path is a compatibility projection.  A small durable
journal makes the three ``os.replace`` operations recoverable as one logical
generation after an exception or process crash.
"""

from __future__ import annotations

import base64
import copy
import fcntl
import hashlib
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

from registry_shadow_split import (
    ShadowSplitError,
    pretty_bytes,
    protected_state_hash,
    recompose_registry,
    semantic_normalize,
    sha256_bytes,
    split_registry,
)


SCHEMA_VERSION = 1
TOOL_ID = "project-dashboard-registry-split-store"
DEFAULT_JOURNAL = Path("/private/tmp/codex-project-dashboard-split-store-transaction/journal.json")
DEFAULT_LOCK = Path("/private/tmp/codex-project-dashboard-split-store.lock")


class SplitStoreError(RuntimeError):
    """Fail-closed split-store consistency or transaction failure."""


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _capture(path: Path) -> dict[str, Any]:
    return {
        "exists": path.is_file(),
        "bytes_b64": base64.b64encode(path.read_bytes()).decode("ascii") if path.is_file() else "",
    }


def _restore(path: Path, state: dict[str, Any]) -> None:
    if state.get("exists"):
        _atomic_write(path, base64.b64decode(str(state.get("bytes_b64") or "")))
    elif path.exists():
        path.unlink()


@contextmanager
def _exclusive_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


class SplitStoreCoordinator:
    """Read and commit one canonical manual/replay/compatibility generation."""

    def __init__(
        self,
        *,
        manual_path: Path,
        replay_path: Path,
        compatibility_path: Path,
        journal_path: Path = DEFAULT_JOURNAL,
        lock_path: Path = DEFAULT_LOCK,
    ) -> None:
        self.manual_path = manual_path
        self.replay_path = replay_path
        self.compatibility_path = compatibility_path
        self.journal_path = journal_path
        self.lock_path = lock_path

    @property
    def enabled(self) -> bool:
        present = [self.manual_path.is_file(), self.replay_path.is_file()]
        if any(present) and not all(present):
            raise SplitStoreError("partial canonical split store")
        return all(present)

    def _recover_unlocked(self) -> bool:
        if not self.journal_path.is_file():
            return False
        try:
            journal = json.loads(self.journal_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SplitStoreError("unreadable split-store transaction journal") from exc
        if journal.get("schema_version") != SCHEMA_VERSION:
            raise SplitStoreError("unsupported split-store transaction journal")
        if journal.get("phase") == "committed":
            self.journal_path.unlink()
            return False
        before = journal.get("before") or {}
        for key, path in (
            ("manual", self.manual_path),
            ("replay", self.replay_path),
            ("compatibility", self.compatibility_path),
        ):
            state = before.get(key)
            if not isinstance(state, dict):
                raise SplitStoreError(f"transaction journal missing {key} recovery state")
            _restore(path, state)
        self.journal_path.unlink()
        return True

    def recover(self) -> bool:
        with _exclusive_lock(self.lock_path):
            return self._recover_unlocked()

    @staticmethod
    def _projections(registry: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        source_sha = sha256_bytes(pretty_bytes(registry))
        manual, replay, inventory = split_registry(copy.deepcopy(registry), source_sha)
        generation_id = "registry-gen-" + hashlib.sha256(
            (TOOL_ID + "\x1f" + source_sha).encode("utf-8")
        ).hexdigest()[:24]
        common = {
            "generation_id": generation_id,
            "source_registry_sha256": source_sha,
            "tool_id": TOOL_ID,
        }
        manual.update({
            **common,
            "artifact_type": "project_dashboard_manual_state",
            "classification": "canonical_state",
        })
        replay.update({
            **common,
            "artifact_type": "project_dashboard_replay_state",
            "classification": "derived_view",
        })
        recomposed = recompose_registry(manual, replay)
        if semantic_normalize(recomposed) != semantic_normalize(registry):
            raise SplitStoreError("split-store semantic parity failed")
        roundtrip_manual, _, _ = split_registry(recomposed, source_sha)
        if protected_state_hash(manual) != protected_state_hash(roundtrip_manual):
            raise SplitStoreError("split-store protected manual parity failed")
        inventory = copy.deepcopy(inventory)
        inventory.update({
            "generation_id": generation_id,
            "source_registry_sha256": source_sha,
            "manual_state_sha256": sha256_bytes(pretty_bytes(manual)),
            "replay_state_sha256": sha256_bytes(pretty_bytes(replay)),
            "compatibility_registry_sha256": sha256_bytes(pretty_bytes(recomposed)),
        })
        return manual, replay, {"registry": recomposed, "receipt": inventory}

    def _load_unlocked(self) -> dict[str, Any]:
        if not self.enabled:
            raise SplitStoreError("canonical split store is not initialized")
        try:
            manual = json.loads(self.manual_path.read_text(encoding="utf-8"))
            replay = json.loads(self.replay_path.read_text(encoding="utf-8"))
            compatibility = json.loads(self.compatibility_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SplitStoreError("canonical split generation is unreadable") from exc
        expected_metadata = (
            (
                "manual",
                manual,
                "project_dashboard_manual_state",
                "canonical_state",
            ),
            (
                "replay",
                replay,
                "project_dashboard_replay_state",
                "derived_view",
            ),
        )
        for label, projection, artifact_type, classification in expected_metadata:
            if projection.get("artifact_type") != artifact_type:
                raise SplitStoreError(f"invalid {label} split-store artifact type")
            if projection.get("classification") != classification:
                raise SplitStoreError(f"invalid {label} split-store classification")
            if projection.get("tool_id") != TOOL_ID:
                raise SplitStoreError(f"invalid {label} split-store tool identity")
        try:
            recomposed = recompose_registry(manual, replay)
        except ShadowSplitError as exc:
            raise SplitStoreError(f"canonical split generation mismatch: {exc}") from exc
        if semantic_normalize(recomposed) != semantic_normalize(compatibility):
            raise SplitStoreError("derived compatibility registry drift")
        compatibility_sha = sha256_bytes(pretty_bytes(compatibility))
        if manual.get("source_registry_sha256") != compatibility_sha:
            raise SplitStoreError("split-store source registry hash drift")
        return recomposed

    def load(self) -> dict[str, Any]:
        with _exclusive_lock(self.lock_path):
            self._recover_unlocked()
            return self._load_unlocked()

    def initialize_from_compatibility(self) -> dict[str, Any]:
        if not self.compatibility_path.is_file():
            raise SplitStoreError("compatibility registry is missing")
        with _exclusive_lock(self.lock_path):
            self._recover_unlocked()
            if self.enabled:
                return self._load_unlocked()
            registry = json.loads(self.compatibility_path.read_text(encoding="utf-8"))
            result = self._write_unlocked(registry, writer_role="stage4_cutover_initialization")
            return result["registry"]

    def write(
        self,
        registry: dict[str, Any],
        *,
        writer_role: str,
        inject_failure: str = "",
        after_replace: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        with _exclusive_lock(self.lock_path):
            self._recover_unlocked()
            return self._write_unlocked(
                registry,
                writer_role=writer_role,
                inject_failure=inject_failure,
                after_replace=after_replace,
            )

    def _write_unlocked(
        self,
        registry: dict[str, Any],
        *,
        writer_role: str,
        inject_failure: str = "",
        after_replace: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        manual, replay, result = self._projections(registry)
        compatibility = result["registry"]
        receipt = result["receipt"]
        target_bytes = {
            "manual": pretty_bytes(manual),
            "replay": pretty_bytes(replay),
            "compatibility": pretty_bytes(compatibility),
        }
        paths = {
            "manual": self.manual_path,
            "replay": self.replay_path,
            "compatibility": self.compatibility_path,
        }
        journal = {
            "schema_version": SCHEMA_VERSION,
            "artifact_type": "project_dashboard_split_store_transaction",
            "phase": "prepared",
            "generation_id": receipt["generation_id"],
            "writer_role": writer_role,
            "before": {key: _capture(path) for key, path in paths.items()},
            "target_sha256": {key: sha256_bytes(value) for key, value in target_bytes.items()},
        }
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(self.journal_path, pretty_bytes(journal))
        try:
            if inject_failure == "before_replace":
                raise SplitStoreError("injected failure before split-store replace")
            for key in ("manual", "replay", "compatibility"):
                _atomic_write(paths[key], target_bytes[key])
                if after_replace:
                    after_replace(key)
                if inject_failure == f"after_{key}":
                    raise SplitStoreError(f"injected failure after {key} replace")
            loaded = self._load_unlocked()
            if semantic_normalize(loaded) != semantic_normalize(compatibility):
                raise SplitStoreError("post-commit split-store validation failed")
            journal["phase"] = "committed"
            _atomic_write(self.journal_path, pretty_bytes(journal))
            self.journal_path.unlink()
        except Exception:
            self._recover_unlocked()
            raise
        return {
            **receipt,
            "writer_role": writer_role,
            "journal_residue": self.journal_path.exists(),
            "registry": loaded,
        }
