#!/usr/bin/env python3
"""Private, read-only M2 web projection for registered PROJECT_MAP.md files.

The process deliberately owns no task executor, model client, credential reader,
or business-state writer.  It binds loopback only and turns malformed maps,
missing maps, and unavailable quota/activity inputs into explicit UI errors.
"""
from __future__ import annotations

import argparse
import json
import re
import secrets
import subprocess
import threading
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from workbench import ID_RE, SCHEMA_VERSION, Store, WorkbenchError

MAX_INPUT = 4096
MAX_CACHE_BYTES = 512 * 1024
MAX_ACTIVITY_BYTES = 8 * 1024 * 1024
MAX_MANAGEMENT_BODY = 8 * 1024
QUOTA_FRESH_SECONDS = 5 * 60
ACCOUNT_SLOTS = ("account-1", "account-2")


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def public_error(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def _safe_text(value: Any, limit: int = 500) -> str | None:
    return value if isinstance(value, str) and 0 < len(value) <= limit else None


def _safe_code(value: Any, limit: int = 80) -> str | None:
    return value if isinstance(value, str) and re.fullmatch(r"[a-z0-9][a-z0-9_.:-]{0,%d}" % (limit - 1), value) else None


def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str): return None
    try: parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError: return None
    return parsed if parsed.tzinfo is not None and parsed.utcoffset() is not None else None


class QuotaAdapter:
    """Narrow reader for the router's already-sanitized quotaDisplay cache."""

    def __init__(self, cache_path: Path | None, refresh: Callable[[], None] | None = None,
                 clock: Callable[[], datetime] | None = None):
        self.cache_path, self.refresh = cache_path, refresh
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._refresh_lock = threading.Lock()

    def _sanitize(self, display: Any) -> dict[str, Any]:
        if not isinstance(display, dict):
            raise ValueError("missing quotaDisplay")
        now = self.clock()
        if now.tzinfo is None or now.utcoffset() is None: now = now.replace(tzinfo=timezone.utc)
        by_slot: dict[str, dict[str, Any]] = {}
        source_accounts = display.get("accounts", [])
        if not isinstance(source_accounts, list): source_accounts = []
        for entry in source_accounts:
            if not isinstance(entry, dict):
                continue
            slot = entry.get("accountSlot")
            if slot not in ACCOUNT_SLOTS or slot in by_slot:
                continue
            source_status = entry.get("status") if entry.get("status") in {"fresh", "error"} else "unavailable"
            fetched = _timestamp(entry.get("fetchedAt"))
            age = max(0, int((now - fetched).total_seconds())) if fetched and fetched <= now else None
            windows: list[dict[str, Any]] = []
            source_windows = entry.get("windows", [])
            if not isinstance(source_windows, list): source_windows = []
            for window in source_windows:
                if not isinstance(window, dict): continue
                remaining = window.get("remainingPercent")
                if not isinstance(remaining, (int, float)) or isinstance(remaining, bool) or not 0 <= remaining <= 100: continue
                windows.append({
                    "limit_id": _safe_code(window.get("limitId"), 64),
                    "kind": _safe_code(window.get("kind"), 64) or "quota",
                    "remaining_percent": remaining,
                    "resets_at": window.get("resetsAt") if isinstance(window.get("resetsAt"), (int, float)) and not isinstance(window.get("resetsAt"), bool) else None,
                    "window_duration_mins": window.get("windowDurationMins") if isinstance(window.get("windowDurationMins"), int) and not isinstance(window.get("windowDurationMins"), bool) and window.get("windowDurationMins") > 0 else None,
                })
            if source_status == "fresh" and age is not None and age <= QUOTA_FRESH_SECONDS:
                status = "fresh"
            elif fetched and windows:
                status = "cached" if age is not None and age <= QUOTA_FRESH_SECONDS else "stale"
            else:
                status = "unavailable"
            account: dict[str, Any] = {
                "account_slot": slot,
                "status": status,
                "source_status": source_status,
                "error_code": _safe_code(entry.get("errorCode")),
                "fetched_at": entry.get("fetchedAt") if fetched else None,
                "age_seconds": age,
                "windows": windows if status != "unavailable" else [],
            }
            by_slot[slot] = account
        accounts = [by_slot.get(slot, {"account_slot": slot, "status": "unavailable", "source_status": "unavailable",
                                              "error_code": "quota_account_missing", "fetched_at": None,
                                              "age_seconds": None, "windows": []}) for slot in ACCOUNT_SLOTS]
        fresh_count = sum(account["status"] == "fresh" for account in accounts)
        if fresh_count == len(ACCOUNT_SLOTS): status = "fresh"
        elif fresh_count: status = "partial"
        elif any(account["status"] in {"cached", "stale"} for account in accounts): status = "cached"
        else: status = "unavailable"
        current_status = display.get("currentAccountStatus") if display.get("currentAccountStatus") in {"confirmed", "unknown", "ambiguous", "mapped"} else "unknown"
        current_slot = display.get("currentAccountSlot") if current_status == "confirmed" and display.get("currentAccountSlot") in ACCOUNT_SLOTS else None
        return {
            "status": status,
            "source_status": display.get("status") if display.get("status") in {"fresh", "partial", "error", "unavailable"} else "unavailable",
            "attempted_at": display.get("attemptedAt") if _timestamp(display.get("attemptedAt")) else None,
            "current_account_status": current_status,
            "current_account_slot": current_slot,
            "accounts": accounts,
            "source": "router quotaDisplay cache",
        }

    def read(self) -> dict[str, Any]:
        if self.cache_path is None:
            return {"available": False, "error": public_error("quota_unconfigured", "未配置脱敏额度缓存。")}
        try:
            raw = self.cache_path.read_bytes()
            if len(raw) > MAX_CACHE_BYTES:
                raise ValueError("cache too large")
            state = json.loads(raw)
            return {"available": True, "quota": self._sanitize(state.get("quotaDisplay"))}
        except (OSError, ValueError, json.JSONDecodeError, AttributeError):
            return {"available": False, "error": public_error("quota_unavailable", "额度缓存当前不可用；未显示推测余额。")}

    def refresh_explicitly(self) -> dict[str, Any]:
        if self.refresh is None:
            return {"available": False, "error": public_error("quota_refresh_unavailable", "未配置显式额度刷新；可继续查看现有缓存。")}
        if not self._refresh_lock.acquire(blocking=False):
            return {"available": False, "error": public_error("quota_refresh_in_progress", "额度刷新正在进行。")}
        try:
            self.refresh()  # The configured router owns all credential handling.
        except (OSError, subprocess.SubprocessError):
            return {"available": False, "error": public_error("quota_refresh_failed", "额度刷新失败；保留原有缓存状态。")}
        finally:
            self._refresh_lock.release()
        return self.read()


class ActivityAdapter:
    """Optional TTL observations; never changes a map's business status."""

    _BRIDGE_STATES = {
        "running": ("running", "Codex 任务正在运行"),
        "waiting_approval": ("waiting_approval", "Codex 任务等待批准"),
        "completed": ("stopped", "Codex Stop 已完成"),
        "failed": ("failed", "Codex Stop 报告失败（不等于进程 crash）"),
    }

    def __init__(self, path: Path | None, clock: Callable[[], datetime] | None = None):
        self.path = path
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def _direct(self, value: dict[str, Any], now: datetime) -> dict[tuple[str, str], dict[str, Any]]:
        rows = value.get("observations", [])
        if not isinstance(rows, list): return {}
        result: dict[tuple[str, str], dict[str, Any]] = {}
        for row in rows:
            if not isinstance(row, dict): continue
            project_id, outcome_id = row.get("project_id"), row.get("outcome_id")
            expires = row.get("expires_at")
            if not isinstance(project_id, str) or not isinstance(outcome_id, str) or not isinstance(expires, str): continue
            expiry = _timestamp(expires)
            if expiry is None or expiry <= now: continue
            state = row.get("state")
            if state not in {"running", "stopped", "unknown"}: continue
            result[(project_id, outcome_id)] = {
                "state": state,
                "observed_at": _safe_text(row.get("observed_at"), 64),
                "expires_at": expires,
                "label": _safe_text(row.get("label"), 240),
                "source": "explicit_ttl_observation",
            }
        return result

    def _progress_bridge(self, value: dict[str, Any], now: datetime) -> dict[tuple[str, str], dict[str, Any]]:
        """Project the Progress Bridge's documented, already-redacted live-state schema.

        Rows are keyed by the stable Codex session/thread ID.  The project map
        must explicitly associate that ID with an outcome; cwd is deliberately
        ignored because a Codex task can operate across several worktrees.
        """
        if value.get("schema_version") != 1 or not isinstance(value.get("tasks"), list): return {}
        stale_seconds = value.get("stale_after_seconds")
        if not isinstance(stale_seconds, int) or isinstance(stale_seconds, bool) or not 1 <= stale_seconds <= 86400:
            return {}
        selected: dict[tuple[str, str], tuple[datetime, dict[str, Any]]] = {}
        for row in value["tasks"]:
            if not isinstance(row, dict): continue
            thread_id = _safe_text(row.get("session_id"), 160)
            observed = _timestamp(row.get("updated_at"))
            mapped = self._BRIDGE_STATES.get(row.get("status"))
            if thread_id is None or observed is None or mapped is None or observed > now: continue
            expiry = observed + timedelta(seconds=stale_seconds)
            if expiry <= now: continue
            state, label = mapped
            observation = {
                "state": state,
                "observed_at": row["updated_at"],
                "expires_at": expiry.isoformat().replace("+00:00", "Z"),
                "label": label,
                "source": "codex_progress_bridge",
            }
            key = ("@thread", thread_id)
            previous = selected.get(key)
            if previous is None or observed > previous[0]: selected[key] = (observed, observation)
        return {key: item[1] for key, item in selected.items()}

    def read(self) -> dict[tuple[str, str], dict[str, Any]]:
        if self.path is None: return {}
        try:
            raw = self.path.read_bytes()
            if len(raw) > MAX_ACTIVITY_BYTES: return {}
            value = json.loads(raw)
        except (OSError, json.JSONDecodeError): return {}
        if not isinstance(value, dict): return {}
        now = self.clock()
        if now.tzinfo is None or now.utcoffset() is None: now = now.replace(tzinfo=timezone.utc)
        result = self._direct(value, now)
        result.update(self._progress_bridge(value, now))
        return result


class ProjectProjection:
    def __init__(self, store: Store, activity: ActivityAdapter): self.store, self.activity = store, activity

    def _rows(self, include_archived: bool = False) -> list[dict[str, Any]]:
        registry = self.store._registry()  # Registry contains paths only, never projected business state.
        observations = self.activity.read()
        rows: list[dict[str, Any]] = []
        for binding in registry.get("projects", []):
            project_id = binding.get("project_id") if isinstance(binding, dict) else None
            if not isinstance(project_id, str): continue
            if binding.get("visibility", "active") == "archived" and not include_archived: continue
            if binding.get("map_status") == "missing":
                rows.append({"project_id": project_id, "title": project_id, "outcomes": [], "error": public_error("map_missing", "尚未找到 PROJECT_MAP.md；未初始化、未推断任何业务状态。")})
                continue
            try:
                mapped = self.store.read(project_id)["project_map"]
                outcomes = []
                for outcome in mapped["outcomes"]:
                    view = {key: outcome.get(key) for key in ("id", "title", "status", "priority", "deadline", "pause_reason", "checkpoint", "evidence", "acceptance", "conversations", "depends_on")}
                    view["activity"] = observations.get((project_id, outcome["id"]))
                    if view["activity"] is None:
                        linked = [observations.get(("@thread", ref.get("thread_id"))) for ref in outcome.get("conversations", []) if isinstance(ref, dict)]
                        linked = [item for item in linked if item is not None]
                        if linked:
                            view["activity"] = max(linked, key=lambda item: _timestamp(item.get("observed_at")) or datetime.min.replace(tzinfo=timezone.utc))
                    outcomes.append(view)
                status_order = {"in_progress": 0, "pending_review": 1, "paused": 2, "not_started": 3, "done": 4, "cancelled": 5}
                priority_order = {"high": 0, "normal": 1, "low": 2}
                outcomes.sort(key=lambda item: (status_order.get(item.get("status"), 9), priority_order.get(item.get("priority"), 9), item["title"].casefold(), item["id"]))
                rows.append({"project_id": project_id, "title": mapped["title"], "goal": mapped["goal"], "architecture": mapped.get("architecture"), "lifecycle": mapped["lifecycle"], "focus_outcome_id": mapped.get("focus_outcome_id"), "revision": mapped["revision"], "updated_at": mapped["updated_at"], "visibility": binding.get("visibility", "active"), "outcomes": outcomes})
            except WorkbenchError as exc:
                rows.append({"project_id": project_id, "title": project_id, "outcomes": [], "error": public_error(exc.code, "项目地图不可读；未显示推测状态。")})
        return sorted(rows, key=lambda row: (bool(row.get("error")), row["title"].casefold(), row["project_id"]))

    def list(self, query: str = "", status: str = "", sort: str = "recommended", include_archived: bool = False) -> list[dict[str, Any]]:
        needle = query.casefold().strip()
        rows = self._rows(include_archived)
        if needle:
            rows = [row for row in rows if needle in row["project_id"].casefold() or needle in row["title"].casefold() or any(needle in o["id"].casefold() or needle in o["title"].casefold() or needle in ((o.get("checkpoint") or {}).get("last_result") or "").casefold() or needle in ((o.get("checkpoint") or {}).get("next_action") or "").casefold() for o in row["outcomes"])]
        if status:
            filtered: list[dict[str, Any]] = []
            for row in rows:
                if row.get("error"): filtered.append(row); continue
                copy_row = dict(row); copy_row["outcomes"] = [outcome for outcome in row["outcomes"] if outcome["status"] == status]
                if copy_row["outcomes"]: filtered.append(copy_row)
            rows = filtered
        if sort == "title":
            for row in rows: row["outcomes"].sort(key=lambda item: (item["title"].casefold(), item["id"]))
            rows.sort(key=lambda row: (bool(row.get("error")), row["title"].casefold(), row["project_id"]))
        return rows

    def revisions(self) -> list[dict[str, Any]]:
        """Return the smallest safe signal needed by the visible-page refresh loop."""
        return [{key: row.get(key) for key in ("project_id", "revision", "updated_at", "error")}
                for row in self._rows()]

    def detail(self, project_id: str, outcome_id: str) -> dict[str, Any]:
        if not ID_RE.fullmatch(project_id) or not ID_RE.fullmatch(outcome_id): raise WorkbenchError("not_found", "unknown project or outcome")
        for row in self._rows():
            if row["project_id"] == project_id:
                for outcome in row["outcomes"]:
                    if outcome["id"] == outcome_id:
                        checkpoint = outcome.get("checkpoint") if isinstance(outcome.get("checkpoint"), dict) else {}
                        evidence = outcome.get("evidence")
                        if not isinstance(evidence, list): evidence = []
                        completed = [item.get("summary") for item in evidence
                                     if isinstance(item, dict) and _safe_text(item.get("summary"), 500)]
                        remaining = [item for item in checkpoint.get("remaining", []) if _safe_text(item, 500)]
                        resume_lines = [
                            f"继续项目 {project_id} 的成果 {outcome_id}（当前业务状态：{outcome.get('status', 'unknown')}）。",
                            f"已知检查点：{_safe_text(checkpoint.get('last_result'), 1000) or '无；先读取 PROJECT_MAP.md。'}",
                        ]
                        if completed:
                            resume_lines.append("已核对证据（不要在无新变化时重复执行）：" + "；".join(completed[-3:]))
                        if remaining:
                            resume_lines.append("仍需完成：" + "；".join(remaining[:5]))
                        if _safe_text(checkpoint.get("next_action"), 500):
                            resume_lines.append("本次唯一下一步：" + checkpoint["next_action"])
                        resume_lines.extend([
                            "先核对 PROJECT_MAP.md 检查点之后的文件变化；无新增变化时，不重跑已记录为通过的完整核验。",
                            "不要假定旧会话或另一账号可读。",
                        ])
                        return {"project_id": project_id, "project_title": row["title"], "outcome": outcome,
                                "resume_note": "\n".join(resume_lines)}
        raise WorkbenchError("not_found", "unknown project or outcome")


class WorkbenchWeb:
    def __init__(self, projection: ProjectProjection, quota: QuotaAdapter, recommended_model: str, actual_model: str):
        self.projection, self.quota = projection, quota
        self.models = {"recommended_planning": recommended_model, "actual_this_run": actual_model,
                       "note": "推荐规划不会自动生效；阶段切换需由 create_thread/send_message 显式传入 model 与 thinking。"}
        # A process-lifetime capability token supplements loopback Host/Origin
        # checks. It is delivered only by the same-origin management GET and is
        # never persisted in the registry or logs.
        self.management_token = secrets.token_urlsafe(32)

    def handler(self) -> type[BaseHTTPRequestHandler]:
        app = self
        class Handler(BaseHTTPRequestHandler):
            server_version = "CodexWorkbench/0.2"
            def log_message(self, _format: str, *_args: Any) -> None: pass
            def _local(self) -> bool: return self.client_address[0] in {"127.0.0.1", "::1"}
            def _host_ok(self) -> bool:
                values = self.headers.get_all("Host", [])
                if len(values) != 1: return False
                return re.fullmatch(r"(?:127\.0\.0\.1|localhost)(?::\d{1,5})?|\[::1\](?::\d{1,5})?", values[0].strip().lower()) is not None
            def _headers(self, content_type: str, length: int) -> None:
                self.send_header("Content-Type", content_type); self.send_header("Content-Length", str(length)); self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff"); self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("Content-Security-Policy", "default-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
            def _json(self, status: HTTPStatus, value: Any, body: bool = True) -> None:
                data = json.dumps(value, ensure_ascii=False).encode("utf-8")
                self.send_response(status); self._headers("application/json; charset=utf-8", len(data)); self.end_headers()
                if body: self.wfile.write(data)
            def _text(self, status: HTTPStatus, body: str, content_type: str) -> None:
                data = body.encode("utf-8"); self.send_response(status); self._headers(content_type, len(data)); self.end_headers(); self.wfile.write(data)
            def _guard(self, post: bool = False) -> bool:
                if not self._local() or not self._host_ok(): self._json(HTTPStatus.FORBIDDEN, {"error": public_error("origin_rejected", "仅允许本机私有入口。")} ); return False
                if post:
                    origins = self.headers.get_all("Origin", [])
                    if len(origins) != 1: self._json(HTTPStatus.FORBIDDEN, {"error": public_error("origin_rejected", "刷新仅接受同源页面操作。")} ); return False
                    origin = origins[0]
                    host = self.headers.get("Host", "")
                    if origin != f"http://{host}": self._json(HTTPStatus.FORBIDDEN, {"error": public_error("origin_rejected", "刷新仅接受同源页面操作。")} ); return False
                return True
            def _management_guard(self) -> bool:
                if not self._guard(post=True): return False
                if not secrets.compare_digest(self.headers.get("X-Workbench-Token", ""), app.management_token):
                    self._json(HTTPStatus.FORBIDDEN, {"error": public_error("management_token_rejected", "项目管理确认已失效；请刷新页面。")}); return False
                return True
            def _management_json(self) -> dict[str, Any] | None:
                if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
                    self._json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": public_error("invalid_request", "项目管理请求必须是 JSON。")}); return None
                length = self.headers.get("Content-Length")
                if self.headers.get("Transfer-Encoding") or not length or not length.isdigit() or not 2 <= int(length) <= MAX_MANAGEMENT_BODY:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": public_error("invalid_request", "项目管理请求大小无效。")}); return None
                try: value = json.loads(self.rfile.read(int(length)))
                except json.JSONDecodeError:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": public_error("invalid_request", "项目管理请求不是有效 JSON。")}); return None
                if not isinstance(value, dict):
                    self._json(HTTPStatus.BAD_REQUEST, {"error": public_error("invalid_request", "项目管理请求格式无效。")}); return None
                return value
            def _membership_error(self, exc: WorkbenchError) -> None:
                status = HTTPStatus.CONFLICT if exc.code == "revision_conflict" else HTTPStatus.BAD_REQUEST
                self._json(status, {"error": public_error(exc.code, "项目登记请求未被接受；请刷新后重新核对。"), "current_revision": exc.current_revision})
            def do_GET(self) -> None:
                if not self._guard(): return
                parsed = urlparse(self.path)
                if parsed.path == "/": return self._text(HTTPStatus.OK, INDEX_HTML, "text/html; charset=utf-8")
                if parsed.path == "/app.js": return self._text(HTTPStatus.OK, APP_JS, "application/javascript; charset=utf-8")
                if parsed.path == "/app.css": return self._text(HTTPStatus.OK, APP_CSS, "text/css; charset=utf-8")
                if parsed.path == "/api/projects/revisions" and not parsed.query:
                    try: revisions = app.projection.revisions()
                    except WorkbenchError: return self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": public_error("registry_unavailable", "项目登记当前不可读；未显示推测状态。")} )
                    return self._json(HTTPStatus.OK, {"revisions": revisions, "generated_at": iso_now(), "model_calls": 0})
                if parsed.path == "/api/projects":
                    params = parse_qs(parsed.query, keep_blank_values=True)
                    if set(params) - {"q", "status", "sort", "archived"} or any(len(params.get(key, [""])) != 1 for key in params): return self._json(HTTPStatus.BAD_REQUEST, {"error": public_error("invalid_query", "搜索参数无效。")})
                    query = params.get("q", [""])[0]
                    status_filter = params.get("status", [""])[0]
                    sort = params.get("sort", ["recommended"])[0]
                    archived = params.get("archived", ["0"])[0]
                    if len(query) > MAX_INPUT: return self._json(HTTPStatus.BAD_REQUEST, {"error": public_error("invalid_query", "搜索条件过长。")})
                    if status_filter not in {"", "not_started", "in_progress", "paused", "pending_review", "done", "cancelled"} or sort not in {"recommended", "title"} or archived not in {"0", "1"}:
                        return self._json(HTTPStatus.BAD_REQUEST, {"error": public_error("invalid_query", "筛选或排序参数无效。")})
                    try: projects = app.projection.list(query, status_filter, sort, archived == "1")
                    except WorkbenchError: return self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": public_error("registry_unavailable", "项目登记当前不可读；未显示推测状态。")})
                    return self._json(HTTPStatus.OK, {"projects": projects, "generated_at": iso_now(), "model_calls": 0})
                if parsed.path.startswith("/api/projects/"):
                    bits = parsed.path.split("/")
                    if len(bits) == 6 and bits[4] == "outcomes":
                        try: return self._json(HTTPStatus.OK, app.projection.detail(bits[3], bits[5]))
                        except WorkbenchError as exc: return self._json(HTTPStatus.NOT_FOUND, {"error": public_error(exc.code, "找不到该成果。")})
                if parsed.path == "/api/quota": return self._json(HTTPStatus.OK, app.quota.read())
                if parsed.path == "/api/runtime": return self._json(HTTPStatus.OK, {"models": app.models, "model_calls": 0})
                if parsed.path == "/api/management" and not parsed.query:
                    try:
                        view = app.projection.store.membership_view()
                        view["token"] = app.management_token
                        return self._json(HTTPStatus.OK, view)
                    except WorkbenchError:
                        return self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": public_error("registry_unavailable", "项目登记当前不可读；未显示推测状态。")})
                self._json(HTTPStatus.NOT_FOUND, {"error": public_error("not_found", "不存在的私有工作台路径。")})
            def do_POST(self) -> None:
                if not self._guard(post=True): return
                parsed = urlparse(self.path)
                if parsed.path.startswith("/api/management/"):
                    if not self._management_guard(): return
                    body = self._management_json()
                    if body is None: return
                    try:
                        if parsed.path == "/api/management/preview" and set(body) == {"project_id", "root_path", "map_path"}:
                            if not isinstance(body["project_id"], str) or not isinstance(body["root_path"], str) or not isinstance(body["map_path"], str): raise WorkbenchError("invalid_map", "invalid preview")
                            return self._json(HTTPStatus.OK, {"preview": app.projection.store.preview_binding(body["project_id"], Path(body["root_path"]), body["map_path"])})
                        if parsed.path == "/api/management/apply" and set(body) == {"project_id", "root_path", "map_path", "expected_registry_revision", "confirm_project_id"}:
                            if body.get("confirm_project_id") != body.get("project_id") or not isinstance(body["root_path"], str) or not isinstance(body["map_path"], str): raise WorkbenchError("invalid_map", "invalid apply")
                            return self._json(HTTPStatus.OK, {"result": app.projection.store.apply_binding(body["project_id"], Path(body["root_path"]), body["map_path"], body["expected_registry_revision"])})
                        if parsed.path in {"/api/management/archive", "/api/management/restore"} and set(body) == {"project_id", "expected_registry_revision"}:
                            visibility = "archived" if parsed.path.endswith("archive") else "active"
                            return self._json(HTTPStatus.OK, {"result": app.projection.store.change_visibility(body["project_id"], visibility, body["expected_registry_revision"])})
                        if parsed.path == "/api/management/remove" and set(body) == {"project_id", "expected_registry_revision", "confirm_project_id", "confirm_remove"}:
                            if body.get("confirm_project_id") != body.get("project_id") or body.get("confirm_remove") is not True: raise WorkbenchError("invalid_map", "invalid remove confirmation")
                            return self._json(HTTPStatus.OK, {"result": app.projection.store.remove_binding(body["project_id"], body["expected_registry_revision"])})
                        raise WorkbenchError("invalid_map", "unsupported management request")
                    except WorkbenchError as exc:
                        return self._membership_error(exc)
                length = self.headers.get("Content-Length", "0")
                if self.headers.get("Transfer-Encoding") or not length.isdigit() or int(length) != 0:
                    return self._json(HTTPStatus.BAD_REQUEST, {"error": public_error("invalid_request", "刷新请求不得包含正文。")})
                if parsed.path == "/api/quota/refresh" and not parsed.query: return self._json(HTTPStatus.OK, app.quota.refresh_explicitly())
                self._json(HTTPStatus.NOT_FOUND, {"error": public_error("not_found", "不存在的私有工作台路径。")})
            def _unsupported(self, body: bool = True) -> None:
                if not self._guard(): return
                self._json(HTTPStatus.METHOD_NOT_ALLOWED, {"error": public_error("method_not_allowed", "该私有工作台不允许此方法。")}, body=body)
            def do_HEAD(self) -> None:
                self._unsupported(body=False)
            do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _unsupported
        return Handler


INDEX_HTML = """<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'><title>Codex Workbench</title><link rel='stylesheet' href='/app.css'></head><body><header><h1>Codex Workbench</h1><p>私有本机投影 · 浏览与搜索不调用模型</p><div class='controls'><input id='search' type='search' placeholder='搜索项目、成果或检查点' autocomplete='off'><label>状态<select id='status-filter'><option value=''>全部</option><option value='in_progress'>进行中</option><option value='pending_review'>待验收</option><option value='paused'>暂停</option><option value='not_started'>待开始</option><option value='done'>已完成</option><option value='cancelled'>已取消</option></select></label><label>排序<select id='sort'><option value='recommended'>推荐顺序</option><option value='title'>标题</option></select></label><button id='manage' type='button'>管理项目</button></div></header><main><aside><h2>项目</h2><div id='projects'></div></aside><section id='overview'><h2>成果</h2><div id='outcomes'></div></section><section id='detail'><h2>详情</h2><p>选择一个成果查看检查点、证据和安全接续说明。</p></section></main><footer><div id='quota'></div><button id='refresh' type='button'>显式刷新额度</button><div id='runtime'></div></footer><dialog id='manager'><form method='dialog'><button aria-label='关闭项目管理' class='close'>×</button><h2>管理项目</h2><p class='muted'>加入必须先预览再确认。归档只隐藏成员；移出只删除工作台登记，不删除项目、地图、业务数据或私有备份。</p><section class='manage-section'><h3>加入项目</h3><label>项目 ID<input id='join-id' pattern='[a-z][a-z0-9_-]{2,79}' required></label><label>绝对根目录<input id='join-root' placeholder='/absolute/project' required></label><label>地图路径<input id='join-map' value='PROJECT_MAP.md' required></label><button id='join-preview' type='button'>预览加入</button><div id='join-result' aria-live='polite'></div></section><section class='manage-section'><h3>活动项目</h3><div id='active-members'></div></section><section class='manage-section'><h3>暂时归档</h3><div id='archived-members'></div></section></form></dialog><script src='/app.js'></script></body></html>"""

APP_CSS = """*{box-sizing:border-box}body{margin:0;background:#101821;color:#e7eef7;font:14px system-ui,-apple-system,sans-serif;line-height:1.45}header,footer{padding:16px 22px;background:#172532}h1,h2,h3,p{margin:0 0 8px}h1{font-size:20px}header p{color:#adc0d4}input,button,select{font:inherit;border-radius:8px;padding:9px 10px}input,select{border:1px solid #426078;background:#0d151d;color:#fff}input{width:min(680px,100%)}.controls{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.controls label{display:flex;gap:5px;align-items:center;color:#adc0d4}button,.action{background:#5dd6b5;color:#082019;border:0;cursor:pointer}.action{display:inline-block;border-radius:8px;margin:6px 6px 0 0;padding:9px 10px}button{padding:9px 10px}.card[role=button]{cursor:pointer}.card:focus-visible,button:focus-visible,input:focus-visible{outline:2px solid #5dd6b5;outline-offset:2px}main{display:grid;grid-template-columns:minmax(210px,.7fr) minmax(300px,1.2fr) minmax(290px,1fr);gap:1px;background:#284154;min-height:calc(100vh - 187px)}main>*,footer{background:#101821;padding:18px}.card,.panel{background:#172532;border:1px solid #294158;border-radius:10px;padding:12px;margin:0 0 10px;overflow-wrap:anywhere}.project-card{border-left:4px solid #426078}.project-card.selected{border-left-color:#5dd6b5}.panel h4{margin:12px 0 5px;color:#9dd8ff}.status{color:#9dd8ff}.error{border-color:#d98b68;color:#ffd3c2}.muted{color:#a9b8c8}.evidence{font-size:12px}.quota-account{margin:2px 0}footer{display:flex;gap:12px;align-items:center;flex-wrap:wrap}#quota,#runtime{max-width:520px}.selected{outline:2px solid #5dd6b5}dialog{width:min(760px,calc(100vw - 28px));color:#e7eef7;background:#172532;border:1px solid #426078;border-radius:12px}.close{float:right}.manage-section{border-top:1px solid #426078;margin-top:14px;padding-top:14px}.manage-section label{display:block;margin:8px 0}.manage-section input{display:block;width:100%}.member{padding:10px 0;border-bottom:1px solid #294158;overflow-wrap:anywhere}.member button{margin:4px 6px 0 0}.danger{background:#f1a48b;color:#32120b}@media(max-width:899px){main{grid-template-columns:1fr;min-height:0}aside{order:0}#overview{order:1}#detail{order:2}}@media(max-width:430px){header,footer,main>*,dialog{padding:14px}.controls>*{width:100%}.controls label select{flex:1}body{font-size:14px}h1{font-size:18px}}"""

APP_JS = """const $=s=>document.querySelector(s), esc=s=>String(s??'').replace(/[&<>\"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}[c]));let projects=[];function checkpoint(o){return o.checkpoint?.last_result||'尚无检查点'}function quotaWindowLabel(w){const scope=w.limit_id==='codex'?'':w.limit_id?.startsWith('additional:')?'额外 '+w.limit_id.slice(11):'未标识额度桶';const mins=w.window_duration_mins;const label=mins===300?'5 小时额度':mins===10080?'周额度':Number.isInteger(mins)?`${mins} 分钟额度`:`${w.kind||'未知'}额度`;return scope?`${scope} · ${label}`:label}function showQuota(x){if(!x.available){$('#quota').textContent=x.error.message;return}const q=x.quota;$('#quota').innerHTML=`<p>额度缓存：${esc(q.status)}；读取：${esc(q.attempted_at||'未知')}；当前账号：${esc(q.current_account_slot||q.current_account_status)}</p>`+q.accounts.map(a=>`<p class='quota-account'>${esc(a.account_slot)}：${esc(a.status)}${a.windows.map(w=>` · ${esc(quotaWindowLabel(w))} ${esc(w.remaining_percent)}%`).join('')}</p>`).join('')}async function quota(){showQuota(await (await fetch('/api/quota')).json())}async function runtime(){const x=await (await fetch('/api/runtime')).json();$('#runtime').textContent=`推荐规划：${x.models.recommended_planning}；本次实际配置：${x.models.actual_this_run}`}"""
APP_JS += r"""
let membership=null;
const manager=$('#manager');
async function manageGet(){const r=await fetch('/api/management');const x=await r.json();if(!r.ok)throw new Error(x.error?.message||'管理信息不可用');membership=x;return x}
async function managePost(path,body){const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-Workbench-Token':membership.token},body:JSON.stringify(body)});const x=await r.json();if(!r.ok)throw new Error(x.error?.message||'请求未被接受');return x}
function memberCard(p){const archive=p.visibility==='archived';const primary=archive?`<button data-action='restore' data-id='${esc(p.project_id)}'>恢复</button>`:`<button data-action='archive' data-id='${esc(p.project_id)}'>暂时归档</button>`;return `<div class=member><strong>${esc(p.project_id)}</strong><br><span class=muted>${esc(p.root_path)} · ${esc(p.map_status)}</span><br>${primary}<button class=danger data-action='remove' data-id='${esc(p.project_id)}'>移出…</button></div>`}
function renderMembers(){const active=membership.projects.filter(p=>p.visibility!=='archived'),archived=membership.projects.filter(p=>p.visibility==='archived');$('#active-members').innerHTML=active.map(memberCard).join('')||'<p class=muted>没有活动项目。</p>';$('#archived-members').innerHTML=archived.map(memberCard).join('')||'<p class=muted>没有归档项目。</p>';manager.querySelectorAll('[data-action]').forEach(b=>b.onclick=()=>memberAction(b.dataset.action,b.dataset.id));}
async function refreshManager(){await manageGet();renderMembers();}
async function memberAction(action,id){try{if(action==='remove'){const p=membership.projects.find(x=>x.project_id===id);if(!p||prompt(`输入 ${id} 以确认移出。项目目录、PROJECT_MAP.md、业务数据和私有备份不会删除。`)!==id)return;await managePost('/api/management/remove',{project_id:id,expected_registry_revision:membership.registry_revision,confirm_project_id:id,confirm_remove:true});}else await managePost('/api/management/'+action,{project_id:id,expected_registry_revision:membership.registry_revision});await refreshManager();await load();}catch(e){alert(e.message)}}
$('#manage').onclick=async()=>{try{await refreshManager();manager.showModal();$('#join-id').focus()}catch(e){alert(e.message)}};
$('#join-preview').onclick=async()=>{const id=$('#join-id').value.trim(),root=$('#join-root').value.trim(),map=$('#join-map').value.trim(),out=$('#join-result');try{if(!membership)await manageGet();const x=await managePost('/api/management/preview',{project_id:id,root_path:root,map_path:map});const p=x.preview;out.innerHTML=`<p>预览：${esc(p.project_id)} · ${esc(p.map_status)}。确认后才登记；不会初始化地图或导入业务状态。</p><button id=join-apply type=button>确认加入</button>`;$('#join-apply').onclick=async()=>{try{await managePost('/api/management/apply',{project_id:id,root_path:root,map_path:map,expected_registry_revision:p.registry_revision,confirm_project_id:id});out.textContent='已加入。';await refreshManager();await load()}catch(e){out.textContent=e.message}}}catch(e){out.textContent=e.message}};
"""

APP_JS += r"""
// Project selection is a client-side view state only.  It never writes a map.
let selectedProjectId=null, selectedOutcomeId=null, detailRequest=0, loadRequest=0;
const statusName={not_started:'可开始',in_progress:'进行中',paused:'等待',pending_review:'待验收',done:'已完成',cancelled:'已取消'};
function selectedProject(){return projects.find(p=>p.project_id===selectedProjectId)}
function text(v,fallback='待核对'){return typeof v==='string'&&v? v:fallback}
function selectProject(id){if(id===selectedProjectId)return;selectedProjectId=id;selectedOutcomeId=null;detailRequest++;render()}
function chooseProject(){if(!projects.length){selectedProjectId=null;selectedOutcomeId=null;detailRequest++;return}if(!projects.some(p=>p.project_id===selectedProjectId)){selectedProjectId=projects[0].project_id;selectedOutcomeId=null;detailRequest++}const p=selectedProject();if(selectedOutcomeId&&(!p||!p.outcomes.some(o=>o.id===selectedOutcomeId))){selectedOutcomeId=null;detailRequest++}}
function projectCard(p){const chosen=p.project_id===selectedProjectId;return `<article class='card project-card ${chosen?'selected':''} ${p.error?'error':''}' tabindex='0' role='button' data-project='${esc(p.project_id)}' aria-pressed='${chosen}' aria-label='选择项目 ${esc(p.title)}'><h3>${esc(p.title)}</h3><p>${p.error?esc(p.error.message):esc(p.goal)}</p><p class='muted'>${p.error?'待初始化':'成果 '+p.outcomes.length}${chosen?' · 已选中':''}</p></article>`}
function outcomeCard(o,p){const chosen=o.id===selectedOutcomeId;const deadline=o.deadline?.date?`<p class=muted>截止：${esc(o.deadline.date)}（${esc(o.deadline.basis||'待核对')}）</p>`:'';return `<article class='card ${chosen?'selected':''}' tabindex='0' role='button' data-outcome='${esc(o.id)}' aria-pressed='${chosen}' aria-label='查看 ${esc(o.title)} 详情'><h3>${esc(o.title)}</h3><p class=status>${esc(statusName[o.status]||o.status)} · ${esc(o.priority||'normal')}</p><p>${esc(checkpoint(o))}</p><p class=muted>下一步：${esc(o.checkpoint?.next_action||'待核对')}</p>${deadline}${o.activity?`<p class=muted>运行观察（非业务状态）：${esc(o.activity.label||o.activity.state)}</p>`:''}</article>`}
function acceptance(o){const a=Array.isArray(o.acceptance)?o.acceptance:[];if(!a.length)return '完成门槛：地图未记录';return a.filter(x=>x?.result!=='passed').length?`完成门槛：${a.filter(x=>x?.result!=='passed').map(x=>esc(x.text||'待核对')).join('；')}`:'完成门槛：现有验收项均已通过'}
function render(){chooseProject();const ps=$('#projects'),os=$('#outcomes'),p=selectedProject();ps.innerHTML=projects.map(projectCard).join('')||'<p class=muted>没有匹配项目。</p>';ps.querySelectorAll('[data-project]').forEach(e=>{const open=()=>selectProject(e.dataset.project);e.onclick=open;e.onkeydown=x=>{if(x.key==='Enter'||x.key===' '){x.preventDefault();open()}}});os.innerHTML=taskPanel(p)+(p&&!p.error?`<h3>该项目成果（${p.outcomes.length}）</h3>${p.outcomes.map(o=>outcomeCard(o,p)).join('')||'<p class=muted>该项目没有可展示成果。</p>'}`:'');os.querySelectorAll('[data-outcome]').forEach(e=>{const open=()=>detail(selectedProjectId,e.dataset.outcome);e.onclick=open;e.onkeydown=x=>{if(x.key==='Enter'||x.key===' '){x.preventDefault();open()}}});if(!selectedOutcomeId||!p||p.error)$('#detail').innerHTML='<h2>详情</h2><p class=muted>选择一个可读项目和成果查看详情。</p>'}
async function load(){const seq=++loadRequest;const q=new URLSearchParams({q:$('#search').value,status:$('#status-filter').value,sort:$('#sort').value});const r=await fetch('/api/projects?'+q);const data=await r.json();if(seq!==loadRequest)return;projects=data.projects||[];chooseProject();render()}
async function detail(projectId,outcomeId){const p=selectedProject();if(!p||p.project_id!==projectId||!p.outcomes.some(o=>o.id===outcomeId))return;selectedOutcomeId=outcomeId;const seq=++detailRequest;$('#detail').innerHTML='<h2>详情</h2><p class=muted>正在加载所选成果…</p>';render();const response=await fetch(`/api/projects/${encodeURIComponent(projectId)}/outcomes/${encodeURIComponent(outcomeId)}`);const x=await response.json();const current=selectedProject();if(seq!==detailRequest||!response.ok||!current||current.project_id!==projectId||selectedOutcomeId!==outcomeId||!current.outcomes.some(o=>o.id===outcomeId))return;const v=x.outcome;$('#detail').innerHTML=`<h2>${esc(v.title)}</h2><p class=status>${esc(statusName[v.status]||v.status)} · ${esc(v.priority||'normal')}</p><p>${esc(checkpoint(v))}</p><p>${acceptance(v)}</p><p>依赖：${(v.depends_on||[]).map(esc).join('、')||'无'}</p><h3>证据</h3>${(v.evidence||[]).map(e=>`<p class=evidence>${esc(e.summary)}（${esc(e.locator)}）</p>`).join('')||'<p>无可验证证据</p>'}<button id=copy type=button>复制紧凑续做包</button><p class=muted>续做前必须核对 PROJECT_MAP.md 检查点后的变化；不会假定旧会话可读。</p>`;$('#copy').onclick=async()=>{await navigator.clipboard.writeText(x.resume_note);$('#copy').textContent='已复制'};render()}
"""

APP_JS += r"""
function dependencyBlockers(o,byId){return (o.depends_on||[]).map(id=>{const dep=byId[id];return !dep?`${id}（当前筛选中状态未知）`:dep.status==='done'?null:`${dep.title}（${statusName[dep.status]||dep.status}）`}).filter(Boolean)}
function taskPanel(p){
  if(!p)return '<h2>成果</h2><p class=muted>没有匹配项目；请调整搜索或筛选。</p>';
  if(p.error)return `<h2>${esc(p.title)}</h2><p class=error>${esc(p.error.message)}</p>`;
  const outcomes=p.outcomes||[],byId=Object.fromEntries(outcomes.map(o=>[o.id,o]));
  const main=outcomes.find(o=>o.id===p.focus_outcome_id);
  const ready=outcomes.filter(o=>o.status==='not_started'&&!dependencyBlockers(o,byId).length);
  const blocked=outcomes.filter(o=>['not_started','in_progress'].includes(o.status)&&dependencyBlockers(o,byId).length);
  const eligible=outcomes.filter(o=>['in_progress','not_started'].includes(o.status)&&!dependencyBlockers(o,byId).length);
  const recommended=eligible.filter(o=>o.checkpoint?.next_action).sort((a,b)=>(a.status==='in_progress'?0:1)-(b.status==='in_progress'?0:1)||a.title.localeCompare(b.title)||a.id.localeCompare(b.id))[0];
  const decisionClues=outcomes.flatMap(o=>(o.checkpoint?.remaining||[]).filter(x=>/决定|选择|确认/.test(x)).map(x=>[o.title,x]));
  const risks=[...blocked.map(o=>`${o.title} 的依赖尚未满足或当前不可核对`),...eligible.filter(o=>!o.checkpoint?.next_action).map(o=>`${o.title} 缺少唯一下一步`),...outcomes.filter(o=>(o.acceptance||[]).some(a=>a.result==='unverified')).map(o=>`${o.title} 仍有待核对门槛`)];
  const events=[[p.updated_at,'项目地图最近更新']];
  outcomes.forEach(o=>{if(o.checkpoint?.saved_at)events.push([o.checkpoint.saved_at,`${o.title}：${o.checkpoint.last_result||'保存检查点'}`]);(o.evidence||[]).forEach(e=>e?.verified_at&&events.push([e.verified_at,`${o.title}：${e.summary||'验证证据'}`]))});
  events.sort((a,b)=>(b[0]||'').localeCompare(a[0]||''));
  const groups=[['可开始',ready],['进行中',outcomes.filter(o=>o.status==='in_progress')],['依赖阻塞',blocked],['等待',outcomes.filter(o=>o.status==='paused')],['待验收',outcomes.filter(o=>o.status==='pending_review')]];
  return `<h2>${esc(p.title)} · 任务面板</h2>
    <section class=panel><h3>当前主线</h3><p>${main?esc(main.title)+' · '+esc(statusName[main.status]||main.status):'当前筛选未包含地图指定的主线'}</p><p class=muted>${main?esc(checkpoint(main)):'请清除筛选后核对主线'}</p><p>唯一下一步：${main?esc(text(main.checkpoint?.next_action)):'待核对'}</p><p class=muted>依据：${main?esc(text(main.checkpoint?.next_action_basis)):'待核对'}</p></section>
    <section class=panel><h3>推荐下一步</h3><p>${recommended?esc(recommended.title)+'：'+esc(recommended.checkpoint.next_action):'暂无可执行的已知下一步'}</p><p class=muted>依据：仅从进行中或可开始、已知依赖完成且有下一步记录的成果选择；${blocked.length?esc(blocked.length+' 项依赖阻塞或无法核对'):'当前视图没有已知依赖阻塞'}。不确定性：仅使用当前筛选中的地图字段；筛选外的依赖按未知处理。</p></section>
    <section class=panel><h3>可能需要你决定（请核对）</h3>${decisionClues.length?decisionClues.map(([title,clue])=>`<p>${esc(title)}：${esc(clue)}</p>`).join(''):'<p class=muted>剩余项中没有匹配的决定线索；这不代表项目无需决策。</p>'}</section>
    <section class=panel><h3>状态分区</h3>${groups.map(([name,rows])=>`<h4>${name}（${rows.length}）</h4>${rows.length?rows.map(o=>`<p>${esc(o.title)} · ${esc(acceptance(o))}${name==='依赖阻塞'?' · 前置：'+esc(dependencyBlockers(o,byId).join('、')):''}</p>`).join(''):'<p class=muted>暂无</p>'}`).join('')}</section>
    <section class=panel><h3>中断风险与健康</h3><p>健康：地图 revision ${esc(p.revision)}；当前视图 ${outcomes.length} 个成果；最近地图更新 ${esc(p.updated_at)}。</p><p>${risks.length?'风险：'+esc(risks.join('；')):'当前视图未从已知字段发现额外中断风险。'}</p></section>
    <section class=panel><h3>最近可验证变化</h3>${events.filter(e=>e[0]).slice(0,8).map(e=>`<p class=muted>${esc(e[0])} · ${esc(e[1])}</p>`).join('')||'<p class=muted>暂无可验证历史。</p>'}</section>`;
}
$('#search').oninput=load;
$('#status-filter').onchange=load;
$('#sort').onchange=load;
$('#refresh').onclick=async()=>showQuota(await (await fetch('/api/quota/refresh',{method:'POST'})).json());
load();quota();runtime();
"""

# Keep the HTTP and security implementation independent from the interface.
# The compact project-panel assets intentionally override the earlier M2/M5
# prototype strings while API compatibility remains unchanged.
from web_ui import APP_CSS as COMPACT_APP_CSS, APP_JS as COMPACT_APP_JS, INDEX_HTML as COMPACT_INDEX_HTML

INDEX_HTML = COMPACT_INDEX_HTML
APP_CSS = COMPACT_APP_CSS
APP_JS = COMPACT_APP_JS


def main() -> int:
    parser = argparse.ArgumentParser(description="private loopback PROJECT_MAP workbench")
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--host", default="127.0.0.1", choices=["127.0.0.1", "::1"])
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--quota-cache", type=Path, help="explicit router state cache; only quotaDisplay is parsed")
    parser.add_argument("--quota-refresh-script", type=Path, help="optional routerctl.mjs; refresh only runs after the page button")
    parser.add_argument("--activity-file", type=Path, help="optional TTL observation or redacted Progress Bridge JSON")
    parser.add_argument("--recommended-model", default="未配置")
    parser.add_argument("--actual-model", default="未配置")
    args = parser.parse_args()
    refresh = None
    if args.quota_refresh_script:
        script = args.quota_refresh_script.resolve()
        def refresh() -> None:
            subprocess.run(["node", str(script), "quota", "refresh", "--json"], check=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
    app = WorkbenchWeb(ProjectProjection(Store(args.data_dir), ActivityAdapter(args.activity_file)), QuotaAdapter(args.quota_cache, refresh), args.recommended_model, args.actual_model)
    server = ThreadingHTTPServer((args.host, args.port), app.handler())
    print(json.dumps({"listening": f"http://{args.host}:{server.server_port}", "private": True, "model_calls_on_get": 0}), flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()
    return 0


if __name__ == "__main__": raise SystemExit(main())
