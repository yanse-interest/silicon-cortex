#!/usr/bin/env python3
"""Refresh the local read-only H5 after the previous-day reports are ready."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from export_h5_snapshot import DEFAULT_CONFIG, atomic_write, build_h5_snapshot, run_export
from dashboard_refresh_support import snapshot
from dashboard_h5_open_snapshot import generate_fresh_snapshot
from recipe_library import rebuild_recipe_registry
from dashboard_contiguous_catchup import DashboardCatchupError
from registry_migration_gate import EXPECTED_RUNNER, LAUNCHD_LABEL, ONE_SHOT_LAUNCHD_LABEL, TZ


APP_ROOT = Path(__file__).resolve().parent
H5_ROOT = APP_ROOT.parent / "codex-project-dashboard-h5"
H5_SNAPSHOT = H5_ROOT / "public" / "dashboard-snapshot.json"
H5_SERVICE = "com.shiba.codex-project-dashboard-h5"
MEMORY_ROOT = Path("/Users/shiba/Documents/Obsidian/Silicon Cortex/Codex Memory")
RECIPE_REGISTRY = MEMORY_ROOT / "wiki/project-dashboard-recipe-registry.json"
SOURCE_WAIT_INTERVAL_SECONDS = 60
SOURCE_WAIT_MAX_SECONDS = 50 * 60
SOURCE_WAIT_CUTOFF_HOUR = 8
SOURCE_WAIT_CUTOFF_MINUTE = 10


def routing_footer(status: str, reason_codes: list[str], safe_to_retry: bool) -> dict[str, object]:
    return {
        "routing_status": status,
        "reason_codes": reason_codes,
        "recommended_next_effort": "none",
        "safe_to_retry": safe_to_retry,
    }


def is_genuine_launchd_refresh_context() -> bool:
    """Return true only for the exact refresh runner started directly by launchd."""
    return (
        os.environ.get("XPC_SERVICE_NAME") in {LAUNCHD_LABEL, ONE_SHOT_LAUNCHD_LABEL}
        and os.getppid() == 1
        and Path(sys.argv[0]).resolve() == EXPECTED_RUNNER.resolve()
    )


def _local_time(value: datetime) -> datetime:
    return value.replace(tzinfo=TZ) if value.tzinfo is None else value.astimezone(TZ)


def _source_wait_deadline(started_at: datetime) -> datetime:
    local_start = _local_time(started_at)
    daily_cutoff = local_start.replace(
        hour=SOURCE_WAIT_CUTOFF_HOUR,
        minute=SOURCE_WAIT_CUTOFF_MINUTE,
        second=0,
        microsecond=0,
    )
    bounded_cutoff = local_start + timedelta(seconds=SOURCE_WAIT_MAX_SECONDS)
    return min(daily_cutoff, bounded_cutoff)


def _retryable_source_not_ready(exc: DashboardCatchupError) -> bool:
    if exc.code != "contiguous_source_gate_failed":
        return False
    gate = exc.gate if isinstance(exc.gate, dict) else {}
    return (
        gate.get("ready") is not True
        and not bool(gate.get("invalid_sources"))
        and bool(gate.get("missing_sources") or gate.get("incomplete_deposition"))
    )


def run_export_with_launchd_source_wait(
    *,
    now_fn: Callable[[], datetime],
    sleep_fn: Callable[[float], None],
) -> dict[str, object]:
    """Retry only an expected source-readiness race in the genuine 07:20 service."""
    launchd_context = is_genuine_launchd_refresh_context()
    started_at = _local_time(now_fn())
    deadline = _source_wait_deadline(started_at)
    attempts = 0
    waited_seconds = 0.0
    while True:
        attempts += 1
        try:
            result = run_export(config_path=DEFAULT_CONFIG, output=H5_SNAPSHOT, enforce_gates=True)
            if attempts > 1:
                result["launchd_source_wait"] = {
                    "attempts": attempts,
                    "waited_seconds": int(waited_seconds),
                    "deadline": deadline.isoformat(),
                }
            return result
        except DashboardCatchupError as exc:
            if not launchd_context or not _retryable_source_not_ready(exc):
                raise
            observed_at = _local_time(now_fn())
            remaining_seconds = (deadline - observed_at).total_seconds()
            if remaining_seconds <= 0:
                raise
            sleep_seconds = min(float(SOURCE_WAIT_INTERVAL_SECONDS), remaining_seconds)
            print(json.dumps({
                "ok": False,
                "routing_status": "launchd_source_wait",
                "target_date": exc.target_date,
                "missing_sources": exc.gate.get("missing_sources") or [],
                "incomplete_deposition": exc.gate.get("incomplete_deposition") or [],
                "retry_in_seconds": int(sleep_seconds),
                "deadline": deadline.isoformat(),
            }, ensure_ascii=False), flush=True)
            sleep_fn(sleep_seconds)
            waited_seconds += sleep_seconds


def rebuild_from_local_registry() -> dict[str, object]:
    """Rebuild display assets without ingesting sources or advancing the daily gate."""
    synced_through = ""
    if H5_SNAPSHOT.exists():
        current = json.loads(H5_SNAPSHOT.read_text(encoding="utf-8"))
        synced_through = str(current.get("daily_updated_through") or "")
    rebuild_recipe_registry(source_root=MEMORY_ROOT / "wiki/sources/conversations", memory_root=MEMORY_ROOT, output=RECIPE_REGISTRY)
    payload = generate_fresh_snapshot()
    atomic_write(H5_SNAPSHOT, payload)
    return {
        "ok": True,
        "skipped": False,
        "routing_status": "local_registry_rebuild",
        "output": str(H5_SNAPSHOT),
        "generated_at": payload["generated_at"],
        "daily_updated_through": payload["daily_updated_through"],
        "case_count": payload["summary"]["case_count"],
        "value_item_count": payload["summary"]["value_item_count"],
    }


def main(
    *,
    registry_only: bool = False,
    _now_fn: Callable[[], datetime] | None = None,
    _sleep_fn: Callable[[float], None] | None = None,
) -> int:
    try:
        exported = (
            rebuild_from_local_registry()
            if registry_only
            else run_export_with_launchd_source_wait(
                now_fn=_now_fn or (lambda: datetime.now(TZ)),
                sleep_fn=_sleep_fn or time.sleep,
            )
        )
        if exported.get("skipped"):
            print(json.dumps({
                "ok": True,
                "skipped": True,
                "export": exported,
                **routing_footer("idempotent_complete", ["dashboard_already_current"], True),
            }, ensure_ascii=False, indent=2))
            return 0

        rebuild_recipe_registry(source_root=MEMORY_ROOT / "wiki/sources/conversations", memory_root=MEMORY_ROOT, output=RECIPE_REGISTRY)
        atomic_write(H5_SNAPSHOT, generate_fresh_snapshot())

        subprocess.run(["/opt/homebrew/bin/npm", "run", "build"], cwd=H5_ROOT, check=True)
        subprocess.run([
            "/bin/launchctl",
            "kickstart",
            "-k",
            f"gui/{os.getuid()}/{H5_SERVICE}",
        ], check=True)
        print(json.dumps({
            "ok": True,
            "skipped": False,
            "export": exported,
            **routing_footer("local_registry_rebuild" if registry_only else "normal_complete", [], True),
        }, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        message = str(exc)
        user_handoff = message.startswith("user_handoff:")
        print(json.dumps({
            "ok": False,
            "error": message,
            **routing_footer(
                "user_handoff" if user_handoff else "failed_medium",
                ["supported_execution_account_required"] if user_handoff else ["local_h5_refresh_failed"],
                not user_handoff,
            ),
        }, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Refresh the local read-only H5 dashboard.")
    parser.add_argument(
        "--registry-only",
        action="store_true",
        help="Rebuild from the existing local registry without ingesting sources or advancing the daily gate.",
    )
    args = parser.parse_args()
    raise SystemExit(main(registry_only=args.registry_only))
