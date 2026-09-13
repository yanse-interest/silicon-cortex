from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from unittest.mock import call, patch
from zoneinfo import ZoneInfo

import dashboard_h5_refresh
from dashboard_contiguous_catchup import DashboardCatchupError


TZ = ZoneInfo("Asia/Shanghai")


class MutableClock:
    def __init__(self, value: datetime) -> None:
        self.value = value
        self.sleeps: list[float] = []

    def now(self) -> datetime:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.value += timedelta(seconds=seconds)


class DashboardH5RefreshTests(unittest.TestCase):
    @staticmethod
    def source_not_ready_error(*, invalid: bool = False) -> DashboardCatchupError:
        return DashboardCatchupError(
            "contiguous_source_gate_failed",
            "contiguous Dashboard refresh stopped at 2026-08-29",
            target_date="2026-08-29",
            gate={
                "ready": False,
                "missing_sources": [] if invalid else ["chatgpt"],
                "incomplete_deposition": [],
                "invalid_sources": ["chatgpt"] if invalid else [],
            },
        )

    @patch("dashboard_h5_refresh.subprocess.run")
    @patch("dashboard_h5_refresh.run_export")
    def test_skipped_export_does_not_build_or_restart(self, run_export, run) -> None:
        run_export.return_value = {"ok": True, "skipped": True}
        self.assertEqual(dashboard_h5_refresh.main(), 0)
        run.assert_not_called()

    @patch("dashboard_h5_refresh.subprocess.run")
    @patch("dashboard_h5_refresh.run_export")
    def test_success_builds_and_restarts_h5(self, run_export, run) -> None:
        run_export.return_value = {"ok": True, "skipped": False}
        self.assertEqual(dashboard_h5_refresh.main(), 0)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args_list[0], call(
            ["/opt/homebrew/bin/npm", "run", "build"],
            cwd=dashboard_h5_refresh.H5_ROOT,
            check=True,
        ))
        self.assertIn("com.shiba.codex-project-dashboard-h5", run.call_args_list[1].args[0][-1])

    @patch("dashboard_h5_refresh.subprocess.run")
    @patch("dashboard_h5_refresh.rebuild_from_local_registry")
    @patch("dashboard_h5_refresh.run_export")
    def test_registry_only_rebuilds_without_account_gated_export(self, run_export, rebuild, run) -> None:
        rebuild.return_value = {"ok": True, "skipped": False}
        self.assertEqual(dashboard_h5_refresh.main(registry_only=True), 0)
        rebuild.assert_called_once_with()
        run_export.assert_not_called()
        self.assertEqual(run.call_count, 2)

    @patch("dashboard_h5_refresh.subprocess.run")
    @patch("dashboard_h5_refresh.is_genuine_launchd_refresh_context", return_value=True)
    @patch("dashboard_h5_refresh.run_export")
    def test_launchd_source_not_ready_retries_then_builds_and_restarts_once(
        self,
        run_export,
        launchd_context,
        run,
    ) -> None:
        run_export.side_effect = [
            self.source_not_ready_error(),
            {"ok": True, "skipped": False},
        ]
        clock = MutableClock(datetime(2026, 8, 30, 7, 20, tzinfo=TZ))

        self.assertEqual(
            dashboard_h5_refresh.main(_now_fn=clock.now, _sleep_fn=clock.sleep),
            0,
        )

        self.assertEqual(clock.sleeps, [60.0])
        self.assertEqual(run_export.call_count, 2)
        self.assertEqual(run_export.call_args_list[0], run_export.call_args_list[1])
        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args_list[0], call(
            ["/opt/homebrew/bin/npm", "run", "build"],
            cwd=dashboard_h5_refresh.H5_ROOT,
            check=True,
        ))
        self.assertIn("com.shiba.codex-project-dashboard-h5", run.call_args_list[1].args[0][-1])
        launchd_context.assert_called_once_with()

    @patch("dashboard_h5_refresh.subprocess.run")
    @patch("dashboard_h5_refresh.is_genuine_launchd_refresh_context", return_value=True)
    @patch("dashboard_h5_refresh.run_export")
    def test_genuine_0720_process_can_finish_bounded_wait_after_0739(
        self,
        run_export,
        launchd_context,
        run,
    ) -> None:
        run_export.side_effect = [self.source_not_ready_error() for _ in range(20)] + [
            {"ok": True, "skipped": False}
        ]
        clock = MutableClock(datetime(2026, 8, 31, 7, 20, tzinfo=TZ))

        self.assertEqual(
            dashboard_h5_refresh.main(_now_fn=clock.now, _sleep_fn=clock.sleep),
            0,
        )

        self.assertEqual(clock.value, datetime(2026, 8, 31, 7, 40, tzinfo=TZ))
        self.assertEqual(clock.sleeps, [60.0] * 20)
        self.assertEqual(run_export.call_count, 21)
        self.assertEqual(run.call_count, 2)
        launchd_context.assert_called_once_with()

    @patch("dashboard_h5_refresh.subprocess.run")
    @patch("dashboard_h5_refresh.is_genuine_launchd_refresh_context", return_value=False)
    @patch("dashboard_h5_refresh.run_export")
    def test_manual_source_not_ready_fails_fast(self, run_export, launchd_context, run) -> None:
        run_export.side_effect = self.source_not_ready_error()
        clock = MutableClock(datetime(2026, 8, 30, 7, 20, tzinfo=TZ))

        self.assertEqual(
            dashboard_h5_refresh.main(_now_fn=clock.now, _sleep_fn=clock.sleep),
            2,
        )

        run_export.assert_called_once_with(
            config_path=dashboard_h5_refresh.DEFAULT_CONFIG,
            output=dashboard_h5_refresh.H5_SNAPSHOT,
            enforce_gates=True,
        )
        self.assertEqual(clock.sleeps, [])
        run.assert_not_called()
        launchd_context.assert_called_once_with()

    @patch("dashboard_h5_refresh.subprocess.run")
    @patch("dashboard_h5_refresh.is_genuine_launchd_refresh_context", return_value=True)
    @patch("dashboard_h5_refresh.run_export")
    def test_launchd_invalid_source_fails_fast(self, run_export, launchd_context, run) -> None:
        run_export.side_effect = self.source_not_ready_error(invalid=True)
        clock = MutableClock(datetime(2026, 8, 30, 7, 20, tzinfo=TZ))

        self.assertEqual(
            dashboard_h5_refresh.main(_now_fn=clock.now, _sleep_fn=clock.sleep),
            2,
        )

        self.assertEqual(run_export.call_count, 1)
        self.assertEqual(clock.sleeps, [])
        run.assert_not_called()
        launchd_context.assert_called_once_with()

    @patch("dashboard_h5_refresh.subprocess.run")
    @patch("dashboard_h5_refresh.is_genuine_launchd_refresh_context", return_value=True)
    @patch("dashboard_h5_refresh.run_export")
    def test_launchd_source_wait_stops_at_daily_deadline(self, run_export, launchd_context, run) -> None:
        run_export.side_effect = [self.source_not_ready_error(), self.source_not_ready_error()]
        clock = MutableClock(datetime(2026, 8, 30, 8, 9, 30, tzinfo=TZ))

        self.assertEqual(
            dashboard_h5_refresh.main(_now_fn=clock.now, _sleep_fn=clock.sleep),
            2,
        )

        self.assertEqual(run_export.call_count, 2)
        self.assertEqual(clock.sleeps, [30.0])
        self.assertLessEqual(max(clock.sleeps), 60.0)
        run.assert_not_called()
        launchd_context.assert_called_once_with()

    @patch("dashboard_h5_refresh.subprocess.run")
    @patch("dashboard_h5_refresh.is_genuine_launchd_refresh_context", return_value=True)
    @patch("dashboard_h5_refresh.run_export")
    def test_launchd_source_wait_is_also_bounded_to_fifty_minutes(
        self,
        run_export,
        launchd_context,
        run,
    ) -> None:
        run_export.side_effect = [self.source_not_ready_error() for _ in range(51)]
        clock = MutableClock(datetime(2026, 8, 30, 6, 30, tzinfo=TZ))

        self.assertEqual(
            dashboard_h5_refresh.main(_now_fn=clock.now, _sleep_fn=clock.sleep),
            2,
        )

        self.assertEqual(run_export.call_count, 51)
        self.assertEqual(clock.sleeps, [60.0] * 50)
        self.assertEqual(
            dashboard_h5_refresh._source_wait_deadline(
                datetime(2026, 8, 30, 6, 30, tzinfo=TZ)
            ),
            datetime(2026, 8, 30, 7, 20, tzinfo=TZ),
        )
        run.assert_not_called()
        launchd_context.assert_called_once_with()

    @patch.object(dashboard_h5_refresh.sys, "argv", [str(dashboard_h5_refresh.EXPECTED_RUNNER)])
    @patch.object(dashboard_h5_refresh.os, "getppid", return_value=1)
    @patch.dict(
        dashboard_h5_refresh.os.environ,
        {"XPC_SERVICE_NAME": dashboard_h5_refresh.LAUNCHD_LABEL},
    )
    def test_launchd_context_requires_exact_service_parent_and_runner(self, getppid) -> None:
        self.assertTrue(dashboard_h5_refresh.is_genuine_launchd_refresh_context())
        with patch.dict(
            dashboard_h5_refresh.os.environ,
            {"XPC_SERVICE_NAME": dashboard_h5_refresh.ONE_SHOT_LAUNCHD_LABEL},
        ):
            self.assertTrue(dashboard_h5_refresh.is_genuine_launchd_refresh_context())
        with patch.dict(
            dashboard_h5_refresh.os.environ,
            {"XPC_SERVICE_NAME": f"{dashboard_h5_refresh.LAUNCHD_LABEL}.manual"},
        ):
            self.assertFalse(dashboard_h5_refresh.is_genuine_launchd_refresh_context())
        with patch.object(dashboard_h5_refresh.os, "getppid", return_value=2):
            self.assertFalse(dashboard_h5_refresh.is_genuine_launchd_refresh_context())
        with patch.object(dashboard_h5_refresh.sys, "argv", ["/private/tmp/manual-refresh.py"]):
            self.assertFalse(dashboard_h5_refresh.is_genuine_launchd_refresh_context())
        getppid.assert_called()


if __name__ == "__main__":
    unittest.main()
