from __future__ import annotations

import json
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from tempfile import TemporaryDirectory
from unittest.mock import patch
from zoneinfo import ZoneInfo

from dashboard_refresh_support import DashboardRefreshError, account_guard, load_config, previous_day_source_gate
from daily_deposition_receipt import receipt_path


class DashboardRefreshSupportTests(unittest.TestCase):
    def test_load_config_requires_account_policy(self) -> None:
        with TemporaryDirectory() as raw_root:
            path = Path(raw_root) / "config.json"
            path.write_text(json.dumps({"schema_version": 1}), encoding="utf-8")
            with self.assertRaises(DashboardRefreshError):
                load_config(path)
            path.write_text(
                json.dumps({"account_policy": self.account_policy()}),
                encoding="utf-8",
            )
            self.assertEqual(
                load_config(path)["account_policy"]["allowed_account_ids"],
                ["account-1", "account-2"],
            )

    @staticmethod
    def account_policy() -> dict[str, object]:
        return {
            "allowed_account_ids": ["account-1", "account-2"],
            "require_paused": True,
            "require_auto_switch_off": True,
            "require_automation_disabled": True,
        }

    @staticmethod
    def router_status(current_account_id: str = "account-1") -> dict[str, object]:
        return {
            "desktop": {"currentAccountId": current_account_id},
            "accounts": [
                {"id": "account-1", "loggedIn": True},
                {"id": "account-2", "loggedIn": True},
            ],
            "paused": True,
            "autoSwitchWhenSafe": False,
            "automationStatus": "disabled",
        }

    @patch("dashboard_refresh_support.subprocess.run")
    def test_account_guard_allows_either_logged_in_supported_account(self, run) -> None:
        for account_id in ("account-1", "account-2"):
            with self.subTest(account_id=account_id):
                run.return_value = SimpleNamespace(
                    returncode=0,
                    stdout=json.dumps(self.router_status(account_id)),
                )
                route = account_guard(self.account_policy())
                self.assertEqual(route["account_id"], account_id)
                self.assertTrue(route["logged_in"])

    @patch("dashboard_refresh_support.subprocess.run")
    def test_account_guard_rejects_unsupported_or_logged_out_selected_account(self, run) -> None:
        cases = []
        unsupported = self.router_status("account-3")
        unsupported["accounts"].append({"id": "account-3", "loggedIn": True})
        cases.append(unsupported)
        logged_out = self.router_status("account-2")
        logged_out["accounts"][1]["loggedIn"] = False
        cases.append(logged_out)
        missing_login_attestation = self.router_status("account-2")
        del missing_login_attestation["accounts"][1]["loggedIn"]
        cases.append(missing_login_attestation)

        for status in cases:
            with self.subTest(status=status):
                run.return_value = SimpleNamespace(returncode=0, stdout=json.dumps(status))
                with self.assertRaisesRegex(
                    DashboardRefreshError,
                    "supported_execution_account_required",
                ):
                    account_guard(self.account_policy())

    @patch("dashboard_refresh_support.subprocess.run")
    def test_account_guard_requires_all_auto_off_flags(self, run) -> None:
        for key, invalid_value in (
            ("paused", False),
            ("autoSwitchWhenSafe", True),
            ("automationStatus", "enabled"),
        ):
            with self.subTest(key=key):
                status = self.router_status("account-2")
                status[key] = invalid_value
                run.return_value = SimpleNamespace(returncode=0, stdout=json.dumps(status))
                with self.assertRaisesRegex(
                    DashboardRefreshError,
                    "supported_execution_account_required",
                ):
                    account_guard(self.account_policy())

    @patch("dashboard_refresh_support.subprocess.run")
    def test_account_guard_treats_unreadable_router_status_as_user_handoff(self, run) -> None:
        for returncode, stdout in ((1, ""), (0, "not-json"), (0, "[]")):
            with self.subTest(returncode=returncode, stdout=stdout):
                run.return_value = SimpleNamespace(returncode=returncode, stdout=stdout)
                with self.assertRaisesRegex(
                    DashboardRefreshError,
                    "supported_execution_account_required",
                ):
                    account_guard(self.account_policy())

    def test_previous_day_gate_requires_both_exact_dated_source_summaries(self) -> None:
        with TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            chatgpt = root / "chatgpt-daily/2026/chatgpt-daily-report-2026-08-02.md"
            codex = root / "codex-daily/2026/codex-daily-report-2026-08-02.md"
            chatgpt.parent.mkdir(parents=True)
            codex.parent.mkdir(parents=True)
            chatgpt.write_text(
                "---\ntype: chatgpt_daily_source_summary\ndate: 2026-08-02\nstatus: access_incomplete\n---\n",
                encoding="utf-8",
            )
            now = datetime(2026, 8, 3, 7, 20, tzinfo=ZoneInfo("Asia/Shanghai"))
            missing = previous_day_source_gate(now=now, source_root=root)
            self.assertFalse(missing["ready"])
            self.assertEqual(missing["missing_sources"], ["codex"])
            codex.write_text(
                "---\ntype: codex_daily_source_summary\ndate: 2026-08-02\nstatus: no_tasks\n---\n",
                encoding="utf-8",
            )
            ready = previous_day_source_gate(now=now, source_root=root)
            self.assertTrue(ready["ready"])
            self.assertEqual(ready["target_date"], "2026-08-02")

    def test_previous_day_gate_requires_deposition_before_refresh(self) -> None:
        with TemporaryDirectory() as raw_root:
            memory = Path(raw_root)
            root = memory / "wiki/sources/conversations"
            target = "2026-08-23"
            paths = {
                "chatgpt": root / f"chatgpt-daily/2026/chatgpt-daily-report-{target}.md",
                "codex": root / f"codex-daily/2026/codex-daily-report-{target}.md",
            }
            for family, path in paths.items():
                path.parent.mkdir(parents=True, exist_ok=True)
                source_type = f"{family}_daily_source_summary"
                directory = f"{family}-daily"
                raw_relative = f"raw/conversations/{directory}/2026/{family}-daily-report-{target}.md"
                raw = memory / raw_relative
                raw.parent.mkdir(parents=True, exist_ok=True)
                raw.write_text(
                    f"---\ntype: {family}_daily_report\ndate: {target}\nstatus: access_incomplete\n---\n",
                    encoding="utf-8",
                )
                instrument = (
                    "\n## Instrument Knowledge Candidates\n\n```json\n[]\n```\n"
                    if family == "chatgpt" else ""
                )
                path.write_text(
                    f"---\ntype: {source_type}\ndate: {target}\ncoverage: partial\n"
                    f"status: access_incomplete\nraw_source: {raw_relative}\n---\n"
                    "\n## Structured Candidates\n\n```json\n[]\n```\n"
                    + instrument,
                    encoding="utf-8",
                )
            now = datetime(2026, 8, 24, 7, 20, tzinfo=ZoneInfo("Asia/Shanghai"))
            blocked = previous_day_source_gate(now=now, source_root=root)
            self.assertFalse(blocked["ready"])
            self.assertEqual(blocked["incomplete_deposition"], ["chatgpt", "codex"])

            for family, path in paths.items():
                receipt = receipt_path(memory, f"{family}-daily", target)
                receipt.parent.mkdir(parents=True, exist_ok=True)
                receipt.write_text(json.dumps({
                    "version": 1,
                    "status": "completed",
                    "source_family": family,
                    "source_date": target,
                    "source_path": path.resolve().as_posix(),
                    "source_sha256": __import__("hashlib").sha256(path.read_bytes()).hexdigest(),
                    "raw_sha256": __import__("hashlib").sha256(
                        (memory / f"raw/conversations/{family}-daily/2026/{family}-daily-report-{target}.md").read_bytes()
                    ).hexdigest(),
                    "raw_files_modified": 0,
                    "actions_dispatched": 0,
                    "candidate_ids": [],
                    "candidate_count": 0,
                    "reviewed_unit_count": 0,
                    "extraction_result": "no_relevant_content",
                }), encoding="utf-8")
            ready = previous_day_source_gate(now=now, source_root=root)
            self.assertTrue(ready["ready"])
            self.assertEqual(ready["incomplete_deposition"], [])


if __name__ == "__main__":
    unittest.main()
