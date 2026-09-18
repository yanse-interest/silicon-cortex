from __future__ import annotations

import http.client
import json
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from web import ActivityAdapter, ProjectProjection, QuotaAdapter, WorkbenchWeb  # noqa: E402
from workbench import Store, atomic_write, new_map, render_map  # noqa: E402


class M3WebAcceptanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.state = self.base / "state"
        ready = self.base / "ready"
        missing = self.base / "missing"
        ready.mkdir(); missing.mkdir()
        value = new_map("ready-project", "可用项目", "验证 Web 边界", "active-outcome", "活动成果")
        value["architecture"] = {"summary": "地图核心到私有投影", "workflow": ["读取地图", "安全展示"]}
        second = new_map("placeholder", "占位", "占位", "waiting-outcome", "等待成果")["outcomes"][0]
        second.update({"status": "paused", "pause_reason": "等待输入", "depends_on": ["active-outcome"],
                       "deadline": {"date": "2026-09-30", "timezone": "Asia/Shanghai", "basis": "user_confirmed"}})
        second["checkpoint"]["saved_at"] = value["updated_at"]
        value["outcomes"].append(second)
        atomic_write(ready / "PROJECT_MAP.md", render_map("", value))
        self.store = Store(self.state)
        self.store.register("ready-project", ready)
        self.store.register_preview("missing-project", missing)
        self.cache = self.base / "quota.json"
        stamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        self.cache.write_text(json.dumps({"quotaDisplay": {"status": "fresh", "attemptedAt": stamp,
            "currentAccountStatus": "confirmed", "currentAccountSlot": "account-1", "accounts": [
                {"accountSlot": "account-1", "status": "fresh", "fetchedAt": stamp,
                 "windows": [{"kind": "primary", "remainingPercent": 60, "windowDurationMins": 300}]},
                {"accountSlot": "account-2", "status": "fresh", "fetchedAt": stamp,
                 "windows": [{"kind": "primary", "remainingPercent": 40, "windowDurationMins": 300}]},
            ]}}), encoding="utf-8")
        self.refreshes = 0
        def refresh() -> None: self.refreshes += 1
        app = WorkbenchWeb(ProjectProjection(self.store, ActivityAdapter(None)), QuotaAdapter(self.cache, refresh), "Sol high", "Sol high")
        from http.server import ThreadingHTTPServer
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), app.handler())
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown(); self.server.server_close(); self.thread.join(); self.temp.cleanup()

    def request(self, method: str, path: str, headers: dict[str, str] | None = None,
                body: bytes | None = None) -> tuple[int, dict, str, dict[str, str]]:
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse(); raw = response.read().decode(); response_headers = dict(response.getheaders()); connection.close()
        try: data = json.loads(raw)
        except json.JSONDecodeError: data = {}
        return response.status, data, raw, response_headers

    def test_loopback_host_origin_query_method_and_error_boundaries(self) -> None:
        status, data, _, headers = self.request("GET", "/api/projects", {"Host": "evil.invalid"})
        self.assertEqual(403, status); self.assertEqual("origin_rejected", data["error"]["code"])
        status, data, _, _ = self.request("GET", "/api/projects", {"Host": f"[::1]:{self.server.server_port}"})
        self.assertEqual(200, status)
        status, data, _, _ = self.request("GET", "/api/projects?path=/etc/passwd")
        self.assertEqual(400, status); self.assertEqual("invalid_query", data["error"]["code"])
        status, data, _, _ = self.request("PUT", "/api/projects")
        self.assertEqual(405, status); self.assertEqual("method_not_allowed", data["error"]["code"])
        status, data, _, _ = self.request("POST", "/api/quota/refresh", {"Origin": f"http://127.0.0.1:{self.server.server_port}"}, b"x")
        self.assertEqual(400, status); self.assertEqual(0, self.refreshes)
        status, data, _, _ = self.request("POST", "/api/quota/refresh?force=1", {"Origin": f"http://127.0.0.1:{self.server.server_port}"})
        self.assertEqual(404, status); self.assertEqual(0, self.refreshes)
        status, data, _, _ = self.request("POST", "/api/quota/refresh", {"Origin": "http://evil.invalid"})
        self.assertEqual(403, status); self.assertEqual(0, self.refreshes)
        self.assertEqual("nosniff", headers["X-Content-Type-Options"])
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])

    def test_gets_are_zero_model_read_only_and_sort_is_stable(self) -> None:
        before = (self.base / "ready" / "PROJECT_MAP.md").read_bytes()
        for path in ("/api/projects", "/api/projects?q=" + quote("活动"), "/api/projects/revisions", "/api/projects/ready-project/outcomes/active-outcome", "/api/quota", "/api/runtime"):
            status, data, raw, _ = self.request("GET", path)
            self.assertEqual(200, status, raw)
            if path.startswith("/api/projects") and "/outcomes/" not in path:
                self.assertEqual(0, data["model_calls"])
        status, data, _, _ = self.request("GET", "/api/projects")
        self.assertEqual(["active-outcome", "waiting-outcome"], [row["id"] for row in data["projects"][0]["outcomes"]])
        self.assertEqual("地图核心到私有投影", data["projects"][0]["architecture"]["summary"])
        status, data, _, _ = self.request("GET", "/api/projects?status=paused&sort=title")
        ready = next(row for row in data["projects"] if row["project_id"] == "ready-project")
        self.assertEqual(["waiting-outcome"], [row["id"] for row in ready["outcomes"]])
        self.assertEqual("2026-09-30", ready["outcomes"][0]["deadline"]["date"])
        status, data, _, _ = self.request("GET", "/api/projects?sort=unknown")
        self.assertEqual(400, status); self.assertEqual("invalid_query", data["error"]["code"])
        self.assertEqual(before, (self.base / "ready" / "PROJECT_MAP.md").read_bytes())
        self.assertEqual(0, self.refreshes)

    def test_missing_and_corrupt_project_are_visible_without_hiding_healthy_project(self) -> None:
        corrupt_root = self.base / "corrupt"
        corrupt_root.mkdir()
        value = new_map("corrupt-project", "稍后损坏", "错误隔离", "corrupt-outcome", "损坏成果")
        atomic_write(corrupt_root / "PROJECT_MAP.md", render_map("", value))
        self.store.register("corrupt-project", corrupt_root)
        (corrupt_root / "PROJECT_MAP.md").write_bytes(b"truncated")
        status, data, _, _ = self.request("GET", "/api/projects")
        self.assertEqual(200, status)
        rows = {row["project_id"]: row for row in data["projects"]}
        self.assertIn("ready-project", rows)
        self.assertEqual("map_missing", rows["missing-project"]["error"]["code"])
        self.assertEqual("invalid_map", rows["corrupt-project"]["error"]["code"])
        self.assertNotIn(str(corrupt_root), json.dumps(data, ensure_ascii=False))

    def test_ui_has_keyboard_copy_fallback_and_no_guessed_navigation(self) -> None:
        status, _, html, _ = self.request("GET", "/")
        self.assertEqual(200, status); self.assertNotIn("8791", html); self.assertNotIn("8792", html)
        status, _, js, _ = self.request("GET", "/app.js")
        self.assertEqual(200, status)
        self.assertIn("tabindex='0'", js); self.assertIn("x.key==='Enter'", js)
        self.assertIn("status-filter", html); self.assertIn("截止：", js); self.assertIn("依赖：", js)
        self.assertIn("复制紧凑续做包", js); self.assertIn("不会假定旧会话可读", js)
        self.assertIn("v.activity.label||v.activity.state", js)
        self.assertNotIn("o.activity.label||o.activity.state", js)
        self.assertIn("quotaWindowLabel", js); self.assertIn("5 小时额度", js); self.assertIn("周额度", js)
        self.assertIn("setInterval(checkRevisions,10000)", js)
        self.assertIn("not_started:'待开始'", js)
        for removed in ("推荐下一步", "可能需要你决定（请核对）", "中断风险与健康", "最近可验证变化"):
            self.assertNotIn(removed, js)
        self.assertNotIn("codex://", js)

    def test_corrupt_registry_returns_safe_json_error(self) -> None:
        self.store.registry_path.write_bytes(b"not-json")
        status, data, raw, _ = self.request("GET", "/api/projects")
        self.assertEqual(503, status); self.assertEqual("registry_unavailable", data["error"]["code"])
        self.assertNotIn(str(self.state), raw)


class M3QuotaAndActivityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.now = datetime(2026, 9, 17, 12, 0, tzinfo=timezone.utc)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write_cache(self, display: object) -> Path:
        path = self.base / "state.json"
        path.write_text(json.dumps({"quotaDisplay": display}), encoding="utf-8")
        return path

    def adapter(self, display: object, refresh=None) -> QuotaAdapter:
        return QuotaAdapter(self.write_cache(display), refresh, clock=lambda: self.now)

    def test_partial_stale_extra_bucket_and_secret_fields_are_sanitized(self) -> None:
        fresh = self.now.isoformat().replace("+00:00", "Z")
        stale = (self.now - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
        display = {"status": "partial", "attemptedAt": fresh, "currentAccountStatus": "confirmed", "currentAccountSlot": "account-1",
                   "auth": "SECRET-AUTH", "token": "SECRET-TOKEN", "accounts": [
                       {"accountSlot": "account-1", "status": "fresh", "fetchedAt": fresh, "cookie": "SECRET-COOKIE",
                        "windows": [{"limitId": "codex", "kind": "primary", "remainingPercent": 55, "windowDurationMins": 300},
                                    {"limitId": "codex", "kind": "secondary", "remainingPercent": 44, "windowDurationMins": 10080},
                                    {"limitId": "additional:gpt-reserve", "kind": "primary", "remainingPercent": 33, "windowDurationMins": 10080}]},
                       {"accountSlot": "account-2", "status": "error", "errorCode": "quota_offline", "fetchedAt": stale,
                        "authorization": "SECRET-AUTHORIZATION", "windows": [{"kind": "primary", "remainingPercent": 20}]},
                   ]}
        result = self.adapter(display).read()
        self.assertTrue(result["available"]); self.assertEqual("partial", result["quota"]["status"])
        self.assertEqual("fresh", result["quota"]["accounts"][0]["status"])
        self.assertEqual(3, len(result["quota"]["accounts"][0]["windows"]))
        self.assertEqual(["codex", "codex", "additional:gpt-reserve"],
                         [window["limit_id"] for window in result["quota"]["accounts"][0]["windows"]])
        self.assertEqual("stale", result["quota"]["accounts"][1]["status"])
        serialized = json.dumps(result, ensure_ascii=False).lower()
        for secret in ("secret-auth", "secret-token", "secret-cookie", "secret-authorization", "authorization", "cookie", "token"):
            self.assertNotIn(secret, serialized)

    def test_missing_account_fields_future_time_offline_and_corrupt_cache_fail_closed(self) -> None:
        fresh = self.now.isoformat().replace("+00:00", "Z")
        result = self.adapter({"status": "fresh", "attemptedAt": fresh, "currentAccountStatus": "confirmed", "currentAccountSlot": "account-9",
                               "accounts": [{"accountSlot": "account-1", "status": "fresh", "windows": [{"kind": "primary", "remainingPercent": 99}]}]}).read()
        self.assertEqual("unavailable", result["quota"]["status"])
        self.assertIsNone(result["quota"]["current_account_slot"])
        self.assertEqual(["unavailable", "unavailable"], [account["status"] for account in result["quota"]["accounts"]])

        absent = QuotaAdapter(self.base / "absent.json", clock=lambda: self.now).read()
        self.assertFalse(absent["available"])
        corrupt = self.base / "corrupt.json"; corrupt.write_bytes(b"{")
        self.assertFalse(QuotaAdapter(corrupt, clock=lambda: self.now).read()["available"])
        self.assertFalse(QuotaAdapter(None, clock=lambda: self.now).read()["available"])
        null_lists = self.adapter({"status": "error", "accounts": None}).read()
        self.assertEqual(["unavailable", "unavailable"], [account["status"] for account in null_lists["quota"]["accounts"]])

    def test_get_never_refreshes_command_failure_and_concurrent_clicks_merge(self) -> None:
        fresh = self.now.isoformat().replace("+00:00", "Z")
        display = {"status": "fresh", "attemptedAt": fresh, "currentAccountStatus": "unknown", "accounts": []}
        calls = 0
        def failed() -> None:
            nonlocal calls
            calls += 1
            raise OSError("offline")
        adapter = self.adapter(display, failed)
        adapter.read(); self.assertEqual(0, calls)
        self.assertEqual("quota_refresh_failed", adapter.refresh_explicitly()["error"]["code"]); self.assertEqual(1, calls)

        entered = threading.Event(); release = threading.Event(); merged_calls = 0
        def slow() -> None:
            nonlocal merged_calls
            merged_calls += 1; entered.set(); release.wait(5)
        adapter = self.adapter(display, slow)
        first: list[dict] = []
        thread = threading.Thread(target=lambda: first.append(adapter.refresh_explicitly()))
        thread.start(); self.assertTrue(entered.wait(2))
        second = adapter.refresh_explicitly()
        self.assertEqual("quota_refresh_in_progress", second["error"]["code"])
        release.set(); thread.join(5)
        self.assertEqual(1, merged_calls); self.assertEqual(1, len(first))

    def test_stopped_timeout_and_quota_observations_never_change_business_status(self) -> None:
        root = self.base / "project"; root.mkdir()
        state = self.base / "private"
        value = new_map("activity-project", "运行隔离", "观察不是业务状态", "active-outcome", "活动成果")
        atomic_write(root / "PROJECT_MAP.md", render_map("", value))
        store = Store(state); store.register("activity-project", root)
        expiry = (datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat().replace("+00:00", "Z")
        activity = self.base / "activity.json"
        activity.write_text('{"observations": null}', encoding="utf-8")
        self.assertIsNone(ProjectProjection(store, ActivityAdapter(activity)).detail("activity-project", "active-outcome")["outcome"]["activity"])
        for observed in ("stopped", "timed_out", "waiting_quota", "refresh_failed"):
            activity.write_text(json.dumps({"observations": [{"project_id": "activity-project", "outcome_id": "active-outcome",
                "state": observed, "expires_at": expiry}]}), encoding="utf-8")
            detail = ProjectProjection(store, ActivityAdapter(activity)).detail("activity-project", "active-outcome")
            self.assertEqual("in_progress", detail["outcome"]["status"])
            if observed != "stopped": self.assertIsNone(detail["outcome"]["activity"])

    def test_real_progress_bridge_rows_require_explicit_thread_link_and_remain_ttl_only(self) -> None:
        root = self.base / "bridge-project"; root.mkdir()
        state = self.base / "bridge-private"
        value = new_map("bridge-project", "真实活动", "只读关联真实 Stop", "bridge-outcome", "活动成果")
        value["outcomes"][0]["conversations"] = [{"thread_id": "thread-live", "host_id": "local"}]
        atomic_write(root / "PROJECT_MAP.md", render_map("", value))
        store = Store(state); store.register("bridge-project", root)
        bridge = self.base / "live-state.json"
        bridge.write_text(json.dumps({"schema_version": 1, "stale_after_seconds": 1800, "tasks": [
            {"session_id": "thread-live", "turn_id": "old", "status": "completed", "updated_at": "2026-09-17T11:40:00Z"},
            {"session_id": "thread-live", "turn_id": "current", "status": "running", "updated_at": "2026-09-17T11:59:00Z",
             "prompt_preview": "SECRET-PROMPT", "changed_files": ["SECRET-FILE"]},
            {"session_id": "unlinked-thread", "status": "failed", "updated_at": "2026-09-17T11:59:30Z"},
        ]}), encoding="utf-8")
        projection = ProjectProjection(store, ActivityAdapter(bridge, clock=lambda: self.now))
        detail = projection.detail("bridge-project", "bridge-outcome")["outcome"]
        self.assertEqual("in_progress", detail["status"])
        self.assertEqual("running", detail["activity"]["state"])
        self.assertEqual("codex_progress_bridge", detail["activity"]["source"])
        self.assertNotIn("secret", json.dumps(detail, ensure_ascii=False).lower())

        bridge.write_text(json.dumps({"schema_version": 1, "stale_after_seconds": 1800, "tasks": [
            {"session_id": "thread-live", "turn_id": "current", "status": "completed", "updated_at": "2026-09-17T12:00:00Z"}
        ]}), encoding="utf-8")
        stopped = projection.detail("bridge-project", "bridge-outcome")["outcome"]
        self.assertEqual("in_progress", stopped["status"])
        self.assertEqual("stopped", stopped["activity"]["state"])
        self.assertIn("Stop", stopped["activity"]["label"])

        expired = ProjectProjection(store, ActivityAdapter(bridge, clock=lambda: self.now + timedelta(minutes=31)))
        self.assertIsNone(expired.detail("bridge-project", "bridge-outcome")["outcome"]["activity"])


class M3ScaleProbeTests(unittest.TestCase):
    def test_twenty_projects_five_hundred_outcomes_meet_local_targets(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            base = Path(name); state = base / "state"; store = Store(state)
            for project_index in range(20):
                root = base / f"project-{project_index:02d}"; root.mkdir()
                value = new_map(f"project-{project_index:02d}", f"项目 {project_index:02d}", "设计负载", "outcome-00", "成果 00")
                stamp = value["updated_at"]
                value["outcomes"] = []
                for outcome_index in range(25):
                    value["outcomes"].append({"id": f"outcome-{outcome_index:02d}", "title": f"成果 {outcome_index:02d}", "status": "not_started",
                        "priority": "normal", "acceptance": [{"id": "acceptance", "text": "待核验", "result": "unverified"}]})
                atomic_write(root / "PROJECT_MAP.md", render_map("", value)); store.register(f"project-{project_index:02d}", root)
            projection = ProjectProjection(store, ActivityAdapter(None))
            start = time.perf_counter(); rows = projection.list(); cold = time.perf_counter() - start
            start = time.perf_counter(); hits = projection.list("成果 24"); warm = time.perf_counter() - start
            self.assertEqual(20, len(rows)); self.assertEqual(500, sum(len(row["outcomes"]) for row in rows))
            self.assertEqual(20, len(hits)); self.assertLess(cold, 2.0); self.assertLess(warm, 0.3)


if __name__ == "__main__":
    unittest.main()
