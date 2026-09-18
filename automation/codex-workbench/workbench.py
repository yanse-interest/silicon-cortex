#!/usr/bin/env python3
"""Fail-closed local store for a single PROJECT_MAP.md authority.

This module deliberately has no dependency on the older dashboard registry or
its network services.  The registry here only binds stable IDs to roots; the
map is the sole business-state authority.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Iterator
from urllib.parse import urlparse

if os.name == "nt":
    import msvcrt
else:
    import fcntl

SCHEMA_VERSION = "1.0.0"
STATE_BEGIN = "<!-- WORKBENCH_STATE_BEGIN -->"
STATE_END = "<!-- WORKBENCH_STATE_END -->"
VIEW_BEGIN = "<!-- WORKBENCH_VIEW_BEGIN -->"
VIEW_END = "<!-- WORKBENCH_VIEW_END -->"
ID_RE = re.compile(r"^[a-z][a-z0-9_-]{2,79}$")
RELATIVE_RE = re.compile(r"^(?!/)(?!.*(?:^|/)\.\.(?:/|$))[^\\\x00]+$")
MAX_MAP_BYTES = 1024 * 1024


class WorkbenchError(RuntimeError):
    def __init__(self, code: str, message: str, current_revision: int | None = None):
        super().__init__(message)
        self.code, self.current_revision = code, current_revision

    def response(self) -> dict[str, Any]:
        value: dict[str, Any] = {"doc_type": "error_response", "schema_version": SCHEMA_VERSION,
                                 "code": self.code, "message": str(self)}
        if self.current_revision is not None:
            value["current_revision"] = self.current_revision
        return value


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _is_timestamp(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.tzinfo is not None and parsed.utcoffset() is not None
    except ValueError:
        return False


def _id(value: Any, label: str) -> None:
    if not isinstance(value, str) or not ID_RE.fullmatch(value):
        raise WorkbenchError("invalid_map", f"{label} must be a stable ID")


def _text(value: Any, label: str, limit: int = 4000) -> None:
    if not isinstance(value, str) or not value or len(value) > limit:
        raise WorkbenchError("invalid_map", f"{label} must be non-empty text")


def _object(value: Any, allowed: set[str], required: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) - allowed or required - set(value):
        raise WorkbenchError("invalid_map", f"{label} has missing or unknown fields")
    return value


def _relative(value: Any, label: str) -> None:
    if not isinstance(value, str) or not RELATIVE_RE.fullmatch(value):
        raise WorkbenchError("invalid_map", f"{label} must be a safe relative path")


def _checkpoint(value: Any, label: str) -> None:
    data = _object(value, {"saved_at", "last_result", "remaining", "next_action", "next_action_basis", "recheck_paths", "worktree_root", "git_head", "uncommitted_note"},
                   {"saved_at", "last_result", "remaining", "next_action", "next_action_basis"}, label)
    if not _is_timestamp(data["saved_at"]): raise WorkbenchError("invalid_map", f"{label}.saved_at is not a timestamp")
    _text(data["last_result"], f"{label}.last_result")
    if not isinstance(data["remaining"], list) or not all(isinstance(x, str) and x for x in data["remaining"]):
        raise WorkbenchError("invalid_map", f"{label}.remaining must be text list")
    basis = data["next_action_basis"]
    if basis not in {"existing_scope", "suggestion", "none"} or (basis == "none") != (data["next_action"] is None):
        raise WorkbenchError("invalid_map", f"{label}.next_action conflicts with its basis")
    if basis != "none": _text(data["next_action"], f"{label}.next_action")
    recheck_paths = data.get("recheck_paths", [])
    if not isinstance(recheck_paths, list): raise WorkbenchError("invalid_map", f"{label}.recheck_paths must be a list")
    for path in recheck_paths: _relative(path, f"{label}.recheck_paths")
    if "worktree_root" in data and (not isinstance(data["worktree_root"], str) or not
        (PurePosixPath(data["worktree_root"]).is_absolute() or PureWindowsPath(data["worktree_root"]).is_absolute())):
        raise WorkbenchError("invalid_map", f"{label}.worktree_root must be absolute")
    if "git_head" in data and (not isinstance(data["git_head"], str) or not re.fullmatch(r"[a-f0-9]{40,64}", data["git_head"])):
        raise WorkbenchError("invalid_map", f"{label}.git_head is invalid")


def _outcome(value: Any, label: str) -> None:
    data = _object(value, {"id", "title", "status", "parent_id", "depends_on", "acceptance", "priority", "deadline", "checkpoint", "evidence", "conversations", "pause_reason", "cancel_reason"},
                   {"id", "title", "status", "acceptance"}, label)
    _id(data["id"], f"{label}.id"); _text(data["title"], f"{label}.title", 160)
    status = data["status"]
    if status not in {"not_started", "in_progress", "paused", "pending_review", "done", "cancelled"}:
        raise WorkbenchError("invalid_map", f"{label}.status is invalid")
    if "parent_id" in data: _id(data["parent_id"], f"{label}.parent_id")
    if "priority" in data and data["priority"] not in {"high", "normal", "low"}:
        raise WorkbenchError("invalid_map", f"{label}.priority is invalid")
    if "deadline" in data:
        d = _object(data["deadline"], {"date", "timezone", "basis", "source_note"}, {"date", "timezone", "basis"}, f"{label}.deadline")
        try:
            if not isinstance(d["date"], str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d["date"]): raise ValueError
            datetime.strptime(d["date"], "%Y-%m-%d")
        except (TypeError, ValueError) as exc: raise WorkbenchError("invalid_map", f"{label}.deadline.date is invalid") from exc
        if d["timezone"] != "Asia/Shanghai" or d["basis"] not in {"user_confirmed", "external_commitment"}: raise WorkbenchError("invalid_map", f"{label}.deadline is invalid")
        if "source_note" in d: _text(d["source_note"], f"{label}.deadline.source_note")
    deps = data.get("depends_on", [])
    if not isinstance(deps, list) or len(set(deps)) != len(deps): raise WorkbenchError("invalid_map", f"{label}.depends_on must be unique")
    for dep in deps: _id(dep, f"{label}.depends_on")
    if not isinstance(data["acceptance"], list) or not data["acceptance"]: raise WorkbenchError("invalid_map", f"{label}.acceptance is required")
    evidence = data.get("evidence", [])
    if not isinstance(evidence, list): raise WorkbenchError("invalid_map", f"{label}.evidence must be a list")
    evidence_ids: set[str] = set()
    for item in evidence:
        e = _object(item, {"id", "kind", "locator", "summary", "verified_at", "verified_by"}, {"id", "kind", "locator", "summary", "verified_at", "verified_by"}, f"{label}.evidence")
        _id(e["id"], f"{label}.evidence.id"); _text(e["locator"], f"{label}.evidence.locator"); _text(e["summary"], f"{label}.evidence.summary")
        if e["id"] in evidence_ids or e["kind"] not in {"file", "commit", "conversation", "url", "user_confirmation"} or e["verified_by"] not in {"assistant", "user", "test_runner"} or not _is_timestamp(e["verified_at"]):
            raise WorkbenchError("invalid_map", f"{label}.evidence is invalid")
        if e["kind"] == "file": _relative(e["locator"], f"{label}.evidence.locator")
        if e["kind"] == "url":
            parsed_url = urlparse(e["locator"])
            if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc or parsed_url.username or parsed_url.password:
                raise WorkbenchError("invalid_map", f"{label}.evidence.locator must be a credential-free http or https URL")
        evidence_ids.add(e["id"])
    criterion_ids: set[str] = set()
    for criterion in data["acceptance"]:
        c = _object(criterion, {"id", "text", "result", "evidence_ids"}, {"id", "text", "result"}, f"{label}.acceptance")
        _id(c["id"], f"{label}.acceptance.id"); _text(c["text"], f"{label}.acceptance.text")
        if c["id"] in criterion_ids or c["result"] not in {"unverified", "passed", "failed"}: raise WorkbenchError("invalid_map", f"{label}.acceptance is invalid")
        refs = c.get("evidence_ids", [])
        if not isinstance(refs, list) or len(set(refs)) != len(refs) or any(ref not in evidence_ids for ref in refs):
            raise WorkbenchError("invalid_reference", f"{label}.acceptance references unknown evidence")
        if c["result"] == "passed" and not refs: raise WorkbenchError("invalid_map", f"{label}.passed acceptance needs evidence")
        criterion_ids.add(c["id"])
    if status in {"in_progress", "paused", "pending_review", "done"}:
        if "checkpoint" not in data: raise WorkbenchError("invalid_map", f"{label}.{status} needs checkpoint")
        _checkpoint(data["checkpoint"], f"{label}.checkpoint")
    if status == "paused": _text(data.get("pause_reason"), f"{label}.pause_reason")
    if status == "cancelled": _text(data.get("cancel_reason"), f"{label}.cancel_reason")
    conversations = data.get("conversations", [])
    if not isinstance(conversations, list): raise WorkbenchError("invalid_map", f"{label}.conversations must be a list")
    for c in conversations:
        ref = _object(c, {"thread_id", "host_id", "account_slot", "label"}, {"thread_id"}, f"{label}.conversations")
        _text(ref["thread_id"], f"{label}.conversations.thread_id", 160)
        if "host_id" in ref: _text(ref["host_id"], f"{label}.conversations.host_id", 160)
        if "account_slot" in ref and ref["account_slot"] not in {"account-1", "account-2"}: raise WorkbenchError("invalid_map", f"{label}.conversations.account_slot is invalid")
    if status == "done":
        cp = data["checkpoint"]
        if not evidence or any(c["result"] != "passed" for c in data["acceptance"]) or cp["remaining"] or cp["next_action"] is not None or cp["next_action_basis"] != "none":
            raise WorkbenchError("invalid_map", f"{label}.done has unmet completion gate")


def validate_map(data: Any) -> dict[str, Any]:
    m = _object(data, {"doc_type", "schema_version", "project_id", "revision", "updated_at", "title", "goal", "lifecycle", "architecture", "focus_outcome_id", "outcomes"},
                {"doc_type", "schema_version", "project_id", "revision", "updated_at", "title", "goal", "lifecycle", "outcomes"}, "project_map")
    if m["doc_type"] != "project_map" or m["schema_version"] != SCHEMA_VERSION: raise WorkbenchError("unsupported_version", "unsupported project map version")
    _id(m["project_id"], "project_id"); _text(m["title"], "title", 160); _text(m["goal"], "goal")
    if not isinstance(m["revision"], int) or isinstance(m["revision"], bool) or m["revision"] < 1 or not _is_timestamp(m["updated_at"]) or m["lifecycle"] not in {"active", "paused", "archived"}:
        raise WorkbenchError("invalid_map", "project map metadata is invalid")
    if not isinstance(m["outcomes"], list) or not m["outcomes"]: raise WorkbenchError("invalid_map", "outcomes is required")
    if "architecture" in m:
        arch = _object(m["architecture"], {"summary", "workflow", "document_paths"}, {"summary", "workflow"}, "architecture")
        _text(arch["summary"], "architecture.summary")
        if not isinstance(arch["workflow"], list) or not 1 <= len(arch["workflow"]) <= 20: raise WorkbenchError("invalid_map", "architecture.workflow is invalid")
        for item in arch["workflow"]: _text(item, "architecture.workflow item")
        paths = arch.get("document_paths", [])
        if not isinstance(paths, list) or len(set(paths)) != len(paths): raise WorkbenchError("invalid_map", "architecture.document_paths is invalid")
        for path in paths: _relative(path, "architecture.document_paths")
    for index, outcome in enumerate(m["outcomes"]): _outcome(outcome, f"outcomes[{index}]")
    updated_at = datetime.fromisoformat(m["updated_at"].replace("Z", "+00:00"))
    for index, outcome in enumerate(m["outcomes"]):
        checkpoint = outcome.get("checkpoint")
        if checkpoint and datetime.fromisoformat(checkpoint["saved_at"].replace("Z", "+00:00")) > updated_at:
            raise WorkbenchError("invalid_map", f"outcomes[{index}].checkpoint is newer than the map")
        for evidence in outcome.get("evidence", []):
            if datetime.fromisoformat(evidence["verified_at"].replace("Z", "+00:00")) > updated_at:
                raise WorkbenchError("invalid_map", f"outcomes[{index}].evidence is newer than the map")
    by_id = {x["id"]: x for x in m["outcomes"]}
    if len(by_id) != len(m["outcomes"]): raise WorkbenchError("invalid_reference", "outcome IDs must be unique")
    if "focus_outcome_id" in m and m["focus_outcome_id"] is not None and m["focus_outcome_id"] not in by_id: raise WorkbenchError("invalid_reference", "focus outcome is missing")
    def ancestors(oid: str) -> list[str]:
        seen: list[str] = []
        while by_id[oid].get("parent_id"):
            oid = by_id[oid]["parent_id"]
            if oid not in by_id or oid in seen: raise WorkbenchError("invalid_reference", "parent relationship has a cycle or missing ID")
            seen.append(oid)
            if len(seen) > 2: raise WorkbenchError("invalid_reference", "outcome tree exceeds three levels")
        return seen
    for oid, outcome in by_id.items():
        chain = ancestors(oid)
        for dep in outcome.get("depends_on", []):
            if dep not in by_id or dep == oid or dep in chain or oid in ancestors(dep): raise WorkbenchError("invalid_reference", "dependency is missing or crosses its tree")
    def visit(oid: str, visiting: set[str], visited: set[str]) -> None:
        if oid in visiting: raise WorkbenchError("invalid_reference", "dependency cycle")
        if oid not in visited:
            visiting.add(oid)
            for dep in by_id[oid].get("depends_on", []): visit(dep, visiting, visited)
            visiting.remove(oid); visited.add(oid)
    visited: set[str] = set()
    for oid in by_id: visit(oid, set(), visited)
    for oid, outcome in by_id.items():
        if outcome["status"] == "done":
            children = [x for x in by_id.values() if x.get("parent_id") == oid and x["status"] != "cancelled"]
            if any(x["status"] != "done" for x in children) or any(by_id[d]["status"] != "done" for d in outcome.get("depends_on", [])):
                raise WorkbenchError("invalid_map", f"done outcome {oid} has unfinished prerequisite")
    return m


def parse_map(raw: bytes) -> dict[str, Any]:
    if len(raw) > MAX_MAP_BYTES: raise WorkbenchError("invalid_map", "map exceeds size limit")
    try: text = raw.decode("utf-8")
    except UnicodeDecodeError as exc: raise WorkbenchError("invalid_map", "map is not UTF-8") from exc
    if text.count(STATE_BEGIN) != 1 or text.count(STATE_END) != 1:
        raise WorkbenchError("invalid_map", "map needs exactly one state block")
    start, end = text.index(STATE_BEGIN), text.index(STATE_END)
    if start >= end: raise WorkbenchError("invalid_map", "invalid state marker order")
    block = text[start + len(STATE_BEGIN):end].strip()
    match = re.fullmatch(r"```json\s*\n(.*?)\n```", block, re.DOTALL)
    if not match: raise WorkbenchError("invalid_map", "state block must be a json fenced block")
    def reject_duplicate(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result: raise WorkbenchError("invalid_map", f"duplicate JSON key {key}")
            result[key] = value
        return result
    try: data = json.loads(match.group(1), object_pairs_hook=reject_duplicate)
    except (json.JSONDecodeError, WorkbenchError) as exc:
        if isinstance(exc, WorkbenchError): raise
        raise WorkbenchError("invalid_map", "state JSON cannot be parsed") from exc
    return validate_map(data)


def render_map(existing: str, data: dict[str, Any]) -> bytes:
    state = STATE_BEGIN + "\n```json\n" + json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n```\n" + STATE_END
    summary = "\n".join([VIEW_BEGIN, "<!-- Generated from WORKBENCH_STATE; do not edit as authority. -->", f"# {data['title']}", "", data["goal"], "", "## Outcomes", *[f"- `{o['id']}` — {o['title']} ({o['status']})" for o in data["outcomes"]], VIEW_END])
    if existing.count(STATE_BEGIN) == 1 and existing.count(STATE_END) == 1:
        a = existing.index(STATE_BEGIN); b = existing.index(STATE_END) + len(STATE_END)
        text = existing[:a] + state + existing[b:]
    else: text = state + "\n\n" + existing.strip() + "\n"
    if text.count(VIEW_BEGIN) == 1 and text.count(VIEW_END) == 1:
        a = text.index(VIEW_BEGIN); b = text.index(VIEW_END) + len(VIEW_END)
        text = text[:a] + summary + text[b:]
    else: text = text.rstrip() + "\n\n" + summary + "\n"
    return text.encode("utf-8")


@contextmanager
def file_lock(path: Path) -> Iterator[None]:
    """Hold the first byte of a stable lock file across processes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        if os.name == "nt":
            handle.seek(0)
            deadline = time.monotonic() + 30
            while True:
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError as exc:
                    if time.monotonic() >= deadline:
                        raise WorkbenchError("lock_unavailable", f"timed out locking {path.name}") from exc
                    time.sleep(0.05)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class Store:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir.resolve()
        self.registry_path = self.data_dir / "project-registry.json"
        self.lock_dir = self.data_dir / "locks"; self.backup_dir = self.data_dir / "backups"

    def _registry(self) -> dict[str, Any]:
        try: raw = self.registry_path.read_bytes()
        except FileNotFoundError: return {"doc_type": "project_registry", "schema_version": SCHEMA_VERSION, "revision": 1, "updated_at": now(), "projects": []}
        try: value = json.loads(raw)
        except json.JSONDecodeError as exc: raise WorkbenchError("invalid_map", "private project registry is malformed") from exc
        if (not isinstance(value, dict) or value.get("doc_type") != "project_registry" or
                value.get("schema_version") != SCHEMA_VERSION or not isinstance(value.get("projects"), list) or
                not isinstance(value.get("revision"), int) or value["revision"] < 1):
            raise WorkbenchError("invalid_map", "private project registry is invalid")
        # Compatibility: prior registries have no visibility field and remain
        # active without rewriting the registry during an ordinary GET.
        for binding in value["projects"]:
            if not isinstance(binding, dict):
                raise WorkbenchError("invalid_map", "private project registry is invalid")
            if binding.get("visibility", "active") not in {"active", "archived"}:
                raise WorkbenchError("invalid_map", "private project registry is invalid")
        return value

    @contextmanager
    def _registry_lock(self) -> Iterator[None]:
        with file_lock(self.lock_dir / "project-registry.lock"):
            yield

    def _write_registry(self, registry: dict[str, Any]) -> None:
        registry["revision"] = int(registry["revision"]) + 1
        registry["updated_at"] = now()
        atomic_write(self.registry_path, json.dumps(registry, ensure_ascii=False, indent=2, sort_keys=True).encode() + b"\n")

    def _validated_binding(self, project_id: str, root: Path, map_path: str = "PROJECT_MAP.md") -> tuple[Path, Path, str]:
        _id(project_id, "project ID"); _relative(map_path, "map_path")
        if not root.is_absolute() or root.is_symlink() or not root.is_dir():
            raise WorkbenchError("path_outside_project", "project root must be an existing absolute non-symlink directory")
        actual_root = root.resolve(strict=True)
        target = (actual_root / map_path).resolve(strict=False)
        if not target.is_relative_to(actual_root):
            raise WorkbenchError("path_outside_project", "map path escapes project root")
        if target.exists():
            if not target.is_file(): raise WorkbenchError("path_outside_project", "map must be a regular file")
            mapped = parse_map(target.read_bytes())
            if mapped["project_id"] != project_id:
                raise WorkbenchError("invalid_reference", "map project ID differs from registration")
            return actual_root, target, "ready"
        return actual_root, target, "missing"

    def preview_binding(self, project_id: str, root: Path, map_path: str = "PROJECT_MAP.md") -> dict[str, Any]:
        actual_root, _, map_status = self._validated_binding(project_id, root, map_path)
        with self._registry_lock():
            registry = self._registry()
            if any(x.get("project_id") == project_id or x.get("root_path") == str(actual_root) for x in registry["projects"]):
                raise WorkbenchError("invalid_reference", "project ID or root already registered")
            return {"project_id": project_id, "root_path": str(actual_root), "map_path": map_path,
                    "map_status": map_status, "registry_revision": registry["revision"]}

    def apply_binding(self, project_id: str, root: Path, map_path: str, expected_registry_revision: int) -> dict[str, Any]:
        if not isinstance(expected_registry_revision, int):
            raise WorkbenchError("invalid_map", "invalid registry revision")
        actual_root, _, map_status = self._validated_binding(project_id, root, map_path)
        with self._registry_lock():
            registry = self._registry()
            if registry["revision"] != expected_registry_revision:
                raise WorkbenchError("revision_conflict", "project membership changed; preview again", registry["revision"])
            if any(x.get("project_id") == project_id or x.get("root_path") == str(actual_root) for x in registry["projects"]):
                raise WorkbenchError("invalid_reference", "project ID or root already registered")
            binding = {"project_id": project_id, "root_path": str(actual_root), "map_path": map_path,
                       "mapping_basis": "user_selected", "verified_at": now(),
                       "verified_worktrees": [str(actual_root)], "visibility": "active"}
            if map_status == "missing": binding["map_status"] = "missing"
            registry["projects"].append(binding)
            self._write_registry(registry)
            return {"project_id": project_id, "map_status": map_status, "registry_revision": registry["revision"]}

    def membership_view(self) -> dict[str, Any]:
        with self._registry_lock():
            registry = self._registry()
            rows = [{"project_id": item.get("project_id"), "root_path": item.get("root_path"),
                     "map_path": item.get("map_path"), "map_status": item.get("map_status", "ready"),
                     "visibility": item.get("visibility", "active")} for item in registry["projects"]]
            return {"registry_revision": registry["revision"], "projects": rows}

    def change_visibility(self, project_id: str, visibility: str, expected_registry_revision: int) -> dict[str, Any]:
        _id(project_id, "project ID")
        if visibility not in {"active", "archived"} or not isinstance(expected_registry_revision, int):
            raise WorkbenchError("invalid_map", "invalid membership change")
        with self._registry_lock():
            registry = self._registry()
            if registry["revision"] != expected_registry_revision:
                raise WorkbenchError("revision_conflict", "project membership changed; refresh first", registry["revision"])
            hits = [item for item in registry["projects"] if item.get("project_id") == project_id]
            if len(hits) != 1: raise WorkbenchError("not_found", "project is not registered")
            hits[0]["visibility"] = visibility
            self._write_registry(registry)
            return {"project_id": project_id, "visibility": visibility, "registry_revision": registry["revision"]}

    def remove_binding(self, project_id: str, expected_registry_revision: int) -> dict[str, Any]:
        _id(project_id, "project ID")
        if not isinstance(expected_registry_revision, int): raise WorkbenchError("invalid_map", "invalid registry revision")
        with self._registry_lock():
            registry = self._registry()
            if registry["revision"] != expected_registry_revision:
                raise WorkbenchError("revision_conflict", "project membership changed; refresh first", registry["revision"])
            before = len(registry["projects"])
            registry["projects"] = [item for item in registry["projects"] if item.get("project_id") != project_id]
            if len(registry["projects"]) != before - 1: raise WorkbenchError("not_found", "project is not registered")
            self._write_registry(registry)
            return {"project_id": project_id, "registry_revision": registry["revision"], "removed": True}

    @contextmanager
    def _lock(self, project_id: str) -> Iterator[None]:
        with file_lock(self.lock_dir / f"{project_id}.lock"):
            yield

    def _binding(self, project_id: str) -> dict[str, Any]:
        _id(project_id, "project ID")
        candidates = [x for x in self._registry()["projects"] if isinstance(x, dict) and x.get("project_id") == project_id]
        if len(candidates) != 1: raise WorkbenchError("not_found", f"project {project_id} is not registered")
        binding = candidates[0]
        root = Path(str(binding.get("root_path", "")))
        map_path = binding.get("map_path")
        if root.is_symlink() or not root.is_dir() or not isinstance(map_path, str): raise WorkbenchError("path_outside_project", "untrusted project binding")
        _relative(map_path, "map_path")
        resolved_root = root.resolve(strict=True)
        if str(resolved_root) != str(root): raise WorkbenchError("path_outside_project", "project root must not resolve through symlink")
        verified = binding.get("verified_worktrees")
        if not isinstance(verified, list) or str(resolved_root) not in verified:
            raise WorkbenchError("path_outside_project", "project root is not a verified worktree")
        target = (resolved_root / map_path).resolve(strict=False)
        if not target.is_relative_to(resolved_root): raise WorkbenchError("path_outside_project", "map path escapes project root")
        binding["_root"] = resolved_root; binding["_map"] = target
        return binding

    def register(self, project_id: str, root: Path, map_path: str = "PROJECT_MAP.md") -> None:
        _id(project_id, "project ID"); _relative(map_path, "map_path")
        if root.is_symlink() or not root.is_dir(): raise WorkbenchError("path_outside_project", "project root must be an existing non-symlink directory")
        # `resolve()` also normalizes macOS's /var -> /private/var alias.  Store
        # that canonical result; a direct symlink at the selected root remains
        # forbidden above, and all child paths are checked against this result.
        actual_root = root.resolve(strict=True)
        target = (actual_root / map_path).resolve(strict=False)
        if not target.is_relative_to(actual_root) or not target.is_file(): raise WorkbenchError("path_outside_project", "map must exist within project root")
        mapped = parse_map(target.read_bytes())
        if mapped["project_id"] != project_id: raise WorkbenchError("invalid_reference", "map project ID differs from registration")
        with self._registry_lock():
            registry = self._registry()
            if any(x.get("project_id") == project_id or x.get("root_path") == str(actual_root) for x in registry["projects"]): raise WorkbenchError("invalid_reference", "project ID or root already registered")
            registry["projects"].append({"project_id": project_id, "root_path": str(actual_root), "map_path": map_path, "mapping_basis": "user_selected", "verified_at": now(), "verified_worktrees": [str(actual_root)], "visibility": "active"})
            self._write_registry(registry)

    def register_preview(self, project_id: str, root: Path, map_path: str = "PROJECT_MAP.md") -> None:
        """Register a validated root whose map is deliberately absent.

        This is an M2 discovery record, not a second source of business state:
        it contains no title, outcome, status, checkpoint, or inferred result.
        It lets the private UI retain a visible, actionable error card until a
        user explicitly asks the M1 ``init`` flow to create a map.
        """
        _id(project_id, "project ID"); _relative(map_path, "map_path")
        if root.is_symlink() or not root.is_dir():
            raise WorkbenchError("path_outside_project", "project root must be an existing non-symlink directory")
        actual_root = root.resolve(strict=True)
        target = (actual_root / map_path).resolve(strict=False)
        if not target.is_relative_to(actual_root):
            raise WorkbenchError("path_outside_project", "map path escapes project root")
        if target.exists():
            raise WorkbenchError("invalid_reference", "map exists; use register after validating it")
        registry = self._registry()
        if any(x.get("project_id") == project_id or x.get("root_path") == str(actual_root) for x in registry["projects"]):
            raise WorkbenchError("invalid_reference", "project ID or root already registered")
        registry["projects"].append({"project_id": project_id, "root_path": str(actual_root), "map_path": map_path,
                                     "mapping_basis": "user_selected", "verified_at": now(),
                                     "verified_worktrees": [str(actual_root)], "map_status": "missing"})
        registry["revision"] = int(registry.get("revision", 0)) + 1; registry["updated_at"] = now()
        atomic_write(self.registry_path, json.dumps(registry, ensure_ascii=False, indent=2, sort_keys=True).encode() + b"\n")

    def promote_preview(self, project_id: str) -> None:
        """Turn an existing missing-map preview into a verified ready binding.

        This is deliberately narrower than registration: it may only promote
        the same pre-selected root after the map has been created and parsed.
        No project facts are copied into the private registry.
        """
        _id(project_id, "project ID")
        with self._lock(project_id):
            registry = self._registry()
            matches = [item for item in registry["projects"] if item.get("project_id") == project_id]
            if len(matches) != 1 or matches[0].get("map_status") != "missing":
                raise WorkbenchError("invalid_reference", "project is not a missing-map preview")
            binding = self._binding(project_id)
            try: mapped = parse_map(binding["_map"].read_bytes())
            except FileNotFoundError as exc: raise WorkbenchError("not_found", "preview map is still missing") from exc
            if mapped["project_id"] != project_id:
                raise WorkbenchError("invalid_reference", "map project ID differs from preview binding")
            matches[0].pop("map_status", None)
            matches[0]["verified_at"] = now()
            registry["revision"] = int(registry.get("revision", 0)) + 1; registry["updated_at"] = now()
            atomic_write(self.registry_path, json.dumps(registry, ensure_ascii=False, indent=2, sort_keys=True).encode() + b"\n")

    def read(self, project_id: str, outcome_id: str | None = None) -> dict[str, Any]:
        binding = self._binding(project_id); target: Path = binding["_map"]
        try: raw = target.read_bytes()
        except FileNotFoundError as exc: raise WorkbenchError("not_found", "registered map is missing") from exc
        mapped = parse_map(raw)
        if mapped["project_id"] != project_id: raise WorkbenchError("invalid_reference", "map differs from binding")
        result: dict[str, Any] = {"project_map": mapped, "sha256": sha256(raw)}
        if outcome_id:
            hits = [x for x in mapped["outcomes"] if x["id"] == outcome_id]
            if not hits: raise WorkbenchError("not_found", f"outcome {outcome_id} is not present")
            result["outcome"] = hits[0]
        return result

    def update(self, request: dict[str, Any]) -> dict[str, Any]:
        required = {"doc_type", "schema_version", "project_id", "expected_revision", "expected_sha256", "change_reason"}
        if not isinstance(request, dict) or request.get("doc_type") != "mutation_request" or request.get("schema_version") != SCHEMA_VERSION or required - set(request) or set(request) - (required | {"set_project", "upsert_outcomes"}):
            raise WorkbenchError("invalid_map", "invalid mutation request")
        if ("set_project" in request) == ("upsert_outcomes" in request) and not ("set_project" in request or "upsert_outcomes" in request): raise WorkbenchError("invalid_map", "mutation needs a change")
        _id(request["project_id"], "project_id"); _text(request["change_reason"], "change_reason")
        if not isinstance(request["expected_revision"], int) or not isinstance(request["expected_sha256"], str) or not re.fullmatch(r"[a-f0-9]{64}", request["expected_sha256"]): raise WorkbenchError("invalid_map", "invalid expected version")
        project_id = request["project_id"]
        with self._lock(project_id):
            binding = self._binding(project_id); path: Path = binding["_map"]
            raw = path.read_bytes(); current = parse_map(raw); actual_hash = sha256(raw)
            if current["revision"] != request["expected_revision"] or actual_hash != request["expected_sha256"]:
                raise WorkbenchError("revision_conflict", "map changed; read it again before updating", current["revision"])
            updated = copy.deepcopy(current)
            if "set_project" in request:
                patch = request["set_project"]
                if not isinstance(patch, dict) or not patch: raise WorkbenchError("invalid_map", "set_project must be non-empty")
                allowed = {"title", "goal", "lifecycle", "architecture", "focus_outcome_id"}
                if set(patch) - allowed: raise WorkbenchError("invalid_map", "set_project has unknown fields")
                updated.update(patch)
            if "upsert_outcomes" in request:
                items = request["upsert_outcomes"]
                if not isinstance(items, list) or not items: raise WorkbenchError("invalid_map", "upsert_outcomes must be non-empty")
                positions = {item["id"]: i for i, item in enumerate(updated["outcomes"])}
                for item in items:
                    if not isinstance(item, dict) or "id" not in item: raise WorkbenchError("invalid_map", "outcome update needs ID")
                    if item["id"] in positions: updated["outcomes"][positions[item["id"]]] = item
                    else: updated["outcomes"].append(item)
            old = {x["id"]: x for x in current["outcomes"]}; new = {x["id"]: x for x in updated["outcomes"]}
            allowed_transitions = {"not_started": {"not_started", "in_progress", "paused", "cancelled"}, "in_progress": {"in_progress", "paused", "pending_review", "done", "cancelled"}, "paused": {"paused", "in_progress", "cancelled"}, "pending_review": {"pending_review", "in_progress", "done", "paused", "cancelled"}, "done": {"done", "in_progress"}, "cancelled": {"cancelled", "not_started"}}
            for oid in old.keys() & new.keys():
                if new[oid].get("status") not in allowed_transitions.get(old[oid].get("status"), set()): raise WorkbenchError("invalid_transition", f"invalid transition for {oid}")
            updated["revision"] = current["revision"] + 1; updated["updated_at"] = now()
            validate_map(updated)
            atomic_write(self.backup_dir / f"{project_id}.md", raw)
            atomic_write(path, render_map(raw.decode("utf-8"), updated))
            persisted = self.read(project_id)
            return {"doc_type": "mutation_result", "schema_version": SCHEMA_VERSION, "project_id": project_id, "revision": persisted["project_map"]["revision"], "sha256": persisted["sha256"], "saved_at": now(), "index_status": "stale"}

    def restore_backup(self, project_id: str, expected_sha256: str) -> dict[str, Any]:
        """Restore the most recent valid backup using an exact-current-file CAS.

        The rejected current bytes are retained in the private data directory
        for inspection.  A corrupt map is never parsed or silently replaced
        without the caller proving which exact bytes it intends to recover.
        """
        _id(project_id, "project ID")
        if not isinstance(expected_sha256, str) or not re.fullmatch(r"[a-f0-9]{64}", expected_sha256):
            raise WorkbenchError("invalid_map", "invalid expected version")
        with self._lock(project_id):
            binding = self._binding(project_id); path: Path = binding["_map"]
            try: current_raw = path.read_bytes()
            except FileNotFoundError as exc: raise WorkbenchError("not_found", "registered map is missing") from exc
            if sha256(current_raw) != expected_sha256:
                raise WorkbenchError("revision_conflict", "map changed; inspect it again before recovery")
            backup_path = self.backup_dir / f"{project_id}.md"
            try: backup_raw = backup_path.read_bytes()
            except FileNotFoundError as exc: raise WorkbenchError("not_found", "no recovery backup is available") from exc
            backup = parse_map(backup_raw)
            if backup["project_id"] != project_id:
                raise WorkbenchError("invalid_reference", "backup differs from binding")
            rejected = self.backup_dir / f"{project_id}.rejected-{expected_sha256[:12]}.md"
            atomic_write(rejected, current_raw)
            atomic_write(path, backup_raw)
            persisted = self.read(project_id)
            return {"doc_type": "recovery_result", "schema_version": SCHEMA_VERSION,
                    "project_id": project_id, "revision": persisted["project_map"]["revision"],
                    "sha256": persisted["sha256"], "rejected_copy": rejected.name,
                    "restored_at": now()}


def atomic_write(path: Path, payload: bytes) -> None:
    name: str | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload); stream.flush(); os.fsync(stream.fileno())
        for attempt in range(6):
            try:
                os.replace(name, path)
                break
            except PermissionError:
                if os.name != "nt" or attempt == 5:
                    raise
                time.sleep(0.05)
        if os.name != "nt":
            directory = os.open(path.parent, os.O_RDONLY)
            try: os.fsync(directory)
            finally: os.close(directory)
    except Exception as exc:
        if name is not None:
            try: os.unlink(name)
            except FileNotFoundError: pass
        raise WorkbenchError("write_failed", f"atomic write failed for {path.name}") from exc


def new_map(project_id: str, title: str, goal: str, outcome_id: str, outcome_title: str) -> dict[str, Any]:
    _id(project_id, "project_id"); _id(outcome_id, "outcome_id")
    stamp = now()
    return {"doc_type": "project_map", "schema_version": SCHEMA_VERSION, "project_id": project_id, "revision": 1, "updated_at": stamp, "title": title, "goal": goal, "lifecycle": "active", "focus_outcome_id": outcome_id, "outcomes": [{"id": outcome_id, "title": outcome_title, "status": "in_progress", "priority": "high", "acceptance": [{"id": "map-closure", "text": "保存、重读并按 ID 接续成果", "result": "unverified"}], "checkpoint": {"saved_at": stamp, "last_result": "项目地图已初始化，等待实现或核验。", "remaining": ["完成并核验该成果的验收条件"], "next_action": "阅读成果检查点并继续当前范围内工作", "next_action_basis": "existing_scope"}}]}


def main() -> int:
    parser = argparse.ArgumentParser(description="local PROJECT_MAP.md workbench core")
    parser.add_argument("--data-dir", type=Path, required=True, help="private registry/lock/backup directory")
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init"); init.add_argument("--root", type=Path, required=True); init.add_argument("--project", required=True); init.add_argument("--title", required=True); init.add_argument("--goal", required=True); init.add_argument("--outcome", required=True); init.add_argument("--outcome-title", required=True); init.add_argument("--preview", action="store_true")
    reg = sub.add_parser("register"); reg.add_argument("--root", type=Path, required=True); reg.add_argument("--project", required=True); reg.add_argument("--map-path", default="PROJECT_MAP.md"); reg.add_argument("--preview-missing-map", action="store_true", help="register a validated root without creating or inferring a map")
    promote = sub.add_parser("promote-preview"); promote.add_argument("--project", required=True, help="validate and activate an existing missing-map preview")
    read = sub.add_parser("read"); read.add_argument("--project", required=True); read.add_argument("--outcome")
    update = sub.add_parser("update"); update.add_argument("--input", type=Path, required=True)
    restore = sub.add_parser("restore-backup"); restore.add_argument("--project", required=True); restore.add_argument("--expected-sha256", required=True)
    args = parser.parse_args(); store = Store(args.data_dir)
    try:
        if args.command == "init":
            target = args.root / "PROJECT_MAP.md"
            if target.exists(): raise WorkbenchError("write_failed", "init refuses to overwrite existing map")
            value = new_map(args.project, args.title, args.goal, args.outcome, args.outcome_title)
            payload = render_map("", value)
            if args.preview: print(json.dumps({"path": str(target), "sha256": sha256(payload), "project_map": value}, ensure_ascii=False, indent=2))
            else: atomic_write(target, payload); print(json.dumps({"created": str(target), "sha256": sha256(payload)}, ensure_ascii=False))
        elif args.command == "register":
            if args.preview_missing_map: store.register_preview(args.project, args.root, args.map_path)
            else: store.register(args.project, args.root, args.map_path)
            print(json.dumps({"registered": args.project, "map_status": "missing" if args.preview_missing_map else "ready"}))
        elif args.command == "promote-preview":
            store.promote_preview(args.project); print(json.dumps({"registered": args.project, "map_status": "ready"}))
        elif args.command == "read": print(json.dumps(store.read(args.project, args.outcome), ensure_ascii=False, indent=2))
        elif args.command == "update": print(json.dumps(store.update(json.loads(args.input.read_text(encoding="utf-8"))), ensure_ascii=False, indent=2))
        else: print(json.dumps(store.restore_backup(args.project, args.expected_sha256), ensure_ascii=False, indent=2))
        return 0
    except WorkbenchError as exc:
        print(json.dumps(exc.response(), ensure_ascii=False), file=__import__("sys").stderr); return 2
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps(WorkbenchError("write_failed", str(exc)).response(), ensure_ascii=False), file=__import__("sys").stderr); return 2


if __name__ == "__main__": raise SystemExit(main())
