#!/usr/bin/env python3
"""Collect bounded Codex daily evidence through the official app-server RPC.

The controller treats the progress bridge as a selector only. It resolves exact
thread/turn IDs with ``thread/read`` and emits a bounded summary projection; it
never writes or returns full transcripts, command bodies, tool responses,
approval payloads, or reasoning content.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import dataclasses
import datetime as dt
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


CONTROLLER_VERSION = 2
BRIDGE_SCHEMA_VERSION = 1
DEFAULT_TIMEZONE = "Asia/Shanghai"
MAX_WORKERS = 4
MAX_ATTEMPTS = 3
MAX_GOAL_CHARS = 1600
MAX_RESULT_CHARS = 4000
MAX_PATHS = 40
MAX_ERROR_CHARS = 500

SECRET_PATTERNS = (
    re.compile(r"(?i)(api[_-]?key|access[_-]?token|refresh[_-]?token|password|passwd|secret)\s*[:=]\s*[^\s,;]+"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{12,}=*\b", re.IGNORECASE),
)


class ControllerError(RuntimeError):
    def __init__(self, code: str, message: str, *, classification: str) -> None:
        super().__init__(message)
        self.code = code
        self.classification = classification


class RpcError(RuntimeError):
    def __init__(self, code: int | None, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclasses.dataclass(frozen=True)
class TaskRef:
    thread_id: str
    turn_id: str
    bridge_status: str
    started_at: str
    updated_at: str
    ended_at: str
    cwd: str
    changed_files: tuple[str, ...]

    @property
    def key(self) -> tuple[str, str]:
        return self.thread_id, self.turn_id


@dataclasses.dataclass(frozen=True)
class Manifest:
    target_date: str
    generated_at: str
    retention_days: int
    references_seen: int
    null_turn_references: int
    missing_thread_references: int
    duplicate_references: int
    refs: tuple[TaskRef, ...]

    @property
    def thread_ids(self) -> tuple[str, ...]:
        return tuple(sorted({ref.thread_id for ref in self.refs}))


def _redact(value: str) -> str:
    normalized = value.replace("\x00", "").strip()
    for pattern in SECRET_PATTERNS:
        normalized = pattern.sub(lambda match: f"{match.group(1)}=[REDACTED]" if match.lastindex else "[REDACTED]", normalized)
    return normalized


def _bounded(value: str, limit: int) -> str:
    value = _redact(value)
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)].rstrip() + "…"


def _parse_iso(value: str) -> dt.datetime:
    if not value:
        raise ValueError("missing timestamp")
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must contain timezone")
    return parsed


def _effective_date(task: dict[str, Any], timezone: ZoneInfo) -> str:
    value = str(task.get("ended_at") or task.get("updated_at") or "")
    return _parse_iso(value).astimezone(timezone).date().isoformat()


def build_manifest(bridge: dict[str, Any], target_date: str, timezone_name: str) -> Manifest:
    if bridge.get("schema_version") != BRIDGE_SCHEMA_VERSION or not isinstance(bridge.get("tasks"), list):
        raise ControllerError(
            "unsupported_bridge_schema",
            "progress bridge must use schema_version 1 with a tasks array",
            classification="structural",
        )
    try:
        dt.date.fromisoformat(target_date)
        timezone = ZoneInfo(timezone_name)
    except (ValueError, KeyError) as exc:
        raise ControllerError("invalid_target_date", str(exc), classification="structural") from exc

    selected: list[dict[str, Any]] = []
    for task in bridge["tasks"]:
        if not isinstance(task, dict):
            continue
        try:
            if _effective_date(task, timezone) == target_date:
                selected.append(task)
        except ValueError:
            continue

    refs: dict[tuple[str, str], TaskRef] = {}
    null_turns = 0
    missing_threads = 0
    duplicates = 0
    for task in selected:
        thread_id = str(task.get("thread_id") or "").strip()
        turn_id = str(task.get("turn_id") or "").strip()
        if not thread_id:
            missing_threads += 1
            continue
        if not turn_id:
            null_turns += 1
            continue
        paths = []
        for item in task.get("changed_files") or []:
            if isinstance(item, dict) and item.get("path"):
                paths.append(str(item["path"]))
            elif isinstance(item, str):
                paths.append(item)
        ref = TaskRef(
            thread_id=thread_id,
            turn_id=turn_id,
            bridge_status=str(task.get("status") or "unknown"),
            started_at=str(task.get("started_at") or ""),
            updated_at=str(task.get("updated_at") or ""),
            ended_at=str(task.get("ended_at") or ""),
            cwd=str(task.get("cwd") or ""),
            changed_files=tuple(dict.fromkeys(paths))[:MAX_PATHS],
        )
        if ref.key in refs:
            duplicates += 1
        else:
            refs[ref.key] = ref

    return Manifest(
        target_date=target_date,
        generated_at=str(bridge.get("generated_at") or ""),
        retention_days=int(bridge.get("retention_days") or 0),
        references_seen=len(selected),
        null_turn_references=null_turns,
        missing_thread_references=missing_threads,
        duplicate_references=duplicates,
        refs=tuple(sorted(refs.values(), key=lambda ref: (ref.thread_id, ref.turn_id))),
    )


def _default_codex_binary() -> Path:
    candidates = (
        Path("/Applications/ChatGPT.app/Contents/Resources/codex"),
        Path("/Applications/Codex.app/Contents/Resources/codex"),
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    resolved = shutil.which("codex")
    if resolved:
        return Path(resolved)
    raise ControllerError("codex_binary_missing", "bundled Codex CLI was not found", classification="structural")


class AppServerClient:
    def __init__(self, binary: Path, codex_home: Path, request_timeout: float) -> None:
        self.binary = binary
        self.codex_home = codex_home
        self.request_timeout = request_timeout
        self._next_id = 1
        self._id_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._pending: dict[int, queue.Queue[dict[str, Any]]] = {}
        self._pending_lock = threading.Lock()
        self._stderr_tail: list[str] = []
        env = os.environ.copy()
        env["CODEX_HOME"] = str(codex_home)
        try:
            self.process = subprocess.Popen(
                [str(binary), "app-server", "--stdio"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                bufsize=1,
                env=env,
            )
        except OSError as exc:
            raise ControllerError("app_server_start_failed", str(exc), classification="structural") from exc
        assert self.process.stdout and self.process.stderr
        self._reader = threading.Thread(target=self._read_stdout, daemon=True)
        self._stderr_reader = threading.Thread(target=self._read_stderr, daemon=True)
        self._reader.start()
        self._stderr_reader.start()

    def _read_stdout(self) -> None:
        assert self.process.stdout
        for line in self.process.stdout:
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            request_id = message.get("id")
            if not isinstance(request_id, int):
                continue
            with self._pending_lock:
                waiter = self._pending.get(request_id)
            if waiter:
                waiter.put(message)

    def _read_stderr(self) -> None:
        assert self.process.stderr
        for line in self.process.stderr:
            safe = _bounded(line, MAX_ERROR_CHARS)
            if safe:
                self._stderr_tail.append(safe)
                del self._stderr_tail[:-8]

    def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if self.process.poll() is not None:
            detail = "; ".join(self._stderr_tail[-2:])
            raise ControllerError("app_server_exited", detail or "app-server exited", classification="structural")
        with self._id_lock:
            request_id = self._next_id
            self._next_id += 1
        waiter: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
        with self._pending_lock:
            self._pending[request_id] = waiter
        payload = json.dumps({"id": request_id, "method": method, "params": params}, ensure_ascii=False)
        assert self.process.stdin
        try:
            with self._write_lock:
                self.process.stdin.write(payload + "\n")
                self.process.stdin.flush()
            message = waiter.get(timeout=self.request_timeout)
        except queue.Empty as exc:
            raise TimeoutError(f"RPC timeout: {method}") from exc
        except (BrokenPipeError, OSError) as exc:
            raise ControllerError("app_server_io_failed", str(exc), classification="structural") from exc
        finally:
            with self._pending_lock:
                self._pending.pop(request_id, None)
        if "error" in message:
            error = message.get("error") or {}
            raise RpcError(error.get("code"), str(error.get("message") or error))
        result = message.get("result")
        if not isinstance(result, dict):
            raise ControllerError("invalid_rpc_response", method, classification="structural")
        return result

    def initialize(self) -> dict[str, Any]:
        result = self.request(
            "initialize",
            {
                "clientInfo": {
                    "name": "codex-daily-evidence-controller",
                    "title": "Codex Daily Evidence Controller",
                    "version": str(CONTROLLER_VERSION),
                },
                "capabilities": {"experimentalApi": True},
            },
        )
        assert self.process.stdin
        with self._write_lock:
            self.process.stdin.write(json.dumps({"method": "initialized", "params": {}}) + "\n")
            self.process.stdin.flush()
        return result

    def close(self) -> None:
        if self.process.poll() is None and self.process.stdin:
            try:
                self.process.stdin.close()
            except OSError:
                pass
        if self.process.poll() is None:
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=2)
        for stream in (self.process.stdout, self.process.stderr):
            if stream:
                stream.close()
        self._reader.join(timeout=1)
        self._stderr_reader.join(timeout=1)

    def __enter__(self) -> "AppServerClient":
        self.initialize()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def classify_failure(exc: BaseException) -> tuple[str, str]:
    message = str(exc).lower()
    if isinstance(exc, TimeoutError):
        return "transient", "rpc_timeout"
    if isinstance(exc, RpcError):
        if exc.code == -32601 or "method not found" in message or "no handler" in message:
            return "structural", "rpc_method_unavailable"
        if any(marker in message for marker in ("busy", "locked", "temporarily unavailable", "try again")):
            return "transient", "rpc_temporarily_unavailable"
        if any(marker in message for marker in ("not found", "not loaded", "does not exist")):
            return "data", "thread_unavailable"
        return "structural", "rpc_error"
    if isinstance(exc, ControllerError):
        return exc.classification, exc.code
    return "structural", "unexpected_controller_error"


def _read_with_retry(
    client: AppServerClient,
    thread_id: str,
    *,
    include_turns: bool,
    max_attempts: int,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    last_error: BaseException | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            result = client.request("thread/read", {"threadId": thread_id, "includeTurns": include_turns})
            return result, {"status": "ok", "attempts": attempt, "error_class": None, "error_code": None}
        except BaseException as exc:  # normalized below; never expose raw payloads
            last_error = exc
            classification, code = classify_failure(exc)
            if classification != "transient" or attempt >= max_attempts:
                return None, {
                    "status": "failed",
                    "attempts": attempt,
                    "error_class": classification,
                    "error_code": code,
                    "error": _bounded(str(exc), MAX_ERROR_CHARS),
                }
            time.sleep(0.2 * attempt)
    raise AssertionError(last_error)


def _text_from_user_item(item: dict[str, Any]) -> str:
    parts = []
    for content in item.get("content") or []:
        if isinstance(content, dict) and content.get("type") == "text":
            parts.append(str(content.get("text") or ""))
    return "\n".join(part for part in parts if part)


def _project_turn(turn: dict[str, Any], ref: TaskRef) -> dict[str, Any]:
    items = [item for item in (turn.get("items") or []) if isinstance(item, dict)]
    goals = [_text_from_user_item(item) for item in items if item.get("type") == "userMessage"]
    agent_messages = [item for item in items if item.get("type") == "agentMessage"]
    finals = [item for item in agent_messages if item.get("phase") == "final_answer"]
    result_items = finals or agent_messages[-1:]
    results = [str(item.get("text") or "") for item in result_items]
    changed_paths = list(ref.changed_files)
    for item in items:
        if item.get("type") != "fileChange":
            continue
        for change in item.get("changes") or []:
            if isinstance(change, dict) and change.get("path"):
                changed_paths.append(str(change["path"]))
    changed_paths = list(dict.fromkeys(changed_paths))[:MAX_PATHS]
    turn_status = str(turn.get("status") or ref.bridge_status or "unknown")
    return {
        "thread_id": ref.thread_id,
        "turn_id": ref.turn_id,
        "bridge_status": ref.bridge_status,
        "turn_status": turn_status,
        "started_at": ref.started_at,
        "updated_at": ref.updated_at,
        "ended_at": ref.ended_at,
        "cwd": ref.cwd,
        "goal_excerpt": _bounded("\n".join(goals), MAX_GOAL_CHARS),
        "result_excerpt": _bounded("\n".join(results), MAX_RESULT_CHARS),
        "changed_files": changed_paths,
        "evidence_boundary": "Bounded user-goal and final-result projection from the exact target turn; commentary, reasoning, commands, tool responses, approval payloads, and full transcript were omitted.",
    }


def _thread_project_context(thread: dict[str, Any], refs: tuple[TaskRef, ...]) -> dict[str, str]:
    """Retain the task's parent-project identity from the authoritative thread.

    ``thread/read`` owns ``projectId`` and ``cwd`` for the loaded thread.  The
    progress bridge is only a selector, so its cwd is a bounded fallback when an
    older app-server response does not expose the thread cwd.  A non-empty,
    unknown project ID is deliberately retained as-is; downstream routing must
    not replace it with a title or keyword guess.
    """
    project_id = str(thread.get("projectId") or thread.get("project_id") or "").strip()
    thread_cwd = str(thread.get("cwd") or "").strip()
    bridge_cwds = {ref.cwd.strip() for ref in refs if ref.cwd.strip()}
    fallback_cwd = next(iter(bridge_cwds)) if len(bridge_cwds) == 1 else ""
    cwd = thread_cwd or fallback_cwd
    if project_id:
        identity_source = "thread_project_id"
    elif thread_cwd:
        identity_source = "thread_cwd"
    elif fallback_cwd:
        identity_source = "bridge_cwd"
    else:
        identity_source = "projectless"
    return {
        "project_id": project_id,
        "cwd": cwd,
        "identity_source": identity_source,
    }


def _project_thread(result: dict[str, Any], refs: tuple[TaskRef, ...], meta: dict[str, Any]) -> dict[str, Any]:
    thread = result.get("thread") or {}
    project_context = _thread_project_context(thread, refs)
    turns = {str(turn.get("id")): turn for turn in (thread.get("turns") or []) if isinstance(turn, dict)}
    tasks = []
    missing = []
    for ref in refs:
        turn = turns.get(ref.turn_id)
        if turn is None:
            missing.append(ref.turn_id)
        else:
            tasks.append(_project_turn(turn, ref))
    status = "ok" if not missing else "partial"
    return {
        "thread_id": refs[0].thread_id,
        "status": status,
        "attempts": meta["attempts"],
        "target_turns": len(refs),
        "found_turns": len(tasks),
        "missing_turn_ids": missing,
        "project_context": project_context,
        "tasks": tasks,
        "error_class": None,
        "error_code": None,
    }


def collect_evidence(
    manifest: Manifest,
    *,
    codex_binary: Path,
    codex_home: Path,
    max_workers: int,
    max_attempts: int,
    request_timeout: float,
) -> dict[str, Any]:
    selection = {
        "references_seen": manifest.references_seen,
        "usable_references": len(manifest.refs),
        "unique_threads": len(manifest.thread_ids),
        "null_turn_references": manifest.null_turn_references,
        "missing_thread_references": manifest.missing_thread_references,
        "duplicate_references": manifest.duplicate_references,
        "bridge_generated_at": manifest.generated_at,
        "bridge_retention_days": manifest.retention_days,
    }
    base = {
        "controller_version": CONTROLLER_VERSION,
        "target_date": manifest.target_date,
        "timezone": DEFAULT_TIMEZONE,
        "selection": selection,
    }
    if not manifest.refs:
        if manifest.references_seen == 0:
            return {
                **base,
                "canary": {"status": "not_required", "reason": "empty_manifest"},
                "threads": [],
                "summary": {"readable_threads": 0, "unreadable_threads": 0, "found_turns": 0, "missing_turns": 0},
                "coverage": "complete",
                "status": "no_tasks",
            }
        return {
            **base,
            "canary": {"status": "not_run", "reason": "no_usable_turn_ids"},
            "threads": [],
            "summary": {"readable_threads": 0, "unreadable_threads": 0, "found_turns": 0, "missing_turns": manifest.references_seen},
            "coverage": "unknown",
            "status": "access_incomplete",
        }

    refs_by_thread: dict[str, tuple[TaskRef, ...]] = {}
    for thread_id in manifest.thread_ids:
        refs_by_thread[thread_id] = tuple(ref for ref in manifest.refs if ref.thread_id == thread_id)

    client = AppServerClient(codex_binary, codex_home, request_timeout)
    try:
        initialize_result = client.initialize()
        canary = None
        canary_data_failures = 0
        for canary_id in manifest.thread_ids:
            canary_result, canary_meta = _read_with_retry(
                client, canary_id, include_turns=False, max_attempts=max_attempts
            )
            candidate = {"thread_id": canary_id, **canary_meta}
            if canary_result is not None:
                canary = {
                    **candidate,
                    "attempted_threads": canary_data_failures + 1,
                    "data_failures": canary_data_failures,
                }
                break
            error_class = str(canary_meta.get("error_class") or "transient")
            if error_class == "data":
                canary_data_failures += 1
                continue
            raise ControllerError(
                str(canary_meta.get("error_code") or "canary_failure"),
                str(canary_meta.get("error") or "thread/read canary failed"),
                classification=error_class,
            )
        if canary is None:
            canary = {
                "status": "failed",
                "attempts": 1,
                "error_class": "data",
                "error_code": "thread_unavailable",
                "attempted_threads": canary_data_failures,
                "data_failures": canary_data_failures,
            }

        workers = max(1, min(max_workers, MAX_WORKERS, len(refs_by_thread)))

        def read_one(thread_id: str) -> dict[str, Any]:
            result, meta = _read_with_retry(
                client, thread_id, include_turns=True, max_attempts=max_attempts
            )
            if result is None:
                return {
                    "thread_id": thread_id,
                    "status": "failed",
                    "attempts": meta["attempts"],
                    "target_turns": len(refs_by_thread[thread_id]),
                    "found_turns": 0,
                    "missing_turn_ids": [ref.turn_id for ref in refs_by_thread[thread_id]],
                    "tasks": [],
                    "error_class": meta.get("error_class"),
                    "error_code": meta.get("error_code"),
                    "error": meta.get("error"),
                }
            return _project_thread(result, refs_by_thread[thread_id], meta)

        outcomes = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(read_one, thread_id): thread_id for thread_id in manifest.thread_ids}
            for future in concurrent.futures.as_completed(futures):
                outcomes.append(future.result())
        outcomes.sort(key=lambda item: item["thread_id"])
    finally:
        client.close()

    readable = sum(1 for item in outcomes if item["found_turns"] > 0)
    unreadable = sum(1 for item in outcomes if item["found_turns"] == 0)
    found_turns = sum(int(item["found_turns"]) for item in outcomes)
    missing_turns = sum(len(item["missing_turn_ids"]) for item in outcomes)
    structural_failures = [item for item in outcomes if item.get("error_class") == "structural"]
    if structural_failures and found_turns == 0:
        status = "access_incomplete"
        coverage = "unknown"
    elif unreadable or missing_turns or manifest.null_turn_references or manifest.missing_thread_references:
        status = "access_incomplete"
        coverage = "partial"
    else:
        status = "ready"
        coverage = "complete"
    return {
        **base,
        "app_server": {
            "user_agent": _bounded(str(initialize_result.get("userAgent") or ""), 200),
            "platform": str(initialize_result.get("platformOs") or ""),
            "rpc_method": "thread/read",
            "include_turns": True,
            "max_workers": workers,
            "max_attempts": max_attempts,
        },
        "canary": canary,
        "threads": outcomes,
        "summary": {
            "readable_threads": readable,
            "unreadable_threads": unreadable,
            "found_turns": found_turns,
            "missing_turns": missing_turns,
        },
        "coverage": coverage,
        "status": status,
    }


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target-date", required=True)
    parser.add_argument("--bridge", type=Path, default=Path.home() / ".codex/progress-bridge/live-state.json")
    parser.add_argument("--codex-home", type=Path, default=Path.home() / ".codex")
    parser.add_argument("--codex-bin", type=Path)
    parser.add_argument("--timezone", default=DEFAULT_TIMEZONE)
    parser.add_argument("--max-workers", type=int, default=3)
    parser.add_argument("--max-attempts", type=int, default=MAX_ATTEMPTS)
    parser.add_argument("--request-timeout", type=float, default=30.0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        if not 1 <= args.max_workers <= MAX_WORKERS:
            raise ControllerError("invalid_max_workers", f"must be 1..{MAX_WORKERS}", classification="structural")
        if not 1 <= args.max_attempts <= MAX_ATTEMPTS:
            raise ControllerError("invalid_max_attempts", f"must be 1..{MAX_ATTEMPTS}", classification="structural")
        bridge = json.loads(args.bridge.read_text(encoding="utf-8"))
        manifest = build_manifest(bridge, args.target_date, args.timezone)
        binary = args.codex_bin or _default_codex_binary()
        payload = collect_evidence(
            manifest,
            codex_binary=binary,
            codex_home=args.codex_home,
            max_workers=args.max_workers,
            max_attempts=args.max_attempts,
            request_timeout=args.request_timeout,
        )
        if args.output:
            _atomic_write_json(args.output, payload)
            print(json.dumps({
                "ok": True,
                "output": str(args.output),
                "target_date": args.target_date,
                "coverage": payload["coverage"],
                "status": payload["status"],
                "selection": payload["selection"],
                "summary": payload["summary"],
                "canary": payload["canary"],
            }, ensure_ascii=False))
        else:
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except ControllerError as exc:
        print(json.dumps({
            "ok": False,
            "error_class": exc.classification,
            "error_code": exc.code,
            "error": _bounded(str(exc), MAX_ERROR_CHARS),
        }, ensure_ascii=False))
        return 3 if exc.classification == "structural" else 4
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({
            "ok": False,
            "error_class": "structural",
            "error_code": "controller_input_failed",
            "error": _bounded(str(exc), MAX_ERROR_CHARS),
        }, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
