from __future__ import annotations

import http.client
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from web import APP_JS, ActivityAdapter, ProjectProjection, QuotaAdapter, WorkbenchWeb  # noqa: E402
from workbench import Store, atomic_write, new_map, render_map  # noqa: E402


class WorkbenchWebTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.state, root = base / "state", base / "ready"
        root.mkdir(); (base / "missing").mkdir()
        value = new_map("ready-project", "真实样式项目", "由地图投影的目标", "first-outcome", "可搜索的成果")
        outcome = value["outcomes"][0]
        outcome["pause_reason"] = "等待无关验证"; outcome["status"] = "paused"
        atomic_write(root / "PROJECT_MAP.md", render_map("", value))
        self.store = Store(self.state); self.store.register("ready-project", root)
        self.store.register_preview("missing-project", base / "missing")
        self.cache = base / "router-state.json"
        self.cache.write_text(json.dumps({"quotaDisplay": {"status": "partial", "attemptedAt": "2026-09-17T00:00:00Z", "currentAccountStatus": "unknown", "accounts": [{"accountSlot": "account-1", "status": "fresh", "windows": [{"kind": "five_hour", "remainingPercent": 33, "resetsAt": 1}]}]}}), encoding="utf-8")
        self.refreshes = 0
        def refresh() -> None: self.refreshes += 1
        app = WorkbenchWeb(ProjectProjection(self.store, ActivityAdapter(None)), QuotaAdapter(self.cache, refresh), "规划模型", "实际模型")
        from http.server import ThreadingHTTPServer
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), app.handler())
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown(); self.server.server_close(); self.thread.join(); self.temp.cleanup()

    def request(self, method: str, path: str, headers: dict[str, str] | None = None, body: bytes | None = None) -> tuple[int, dict, str]:
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse(); body = response.read().decode(); connection.close()
        try: return response.status, json.loads(body), body
        except json.JSONDecodeError: return response.status, {}, body

    def test_two_projects_search_detail_and_missing_map_error(self) -> None:
        status, data, _ = self.request("GET", "/api/projects?q=" + quote("可搜索"))
        self.assertEqual(200, status); self.assertEqual(0, data["model_calls"]); self.assertEqual("ready-project", data["projects"][0]["project_id"])
        status, data, _ = self.request("GET", "/api/projects")
        missing = next(row for row in data["projects"] if row["project_id"] == "missing-project")
        self.assertEqual("map_missing", missing["error"]["code"]); self.assertEqual([], missing["outcomes"])
        status, data, _ = self.request("GET", "/api/projects/ready-project/outcomes/first-outcome")
        self.assertEqual(200, status); self.assertIn("不要假定旧会话", data["resume_note"])
        self.assertIn("当前业务状态：paused", data["resume_note"])
        self.assertIn("仍需完成：完成并核验该成果的验收条件", data["resume_note"])
        self.assertIn("本次唯一下一步：阅读成果检查点并继续当前范围内工作", data["resume_note"])

    def test_search_handles_a_completed_outcome_without_next_action(self) -> None:
        root = Path(self.temp.name) / "completed"
        root.mkdir()
        value = new_map("completed-project", "已完成项目", "完成态搜索边界", "finished-outcome", "可搜索完成成果")
        outcome = value["outcomes"][0]
        outcome["status"] = "done"
        outcome["acceptance"][0].update({"result": "passed", "evidence_ids": ["completion-evidence"]})
        outcome["evidence"] = [{"id": "completion-evidence", "kind": "file", "locator": "README.md",
                                "summary": "隔离的完成态搜索夹具。", "verified_at": value["updated_at"], "verified_by": "test_runner"}]
        outcome["checkpoint"].update({"last_result": "完成态没有下一步。", "remaining": [], "next_action": None, "next_action_basis": "none"})
        atomic_write(root / "PROJECT_MAP.md", render_map("", value))
        self.store.register("completed-project", root)
        status, data, raw = self.request("GET", "/api/projects?q=" + quote("完成成果"))
        self.assertEqual(200, status, raw)
        self.assertEqual(["completed-project"], [row["project_id"] for row in data["projects"]])

    def test_cache_get_is_read_only_refresh_is_explicit_and_stopped_never_changes_status(self) -> None:
        status, data, _ = self.request("GET", "/api/quota")
        self.assertEqual(200, status); self.assertTrue(data["available"]); self.assertEqual(0, self.refreshes)
        status, data, _ = self.request("POST", "/api/quota/refresh", {"Origin": f"http://127.0.0.1:{self.server.server_port}"})
        self.assertEqual(200, status); self.assertEqual(1, self.refreshes)
        # An observation is intentionally absent here: even a supplied stopped
        # observation cannot be written through this read-only HTTP service.
        status, data, _ = self.request("GET", "/api/projects/ready-project/outcomes/first-outcome")
        self.assertEqual("paused", data["outcome"]["status"])

    def test_origin_isolation_and_responsive_assets(self) -> None:
        status, data, _ = self.request("POST", "/api/quota/refresh", {"Origin": "http://evil.invalid"})
        self.assertEqual(403, status); self.assertEqual("origin_rejected", data["error"]["code"]); self.assertEqual(0, self.refreshes)
        status, _, css = self.request("GET", "/app.css")
        self.assertEqual(200, status); self.assertIn("@media(max-width:899px)", css); self.assertIn("@media(max-width:560px)", css)
        self.assertIn("color-scheme:light dark", css); self.assertIn("@media(prefers-color-scheme:light)", css)
        self.assertIn("--bg:#f7f7f8", css); self.assertIn("--bg:#111318", css)
        status, _, html = self.request("GET", "/")
        self.assertEqual(200, status); self.assertIn("viewport", html); self.assertIn("id='manager'", html); self.assertIn("join-preview", html); self.assertIn("id='manager-close' aria-label='关闭项目管理' class='close' type='button'", html); self.assertNotIn("8791", html); self.assertNotIn("8792", html)
        status, _, js = self.request("GET", "/app.js")
        self.assertEqual(200, status); self.assertIn("X-Workbench-Token", js); self.assertIn("showModal", js); self.assertIn("$('#manager-close').onclick=()=>manager.close()", js); self.assertIn("@media(max-width:560px)", css)

    def test_project_selection_scopes_outcomes_to_one_project(self) -> None:
        second = Path(self.temp.name) / "second"; second.mkdir()
        value = new_map("second-project", "第二项目", "防止跨项目成果混列", "same-outcome", "第二项目的成果")
        atomic_write(second / "PROJECT_MAP.md", render_map("", value)); self.store.register("second-project", second)
        status, data, _ = self.request("GET", "/api/projects")
        self.assertEqual(200, status)
        self.assertEqual("first-outcome", next(p for p in data["projects"] if p["project_id"] == "ready-project")["focus_outcome_id"])
        status, detail, _ = self.request("GET", "/api/projects/second-project/outcomes/same-outcome")
        self.assertEqual(200, status); self.assertEqual("第二项目的成果", detail["outcome"]["title"])
        status, _, js = self.request("GET", "/app.js")
        self.assertEqual(200, status)
        self.assertIn("selectedProjectId", js); self.assertIn("data-project", js); self.assertIn("focusOutcome", js); self.assertIn("p.outcomes.filter", js)
        self.assertIn("p.project_id!==projectId", js)
        self.assertNotIn("projects.flatMap", js)
        self.assertEqual(1, js.count("function taskPanel("))
        self.assertIn("setInterval(checkRevisions,10000)", js)
        status, revisions, _ = self.request("GET", "/api/projects/revisions")
        self.assertEqual(200, status); self.assertEqual(0, revisions["model_calls"])
        self.assertEqual({"ready-project", "missing-project", "second-project"}, {row["project_id"] for row in revisions["revisions"]})
        regression = subprocess.run(["node", str(Path(__file__).with_name("ui_regression.mjs"))],
                                    input=APP_JS, text=True, encoding="utf-8", capture_output=True, check=False)
        self.assertEqual(0, regression.returncode, regression.stderr)

    def test_management_preview_apply_archive_restore_remove_needs_token_and_preserves_map(self) -> None:
        root = Path(self.temp.name) / "managed"; root.mkdir()
        atomic_write(root / "PROJECT_MAP.md", render_map("", new_map("managed-project", "受管项目", "测试成员管理", "managed-outcome", "成果")))
        status, view, _ = self.request("GET", "/api/management")
        self.assertEqual(200, status); token = view["token"]
        payload = {"project_id": "managed-project", "root_path": str(root), "map_path": "PROJECT_MAP.md"}
        raw = json.dumps(payload).encode()
        headers = {"Origin": f"http://127.0.0.1:{self.server.server_port}", "Content-Type": "application/json"}
        status, data, _ = self.request("POST", "/api/management/preview", headers, raw)
        self.assertEqual(403, status); self.assertEqual("management_token_rejected", data["error"]["code"])
        headers["X-Workbench-Token"] = token
        status, data, _ = self.request("POST", "/api/management/preview", headers, raw)
        self.assertEqual(200, status); preview = data["preview"]
        apply = dict(payload, expected_registry_revision=preview["registry_revision"], confirm_project_id="managed-project")
        status, data, _ = self.request("POST", "/api/management/apply", headers, json.dumps(apply).encode())
        self.assertEqual(200, status); revision = data["result"]["registry_revision"]
        status, data, _ = self.request("POST", "/api/management/archive", headers, json.dumps({"project_id":"managed-project", "expected_registry_revision":revision}).encode())
        self.assertEqual(200, status); self.assertNotIn("managed-project", [row["project_id"] for row in self.request("GET", "/api/projects")[1]["projects"]])
        revision = data["result"]["registry_revision"]
        status, data, _ = self.request("POST", "/api/management/restore", headers, json.dumps({"project_id":"managed-project", "expected_registry_revision":revision}).encode())
        self.assertEqual(200, status); revision = data["result"]["registry_revision"]
        remove = {"project_id":"managed-project", "expected_registry_revision":revision, "confirm_project_id":"managed-project", "confirm_remove":True}
        status, data, _ = self.request("POST", "/api/management/remove", headers, json.dumps(remove).encode())
        self.assertEqual(200, status); self.assertTrue((root / "PROJECT_MAP.md").is_file())


if __name__ == "__main__": unittest.main()
