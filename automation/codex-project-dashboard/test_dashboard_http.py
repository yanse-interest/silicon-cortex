from __future__ import annotations

import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen

import dashboard_server
from dashboard_model import CaseRegistry


class DashboardHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        wiki = root / "wiki"
        reviews = wiki / "reviews"
        daily = wiki / "sources/conversations/chatgpt-daily/2026"
        reviews.mkdir(parents=True)
        daily.mkdir(parents=True)
        (reviews / "monthly-case-manifest-2026-07.json").write_text(
            json.dumps(
                {
                    "month": "2026-07",
                    "cases": [
                        {
                            "case_id": "case-c15a153c9e934f8e",
                            "title": "复杂修饰多肽与硫醚环肽氧化辅助解序",
                            "line": "work",
                            "status": "in_progress",
                            "current_progress": "原摘要",
                            "timeline": [],
                            "open_items": [],
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (daily / "chatgpt-daily-report-2026-08-01.md").write_text(
            """---
type: chatgpt_daily_source_summary
date: 2026-08-01
coverage: partial
status: access_incomplete
raw_source: raw/example.md
---
## Session Inventory

- S01 - `5C充电解释`: explanation of battery C-rate meaning.
""",
            encoding="utf-8",
        )
        self.store = CaseRegistry(
            wiki_root=wiki,
            registry_path=wiki / "registry.json",
            reviews_root=reviews,
            daily_root=wiki / "sources/conversations/chatgpt-daily",
            live_state_path=root / "missing-live-state.json",
        )
        self.original_store = dashboard_server.STORE
        dashboard_server.STORE = self.store
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard_server.DashboardHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.token = dashboard_server.access_token()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        dashboard_server.STORE = self.original_store
        self.temp.cleanup()

    def request(self, path: str, payload: dict | None = None) -> dict:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = Request(
            self.base + path,
            data=body,
            headers={"X-Dashboard-Token": self.token, "Content-Type": "application/json"},
            method="GET" if payload is None else "POST",
        )
        with urlopen(request, timeout=3) as response:
            return json.loads(response.read().decode("utf-8"))

    def test_reference_route_set_current_and_undo_over_http(self) -> None:
        initial = self.request("/api/dashboard")
        self.assertEqual(initial["summary"]["reference_count"], 1)
        routed = self.request(
            "/api/event/route",
            {
                "event_id": "chatgpt-daily:2026-08-01:S01",
                "case_id": "case-c15a153c9e934f8e",
                "set_current": True,
            },
        )
        self.assertTrue(routed["ok"])
        after_route = self.request("/api/dashboard")
        self.assertEqual(after_route["summary"]["reference_count"], 0)
        self.assertEqual(after_route["all_cases"][0]["current_summary"], "explanation of battery C-rate meaning.")
        undone = self.request(
            "/api/event/undo",
            {"event_id": "chatgpt-daily:2026-08-01:S01"},
        )
        self.assertTrue(undone["ok"])
        after_undo = self.request("/api/dashboard")
        self.assertEqual(after_undo["summary"]["reference_count"], 1)
        self.assertEqual(after_undo["all_cases"][0]["current_summary"], "原摘要")

    def test_page_is_cross_source_project_view_not_realtime_task_view(self) -> None:
        html = dashboard_server.page_html().decode("utf-8")
        self.assertIn("项目进度与价值沉淀", html)
        self.assertIn("项目资料夹", html)
        self.assertIn("Codex 任务与日报", html)
        self.assertIn("groupedProjectLogs", html)
        self.assertIn("ChatGPT + Codex 跨来源整合", html)
        self.assertNotIn("Codex 实时任务", html)
        self.assertNotIn("liveTasks", html)
        self.assertNotIn("setInterval(refresh,60000)", html)
        self.assertIn("setInterval(refresh,300000)", html)
        self.assertNotIn("Case 名称", html)
        self.assertIn("部分来源", html)
        self.assertIn("验证", html)
        self.assertIn("更新进度 / 状态", html)
        self.assertIn("manualProjectStatus", html)

    def test_management_page_uses_collapsed_project_and_inbox_folders(self) -> None:
        html = dashboard_server.page_html().decode("utf-8")
        self.assertIn('<details class="project-folder" data-project-folder=', html)
        self.assertNotIn('<details class="project-folder" open', html)
        self.assertIn('<details class="panel" id="inboxFolder">', html)
        self.assertNotIn('<details class="panel" id="inboxFolder" open', html)
        self.assertIn('<details class="panel" id="inboxReferenceFolder">', html)
        self.assertNotIn('<details class="panel" id="inboxReferenceFolder" open', html)
        self.assertIn('<details class="panel" id="capabilityFolder">', html)
        self.assertIn('证据组 ${item.evidence_unit_count||0} · 原始 ${item.log_count||0}', html)
        self.assertIn('日报与任务证据 · ${item.evidence_unit_count||0} 组', html)
        self.assertIn('待确认关联 / 可能相关证据', html)
        self.assertIn('snapshot.inbox_topic_groups', html)

    def test_project_folder_aggregates_many_tasks_with_visible_provenance(self) -> None:
        html = dashboard_server.page_html().decode("utf-8")
        self.assertIn("function groupedProjectLogs(item,full=false)", html)
        self.assertIn("item.evidence_units||item.logs", html)
        self.assertIn("log.title||'项目进展'", html)
        self.assertIn("log.first_date&&log.last_date", html)
        self.assertIn("log.member_count>1", html)
        self.assertIn("${esc(log.source)} · ${esc(evidenceLabel(log))}", html)
        self.assertIn("Codex 任务与日报", html)
        self.assertIn("snapshot.inbox_review_units", html)
        self.assertIn("snapshot.inbox_topic_groups", html)
        self.assertIn("暂留原因：", html)
        self.assertIn("路由或忽略只处理当前代表记录", html)
        self.assertIn("kind==='candidate'?", html)

    def test_manual_progress_is_saved_as_current_and_kept_in_timeline(self) -> None:
        saved = self.request(
            "/api/case/manual-update",
            {
                "case_id": "case-c15a153c9e934f8e",
                "detail": "已完成第一阶段，正在确认下一步。",
                "status": "in_progress",
            },
        )
        self.assertTrue(saved["ok"])
        snapshot = self.request("/api/dashboard")
        case = snapshot["all_cases"][0]
        self.assertEqual(case["current_summary"], "已完成第一阶段，正在确认下一步。")
        self.assertEqual(case["logs"][0]["source_kind"], "manual")
        self.assertEqual(case["logs"][0]["title"], "手动更新项目状态与进度")

    def test_manual_status_only_update_keeps_current_progress(self) -> None:
        saved = self.request(
            "/api/case/manual-update",
            {
                "case_id": "case-c15a153c9e934f8e",
                "detail": "",
                "status": "on_hold",
            },
        )
        self.assertTrue(saved["ok"])
        snapshot = self.request("/api/dashboard")
        case = snapshot["all_cases"][0]
        self.assertEqual(case["status"], "on_hold")
        self.assertEqual(case["current_summary"], "原摘要")
        self.assertEqual(case["logs"][0]["detail"], "项目状态：进行中 → 暂缓")

    def test_loopback_can_open_dashboard_without_token(self) -> None:
        with urlopen(self.base + "/api/dashboard", timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
        self.assertIn("summary", payload)

    def test_only_loopback_addresses_bypass_token(self) -> None:
        self.assertTrue(dashboard_server.is_loopback_client("127.0.0.1"))
        self.assertTrue(dashboard_server.is_loopback_client("::1"))
        self.assertFalse(dashboard_server.is_loopback_client("192.168.1.20"))
        self.assertFalse(dashboard_server.is_loopback_client("mbp.local"))


if __name__ == "__main__":
    unittest.main()
