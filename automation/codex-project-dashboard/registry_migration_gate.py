#!/usr/bin/env python3
"""Evidence-honest acceptance gate for the V1.0 registry migration.

The gate deliberately separates two evidence classes:

* seven calendar-date isolated historical replays prove deterministic replay,
  parity, privacy, input immutability, and cleanup;
* one post-enforcement production refresh proves the installed 07:20 scheduled
  path can complete with the same protections.

Historical evidence never mutates the frozen legacy ``stage-2-streak.json``.
Live eligibility is derived here from runtime and committed receipt provenance;
there is no caller-supplied eligibility boolean.
"""

from __future__ import annotations

import hashlib
import json
import os
import plistlib
import subprocess
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


SCHEMA_VERSION = 1
ENFORCEMENT_VERSION = "stage2-accelerated-provenance-v4"
GATE_POLICY_ID = "seven-isolated-calendar-replays-plus-one-post-enforcement-scheduled-refresh"
LAUNCHD_LABEL = "com.shiba.codex-project-dashboard-h5-refresh"
ONE_SHOT_LAUNCHD_LABEL = f"{LAUNCHD_LABEL}.v1-acceptance-once"
TZ = ZoneInfo("Asia/Shanghai")
PROJECT_DIR = Path(__file__).resolve().parent
EXPECTED_RUNNER = PROJECT_DIR / "dashboard_h5_refresh.py"
PROJECT_PLIST = PROJECT_DIR / f"{LAUNCHD_LABEL}.plist"
INSTALLED_PLIST = Path.home() / "Library/LaunchAgents" / f"{LAUNCHD_LABEL}.plist"
INSTALLED_ONE_SHOT_PLIST = Path.home() / "Library/LaunchAgents" / f"{ONE_SHOT_LAUNCHD_LABEL}.plist"
DEFAULT_GATE_ROOT = Path(
    "/Users/shiba/Documents/Obsidian/Silicon Cortex/Codex Memory/"
    "wiki/review-cycles/registry-migration-gate"
)
DEFAULT_LEGACY_STREAK = Path(
    "/Users/shiba/Documents/Obsidian/Silicon Cortex/Codex Memory/"
    "wiki/review-cycles/registry-shadow-compare/stage-2-streak.json"
)
DEFAULT_ONE_SHOT_AUTHORIZATION = DEFAULT_GATE_ROOT / "scheduler-one-shot-authorization.json"


class MigrationGateError(RuntimeError):
    """Fail-closed gate validation error."""


def _pretty_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _atomic_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(_pretty_bytes(payload))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else ""


def plist_semantic_sha256(path: Path) -> str:
    """Hash the complete parsed plist value, independent of its wire format.

    Re-encoding as a sorted binary plist preserves plist scalar types while
    removing XML whitespace, key-order, and XML-vs-binary representation
    differences.  Any extra/missing key or type/value/array change therefore
    produces a different digest.
    """
    try:
        payload = plistlib.loads(path.read_bytes())
        canonical = plistlib.dumps(payload, fmt=plistlib.FMT_BINARY, sort_keys=True)
    except (OSError, TypeError, ValueError, plistlib.InvalidFileException):
        return ""
    return hashlib.sha256(canonical).hexdigest()


def _parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise MigrationGateError("timestamp must include timezone")
    return parsed.astimezone(TZ)


def _relative(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _valid_launchagent(path: Path) -> bool:
    try:
        payload = plistlib.loads(path.read_bytes())
    except (OSError, plistlib.InvalidFileException):
        return False
    interval = payload.get("StartCalendarInterval") or {}
    arguments = payload.get("ProgramArguments") or []
    return (
        payload.get("Label") == LAUNCHD_LABEL
        and interval.get("Hour") == 7
        and interval.get("Minute") == 20
        and len(arguments) >= 2
        and Path(str(arguments[1])).resolve() == EXPECTED_RUNNER.resolve()
    )


def _valid_authorized_one_shot_launchagent(path: Path, authorization: dict[str, Any]) -> bool:
    try:
        payload = plistlib.loads(path.read_bytes())
        scheduled_at = _parse_iso(str(authorization.get("scheduled_at") or ""))
    except (OSError, ValueError, MigrationGateError, plistlib.InvalidFileException):
        return False
    interval = payload.get("StartCalendarInterval") or {}
    arguments = payload.get("ProgramArguments") or []
    return (
        payload.get("Label") == ONE_SHOT_LAUNCHD_LABEL
        and interval.get("Year") == scheduled_at.year
        and interval.get("Month") == scheduled_at.month
        and interval.get("Day") == scheduled_at.day
        and interval.get("Hour") == scheduled_at.hour
        and interval.get("Minute") == scheduled_at.minute
        and len(arguments) >= 2
        and Path(str(arguments[1])).resolve() == EXPECTED_RUNNER.resolve()
        and payload.get("RunAtLoad") is not True
    )


def _load_one_shot_authorization(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _observation_time() -> datetime:
    """Return commit-time observation time; tests patch this private helper only."""
    return datetime.now(TZ)


def _current_process_started_at() -> datetime | None:
    """Read this process's OS start time from macOS ``ps`` and fail closed.

    The PID is always derived from :func:`os.getpid`; neither callers nor the
    environment can supply a different process or timestamp.  A hard-coded C
    locale keeps the macOS ``lstart`` representation deterministic.
    """
    try:
        completed = subprocess.run(
            ["/bin/ps", "-o", "lstart=", "-p", str(os.getpid())],
            check=True,
            capture_output=True,
            text=True,
            env={"LC_ALL": "C"},
            timeout=5,
        )
        value = completed.stdout.strip()
        if not value or "\n" in value:
            return None
        parsed = datetime.strptime(value, "%a %b %d %H:%M:%S %Y")
        return parsed.replace(tzinfo=TZ)
    except (OSError, subprocess.SubprocessError, UnicodeError, ValueError):
        return None


def capture_scheduled_runtime_provenance(*, target_date: str) -> dict[str, Any]:
    """Derive (never accept) scheduled provenance from the current process."""
    observed = _observation_time().astimezone(TZ)
    process_started_at = _current_process_started_at()
    expected_source_date = (
        (process_started_at.date() - timedelta(days=1)).isoformat()
        if process_started_at is not None
        else ""
    )
    project_hash = sha256_path(PROJECT_PLIST)
    installed_hash = sha256_path(INSTALLED_PLIST)
    project_semantic_hash = plist_semantic_sha256(PROJECT_PLIST)
    installed_semantic_hash = plist_semantic_sha256(INSTALLED_PLIST)
    service_name = os.environ.get("XPC_SERVICE_NAME") or ""
    regular_context = service_name == LAUNCHD_LABEL
    one_shot_context = service_name == ONE_SHOT_LAUNCHD_LABEL
    authorization = _load_one_shot_authorization(DEFAULT_ONE_SHOT_AUTHORIZATION) if one_shot_context else {}
    authorization_path_hash = sha256_path(DEFAULT_ONE_SHOT_AUTHORIZATION) if authorization else ""
    installed_one_shot_hash = sha256_path(INSTALLED_ONE_SHOT_PLIST) if one_shot_context else ""
    try:
        one_shot_scheduled_at = _parse_iso(str(authorization.get("scheduled_at") or ""))
        one_shot_expires_at = _parse_iso(str(authorization.get("expires_at") or ""))
    except (ValueError, MigrationGateError):
        one_shot_scheduled_at = None
        one_shot_expires_at = None
    one_shot_authorization_valid = bool(
        authorization.get("schema_version") == 1
        and authorization.get("artifact_type") == "dashboard_refresh_scheduler_one_shot_authorization"
        and authorization.get("status") == "authorized"
        and authorization.get("run_once") is True
        and authorization.get("launchd_label") == ONE_SHOT_LAUNCHD_LABEL
        and authorization.get("target_date") == target_date
        and authorization.get("installed_plist_sha256") == installed_one_shot_hash
        and one_shot_scheduled_at is not None
        and one_shot_expires_at is not None
        and process_started_at is not None
        and one_shot_scheduled_at - timedelta(seconds=60) <= process_started_at <= one_shot_expires_at
        and observed <= one_shot_expires_at
        and _valid_authorized_one_shot_launchagent(INSTALLED_ONE_SHOT_PLIST, authorization)
    )
    checks = {
        "launchd_service_environment": regular_context or one_shot_context,
        "launchd_parent_pid": os.getppid() == 1,
        "exact_runner": Path(sys.argv[0]).resolve() == EXPECTED_RUNNER.resolve(),
        "authoritative_process_start_time": process_started_at is not None,
        "project_launchagent_contract": _valid_launchagent(PROJECT_PLIST),
        "installed_launchagent_contract": _valid_launchagent(INSTALLED_PLIST),
        "installed_plist_matches_project": (
            bool(project_semantic_hash) and project_semantic_hash == installed_semantic_hash
        ),
    }
    if regular_context:
        checks.update({
            "scheduled_window_0720": (
                process_started_at is not None
                and process_started_at.hour == 7
                and 20 <= process_started_at.minute <= 39
            ),
            "previous_calendar_date": (
                process_started_at is not None and target_date == expected_source_date
            ),
        })
        schedule_kind = "regular_0720"
    else:
        checks.update({
            "authorized_one_shot": one_shot_authorization_valid,
            "authorized_one_shot_target_date": authorization.get("target_date") == target_date,
            "authorized_one_shot_plist_hash": bool(installed_one_shot_hash) and authorization.get("installed_plist_sha256") == installed_one_shot_hash,
        })
        schedule_kind = "authorized_one_shot"
    failed = sorted(key for key, passed in checks.items() if not passed)
    return {
        "schema_version": SCHEMA_VERSION,
        "enforcement_version": ENFORCEMENT_VERSION,
        "observed_at": observed.isoformat(),
        "process_started_at": process_started_at.isoformat() if process_started_at is not None else "",
        "process_start_source": "/bin/ps -o lstart= -p <current-os-pid>",
        "target_date": target_date,
        "expected_source_date": expected_source_date,
        "launchd_label": LAUNCHD_LABEL,
        "observed_launchd_label": service_name,
        "schedule_kind": schedule_kind,
        "parent_pid": os.getppid(),
        "runner": EXPECTED_RUNNER.resolve().as_posix(),
        "project_plist_sha256": project_hash,
        "installed_plist_sha256": installed_hash,
        "project_plist_semantic_sha256": project_semantic_hash,
        "installed_plist_semantic_sha256": installed_semantic_hash,
        "one_shot_authorization": DEFAULT_ONE_SHOT_AUTHORIZATION.as_posix() if authorization else "",
        "one_shot_authorization_sha256": authorization_path_hash,
        "installed_one_shot_plist_sha256": installed_one_shot_hash,
        "checks": checks,
        "eligible_runtime": not failed,
        "failure_reasons": failed,
    }


def install_enforcement_marker(
    *,
    gate_root: Path = DEFAULT_GATE_ROOT,
    installed_at: str = "",
) -> dict[str, Any]:
    """Install the current immutable boundary; identical reruns are safe.

    A policy-version upgrade preserves the prior boundary as audit evidence and
    starts a new post-enforcement clock.  Same-version hash drift remains a hard
    conflict rather than silently moving the boundary.
    """
    marker_path = gate_root / "provenance-enforcement-installation.json"
    module_hash = sha256_path(Path(__file__).resolve())
    project_hash = sha256_path(PROJECT_PLIST)
    installed_hash = sha256_path(INSTALLED_PLIST)
    project_semantic_hash = plist_semantic_sha256(PROJECT_PLIST)
    installed_semantic_hash = plist_semantic_sha256(INSTALLED_PLIST)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "registry_migration_provenance_enforcement_installation",
        "enforcement_version": ENFORCEMENT_VERSION,
        "installed_at": installed_at or datetime.now(TZ).isoformat(),
        "policy_module": Path(__file__).resolve().as_posix(),
        "policy_module_sha256": module_hash,
        "launchd_label": LAUNCHD_LABEL,
        "scheduled_time": "07:20 Asia/Shanghai; one date-bound scheduler one-shot allowed only by exact authorization receipt",
        "process_start_time_source": "/bin/ps -o lstart= -p <current-os-pid>",
        "schedule_window_basis": "authoritative_process_start_time",
        "commit_observation_time_separate": True,
        "plist_equivalence_policy": "complete_parsed_plist_semantics_v1",
        "project_plist_sha256": project_hash,
        "installed_plist_sha256": installed_hash,
        "project_plist_semantic_sha256": project_semantic_hash,
        "installed_plist_semantic_sha256": installed_semantic_hash,
        "status": "installed",
    }
    if marker_path.exists():
        existing = json.loads(marker_path.read_text(encoding="utf-8"))
        if existing.get("enforcement_version") == ENFORCEMENT_VERSION:
            if existing.get("policy_module_sha256") != module_hash:
                raise MigrationGateError("same-version provenance enforcement module hash conflict")
            return existing
        payload["superseded_installation"] = {
            "enforcement_version": existing.get("enforcement_version"),
            "installed_at": existing.get("installed_at"),
            "policy_module_sha256": existing.get("policy_module_sha256"),
            "reason": "adds one exact user-authorized date-bound scheduler one-shot without weakening the regular 07:20 contract",
            "prior_superseded_installation": existing.get("superseded_installation"),
        }
    _atomic_write(marker_path, payload)
    return payload


def _legacy_streak_summary(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"status": "missing", "path": path.as_posix()}
    state = json.loads(path.read_text(encoding="utf-8"))
    return {
        "status": "preserved_frozen_legacy_evidence",
        "path": path.as_posix(),
        "sha256": sha256_path(path),
        "baseline_policy_version": state.get("baseline_policy_version"),
        "consecutive_eligible_successes": int(state.get("consecutive_eligible_successes") or 0),
        "required_successes": int(state.get("required_successes") or 7),
        "observation_count": len(state.get("observations") or []),
        "acceptance_effect_under_new_policy": "historical_context_only",
    }


def _load_passed_receipts(root: Path, pattern: str, artifact_type: str) -> list[tuple[Path, dict[str, Any]]]:
    receipts: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(root.glob(pattern)):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if payload.get("artifact_type") == artifact_type and payload.get("status") == "passed":
            receipts.append((path, payload))
    return receipts


def build_gate_summary(
    *,
    gate_root: Path = DEFAULT_GATE_ROOT,
    legacy_streak_path: Path = DEFAULT_LEGACY_STREAK,
) -> dict[str, Any]:
    marker_path = gate_root / "provenance-enforcement-installation.json"
    marker = json.loads(marker_path.read_text(encoding="utf-8")) if marker_path.is_file() else {}
    migration_state_path = gate_root / "migration-stage-state.json"
    migration_state = (
        json.loads(migration_state_path.read_text(encoding="utf-8"))
        if migration_state_path.is_file() else {}
    )
    migration_state_valid = (
        migration_state.get("artifact_type") == "project_dashboard_registry_migration_stage_state"
        and migration_state.get("status") == "passed"
    )
    historical = _load_passed_receipts(
        gate_root / "historical-replays",
        "registry-historical-replay-*.json",
        "project_dashboard_registry_historical_replay_receipt",
    )
    live = _load_passed_receipts(
        gate_root / "live-refreshes",
        "registry-live-refresh-*.json",
        "project_dashboard_registry_live_refresh_provenance_receipt",
    )

    by_date: dict[str, tuple[Path, dict[str, Any]]] = {}
    for path, payload in historical:
        source_date = str(payload.get("source_date") or "")
        if source_date:
            by_date[source_date] = (path, payload)
    selected_dates = sorted(by_date)
    consecutive = bool(selected_dates)
    for left, right in zip(selected_dates, selected_dates[1:]):
        consecutive = consecutive and date.fromisoformat(right) - date.fromisoformat(left) == timedelta(days=1)
    historical_ready = (
        len(selected_dates) == 7
        and consecutive
        and all((payload.get("checks") or {}).get("all_required_checks_passed") is True for _, payload in by_date.values())
    )
    historical_entries = [
        {
            "source_date": source_date,
            "classification": payload.get("date_contract_classification"),
            "receipt": _relative(path, gate_root.parent.parent.parent),
            "receipt_sha256": sha256_path(path),
            "source_generation_sha256": payload.get("source_generation_sha256"),
        }
        for source_date, (path, payload) in sorted(by_date.items())
    ]

    installation_time = _parse_iso(str(marker.get("installed_at"))) if marker.get("installed_at") else None
    eligible_live: list[tuple[Path, dict[str, Any]]] = []
    for path, payload in live:
        observed_at = str(payload.get("observed_at") or "")
        try:
            after_install = installation_time is not None and _parse_iso(observed_at) > installation_time
        except (ValueError, MigrationGateError):
            after_install = False
        if (
            payload.get("enforcement_version") == ENFORCEMENT_VERSION
            and payload.get("evidence_class") in {
                "post_enforcement_real_0720_production_refresh",
                "post_enforcement_authorized_scheduler_one_shot_production_refresh",
            }
            and after_install
            and (payload.get("checks") or {}).get("all_required_checks_passed") is True
        ):
            eligible_live.append((path, payload))
    live_ready = len(eligible_live) >= 1
    ready = historical_ready and live_ready
    summary = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "project_dashboard_registry_migration_acceptance_gate",
        "gate_policy_id": GATE_POLICY_ID,
        "enforcement_version": ENFORCEMENT_VERSION,
        "updated_at": datetime.now(TZ).isoformat(),
        "status": "ready_for_stage3_approval_request" if ready else "not_ready",
        "ready_for_stage3_approval_request": ready,
        "historical_replay_gate": {
            "required_distinct_calendar_dates": 7,
            "observed_distinct_calendar_dates": len(selected_dates),
            "dates": selected_dates,
            "consecutive": consecutive,
            "status": "passed" if historical_ready else "incomplete",
            "entries": historical_entries,
            "evidence_limitations": [
                "Historical replay proves deterministic reconstruction/parity/privacy/input immutability for archived inputs, not unattended scheduler continuity.",
                "Current manual state is overlaid where source-stable IDs match; it is not a full historical time-machine reconstruction of past owner edits.",
                "Historical replay has no effect on the frozen legacy live streak and cannot satisfy the real-refresh requirement.",
            ],
        },
        "post_enforcement_live_refresh_gate": {
            "required": 1,
            "observed": len(eligible_live),
            "status": "passed" if live_ready else "incomplete",
            "installation_marker": _relative(marker_path, gate_root.parent.parent.parent) if marker else "",
            "installed_at": marker.get("installed_at", ""),
            "eligible_receipts": [
                {
                    "source_date": payload.get("source_date"),
                    "process_started_at": payload.get("process_started_at"),
                    "observed_at": payload.get("observed_at"),
                    "receipt": _relative(path, gate_root.parent.parent.parent),
                    "receipt_sha256": sha256_path(path),
                }
                for path, payload in eligible_live
            ],
        },
        "legacy_stage2_streak": _legacy_streak_summary(legacy_streak_path),
        "remaining_event": (
            "none for this factual Stage-2 runtime gate"
            if ready else "one provenance-eligible scheduler-originated production refresh after enforcement installation"
            if historical_ready else "complete seven valid distinct consecutive historical replay receipts"
        ),
        "sequencing_authority": _relative(migration_state_path, gate_root.parent.parent.parent) if migration_state_valid else "",
        "scheduled_runtime_validation_deferred": bool(
            migration_state_valid and migration_state.get("scheduled_runtime_validation_deferred") is True
        ),
        "stage3_authorized": bool(migration_state_valid and migration_state.get("stage3_authorized") is True),
        "dual_write_enabled": bool(migration_state_valid and migration_state.get("dual_write_enabled") is True),
        "cutover_enabled": bool(migration_state_valid and migration_state.get("cutover_enabled") is True),
    }
    return summary


def publish_gate_summary(
    *,
    gate_root: Path = DEFAULT_GATE_ROOT,
    legacy_streak_path: Path = DEFAULT_LEGACY_STREAK,
) -> dict[str, Any]:
    summary = build_gate_summary(gate_root=gate_root, legacy_streak_path=legacy_streak_path)
    _atomic_write(gate_root / "stage-2-acceptance-gate.json", summary)
    return summary


def refresh_commit_chain(payload: dict[str, Any]) -> str:
    fields = {
        key: payload.get(key)
        for key in (
            "source_date", "source_generation_sha256", "observation_id",
            "registry_before_sha256", "registry_after_sha256",
            "h5_before_sha256", "h5_after_sha256", "stage2_receipt_sha256",
            "committed_at", "production_source", "provenance_enforcement_version",
            "manual_state_sha256", "replay_state_sha256", "split_generation_id",
            "dual_write_enabled", "cutover_enabled",
        )
    }
    return hashlib.sha256(
        json.dumps(fields, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _verify_bound_inputs(inputs: dict[str, Any]) -> bool:
    if set(inputs) != {"chatgpt", "codex"}:
        return False
    required = ("source_path", "source_sha256", "raw_path", "raw_sha256", "receipt_path", "receipt_sha256")
    for details in inputs.values():
        if not isinstance(details, dict) or any(not details.get(key) for key in required):
            return False
        for path_key, hash_key in (("source_path", "source_sha256"), ("raw_path", "raw_sha256"), ("receipt_path", "receipt_sha256")):
            path = Path(str(details[path_key]))
            if not path.is_file() or sha256_path(path) != details[hash_key]:
                return False
    return True


def record_committed_scheduled_refresh(
    *,
    refresh_receipt_path: Path,
    registry_path: Path,
    h5_snapshot_path: Path,
    gate_root: Path = DEFAULT_GATE_ROOT,
    legacy_streak_path: Path = DEFAULT_LEGACY_STREAK,
) -> dict[str, Any]:
    """Record a live gate observation only from verified committed provenance."""
    refresh = json.loads(refresh_receipt_path.read_text(encoding="utf-8"))
    target_date = str(refresh.get("source_date") or "")
    runtime = capture_scheduled_runtime_provenance(target_date=target_date)
    marker_path = gate_root / "provenance-enforcement-installation.json"
    marker = json.loads(marker_path.read_text(encoding="utf-8")) if marker_path.is_file() else {}
    stage2_path = Path(str(refresh.get("stage2_receipt") or ""))
    stage2 = json.loads(stage2_path.read_text(encoding="utf-8")) if stage2_path.is_file() else {}
    manual_path = registry_path.with_name("project-dashboard-manual-state.json")
    replay_path = registry_path.with_name("project-dashboard-replay-state.json")
    cutover = refresh.get("cutover_enabled") is True and refresh.get("dual_write_enabled") is True
    try:
        manual = json.loads(manual_path.read_text(encoding="utf-8")) if cutover else {}
        replay = json.loads(replay_path.read_text(encoding="utf-8")) if cutover else {}
    except (OSError, json.JSONDecodeError):
        manual, replay = {}, {}
    split_generation = str(refresh.get("split_generation_id") or "")
    migration_mode_valid = (
        refresh.get("production_source") == "canonical_manual_split_store"
        and manual_path.is_file()
        and replay_path.is_file()
        and sha256_path(manual_path) == refresh.get("manual_state_sha256")
        and sha256_path(replay_path) == refresh.get("replay_state_sha256")
        and bool(split_generation)
        and manual.get("generation_id") == split_generation
        and replay.get("generation_id") == split_generation
        and manual.get("classification") == "canonical_state"
        and replay.get("classification") == "derived_view"
    ) if cutover else (
        refresh.get("production_source") == "canonical_compatibility_registry"
        and refresh.get("dual_write_enabled") is False
        and refresh.get("cutover_enabled") is False
    )
    committed_at = str(refresh.get("committed_at") or "")
    try:
        committed_after_install = bool(marker) and _parse_iso(committed_at) > _parse_iso(str(marker.get("installed_at") or ""))
    except (ValueError, MigrationGateError):
        committed_after_install = False
    generation_basis = json.dumps(refresh.get("inputs") or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    checks = {
        "runtime_schedule_provenance": runtime.get("eligible_runtime") is True,
        "enforcement_marker_installed": marker.get("status") == "installed" and marker.get("enforcement_version") == ENFORCEMENT_VERSION,
        "enforcement_module_matches_installation": marker.get("policy_module_sha256") == sha256_path(Path(__file__).resolve()),
        "refresh_postdates_enforcement": committed_after_install,
        "completed_refresh_receipt": refresh.get("status") == "completed" and refresh.get("kind") == "dashboard_contiguous_refresh_receipt",
        "exact_source_generation": hashlib.sha256(generation_basis.encode("utf-8")).hexdigest() == refresh.get("source_generation_sha256"),
        "immutable_raw_source_receipt_bindings": _verify_bound_inputs(refresh.get("inputs") or {}),
        "registry_commit_hash": sha256_path(registry_path) == refresh.get("registry_after_sha256"),
        "h5_commit_hash": sha256_path(h5_snapshot_path) == refresh.get("h5_after_sha256"),
        "stage2_compare_receipt_hash": stage2_path.is_file() and sha256_path(stage2_path) == refresh.get("stage2_receipt_sha256"),
        "stage2_compare_passed": stage2.get("result") == "passed" and stage2.get("observation_id") == refresh.get("observation_id"),
        "stage2_bound_to_registry": (stage2.get("source_registry") or {}).get("sha256") == refresh.get("registry_after_sha256"),
        "canonical_production_source": migration_mode_valid,
        "commit_chain": refresh_commit_chain(refresh) == refresh.get("commit_chain_sha256"),
        "migration_mode_consistent": migration_mode_valid,
    }
    all_passed = all(checks.values())
    if not all_passed:
        return {
            "recorded": False,
            "eligible": False,
            "failure_reasons": sorted(key for key, passed in checks.items() if not passed),
            "runtime_failure_reasons": runtime.get("failure_reasons") or [],
        }

    receipt_id = hashlib.sha256(
        f"{ENFORCEMENT_VERSION}\x1f{sha256_path(refresh_receipt_path)}".encode("utf-8")
    ).hexdigest()[:24]
    live_path = gate_root / "live-refreshes" / f"registry-live-refresh-{target_date}-{receipt_id}.json"
    live_receipt = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "project_dashboard_registry_live_refresh_provenance_receipt",
        "status": "passed",
        "evidence_class": (
            "post_enforcement_authorized_scheduler_one_shot_production_refresh"
            if runtime.get("schedule_kind") == "authorized_one_shot"
            else "post_enforcement_real_0720_production_refresh"
        ),
        "enforcement_version": ENFORCEMENT_VERSION,
        "source_date": target_date,
        "process_started_at": runtime["process_started_at"],
        "observed_at": runtime["observed_at"],
        "refresh_receipt": refresh_receipt_path.resolve().as_posix(),
        "refresh_receipt_sha256": sha256_path(refresh_receipt_path),
        "source_generation_sha256": refresh.get("source_generation_sha256"),
        "registry_sha256": refresh.get("registry_after_sha256"),
        "h5_sha256": refresh.get("h5_after_sha256"),
        "stage2_receipt_sha256": refresh.get("stage2_receipt_sha256"),
        "runtime_provenance": runtime,
        "checks": {**checks, "all_required_checks_passed": True},
        "historical_replay_effect": "none",
        "stage3_authorized": cutover,
        "dual_write_enabled": cutover,
        "cutover_enabled": cutover,
    }
    if live_path.exists():
        existing = json.loads(live_path.read_text(encoding="utf-8"))
        if existing != live_receipt:
            raise MigrationGateError("live refresh provenance receipt conflict")
    else:
        _atomic_write(live_path, live_receipt)
    summary = publish_gate_summary(gate_root=gate_root, legacy_streak_path=legacy_streak_path)
    return {"recorded": True, "eligible": True, "receipt_path": live_path.as_posix(), "gate": summary}


__all__ = [
    "DEFAULT_GATE_ROOT", "DEFAULT_ONE_SHOT_AUTHORIZATION", "ENFORCEMENT_VERSION", "GATE_POLICY_ID",
    "LAUNCHD_LABEL", "ONE_SHOT_LAUNCHD_LABEL", "MigrationGateError",
    "build_gate_summary", "capture_scheduled_runtime_provenance", "install_enforcement_marker",
    "publish_gate_summary", "record_committed_scheduled_refresh", "refresh_commit_chain",
]
