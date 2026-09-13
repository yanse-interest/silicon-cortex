#!/usr/bin/env python3
"""Crash-consistent, contiguous production checkpoints for Dashboard/H5 refresh."""

from __future__ import annotations

import base64
import fcntl
import hashlib
import json
import os
import re
import tempfile
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterator
from zoneinfo import ZoneInfo

from registry_migration_gate import (
    ENFORCEMENT_VERSION,
    record_committed_scheduled_refresh,
    refresh_commit_chain,
)
from registry_split_store import SplitStoreCoordinator, SplitStoreError


TZ = ZoneInfo("Asia/Shanghai")
DEFAULT_TRANSACTION_ROOT = Path("/private/tmp/codex-project-dashboard-refresh-transaction")
DEFAULT_LOCK_PATH = Path("/private/tmp/codex-project-dashboard-refresh.lock")
STAGE2_RECEIPT_PATTERN = "project-dashboard-registry-shadow-compare-*.json"
DAILY_EVENT_KINDS = {"daily", "daily_instrument_knowledge", "codex_daily"}
DAILY_REPLAY_KINDS = DAILY_EVENT_KINDS | {"explicit_daily_candidate"}


class DashboardCatchupError(RuntimeError):
    def __init__(self, code: str, message: str, *, target_date: str = "", gate: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.target_date = target_date
        self.gate = gate or {}


def sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else ""


def atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    atomic_write_bytes(path, (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def _capture(path: Path) -> dict[str, Any]:
    return {
        "exists": path.is_file(),
        "bytes_b64": base64.b64encode(path.read_bytes()).decode("ascii") if path.is_file() else "",
    }


def _restore(path: Path, state: dict[str, Any]) -> None:
    if state.get("exists"):
        atomic_write_bytes(path, base64.b64decode(str(state.get("bytes_b64") or "")))
    elif path.exists():
        path.unlink()


@contextmanager
def exclusive_refresh_lock(path: Path = DEFAULT_LOCK_PATH) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


class RefreshTransaction:
    """Recover a date checkpoint to its exact pre-date mutable state after a crash."""

    def __init__(
        self,
        *,
        transaction_root: Path,
        registry_path: Path,
        output_path: Path,
        stage2_root: Path,
        refresh_receipt_root: Path,
        manual_state_path: Path | None = None,
        replay_state_path: Path | None = None,
    ) -> None:
        self.root = transaction_root
        self.journal = transaction_root / "journal.json"
        self.registry_path = registry_path
        self.output_path = output_path
        self.stage2_root = stage2_root
        self.refresh_receipt_root = refresh_receipt_root
        self.manual_state_path = manual_state_path or registry_path.with_name("project-dashboard-manual-state.json")
        self.replay_state_path = replay_state_path or registry_path.with_name("project-dashboard-replay-state.json")

    def recover(self) -> bool:
        if not self.journal.is_file():
            return False
        payload = json.loads(self.journal.read_text(encoding="utf-8"))
        if payload.get("version") != 1:
            raise DashboardCatchupError("invalid_transaction_journal", "unsupported Dashboard refresh transaction journal")
        if payload.get("phase") == "committed":
            self.journal.unlink()
            return False
        _restore(self.registry_path, payload["registry_before"])
        _restore(self.manual_state_path, payload.get("manual_state_before") or {"exists": False, "bytes_b64": ""})
        _restore(self.replay_state_path, payload.get("replay_state_before") or {"exists": False, "bytes_b64": ""})
        _restore(self.output_path, payload["output_before"])
        _restore(self.stage2_root / "stage-2-streak.json", payload["streak_before"])
        before_receipts = set(payload.get("stage2_receipts_before") or [])
        for path in self.stage2_root.glob(STAGE2_RECEIPT_PATTERN):
            if path.name not in before_receipts:
                path.unlink()
        receipt_name = str(payload.get("refresh_receipt_name") or "")
        if not re.fullmatch(r"dashboard-refresh-\d{4}-\d{2}-\d{2}-[0-9a-f]{16}\.json", receipt_name):
            raise DashboardCatchupError("invalid_transaction_journal", "invalid refresh receipt name in transaction journal")
        _restore(self.refresh_receipt_root / receipt_name, payload["refresh_receipt_before"])
        self.journal.unlink()
        return True

    def begin(self, *, target_date: str, refresh_receipt_name: str) -> None:
        if self.journal.exists():
            raise DashboardCatchupError("transaction_already_active", "Dashboard refresh transaction is already active")
        self.root.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "phase": "prepared",
            "source_date": target_date,
            "registry_before": _capture(self.registry_path),
            "manual_state_before": _capture(self.manual_state_path),
            "replay_state_before": _capture(self.replay_state_path),
            "output_before": _capture(self.output_path),
            "streak_before": _capture(self.stage2_root / "stage-2-streak.json"),
            "stage2_receipts_before": sorted(path.name for path in self.stage2_root.glob(STAGE2_RECEIPT_PATTERN)),
            "refresh_receipt_name": refresh_receipt_name,
            "refresh_receipt_before": _capture(self.refresh_receipt_root / refresh_receipt_name),
        }
        atomic_write_json(self.journal, payload)

    def rollback(self) -> None:
        self.recover()

    def commit(self) -> None:
        payload = json.loads(self.journal.read_text(encoding="utf-8"))
        payload["phase"] = "committed"
        atomic_write_json(self.journal, payload)
        self.journal.unlink()


def _parse_h5_date(output_path: Path) -> date:
    if not output_path.is_file():
        raise DashboardCatchupError("validated_h5_missing", "last validated H5 snapshot is missing")
    try:
        payload = json.loads(output_path.read_text(encoding="utf-8"))
        value = str(payload.get("daily_updated_through") or "")
        parsed = date.fromisoformat(value)
    except (OSError, json.JSONDecodeError, AttributeError, ValueError) as exc:
        raise DashboardCatchupError("validated_h5_invalid", "last validated H5 daily_updated_through is invalid") from exc
    if parsed.isoformat() != value:
        raise DashboardCatchupError("validated_h5_invalid", "last validated H5 daily_updated_through is not canonical")
    return parsed


def derive_contiguous_dates(*, output_path: Path, now: datetime | None = None) -> list[str]:
    current = now or datetime.now(TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=TZ)
    end = current.astimezone(TZ).date() - timedelta(days=1)
    last_validated = _parse_h5_date(output_path)
    if last_validated > end:
        raise DashboardCatchupError("validated_h5_in_future", "last validated H5 is later than yesterday Asia/Shanghai")
    result: list[str] = []
    cursor = last_validated + timedelta(days=1)
    while cursor <= end:
        result.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return result


def registry_daily_max_date(registry_path: Path) -> str:
    if not registry_path.is_file():
        return ""
    payload = json.loads(registry_path.read_text(encoding="utf-8"))
    dates = [
        str(event.get("date") or "")
        for event in payload.get("events") or []
        if isinstance(event, dict) and str(event.get("source_kind") or "") in DAILY_EVENT_KINDS
    ]
    return max((value for value in dates if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)), default="")


def _registry_daily_dates(registry: dict[str, Any]) -> list[str]:
    records = [
        *(registry.get("events") or []),
        *(registry.get("value_candidates") or []),
        *[
            value
            for case in registry.get("cases") or []
            if isinstance(case, dict)
            for value in case.get("value_items") or []
        ],
    ]
    values: set[str] = set()
    for record in records:
        if not isinstance(record, dict):
            raise DashboardCatchupError("canonical_registry_invalid", "canonical Dashboard replay collection is malformed")
        if str(record.get("source_kind") or "") not in DAILY_REPLAY_KINDS:
            continue
        value = str(record.get("date") or record.get("period") or "")
        try:
            parsed = date.fromisoformat(value)
        except ValueError as exc:
            raise DashboardCatchupError(
                "canonical_registry_invalid",
                "canonical Dashboard replay evidence has an invalid source date",
            ) from exc
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) or parsed.isoformat() != value:
            raise DashboardCatchupError(
                "canonical_registry_invalid",
                "canonical Dashboard replay evidence has a non-canonical source date",
            )
        values.add(value)
    return sorted(values)


def _validated_registry_state(registry_path: Path) -> tuple[dict[str, Any], bool]:
    """Load compatibility state, validating Stage-4 split authority when present."""
    manual_path = registry_path.with_name("project-dashboard-manual-state.json")
    replay_path = registry_path.with_name("project-dashboard-replay-state.json")
    present = (manual_path.is_file(), replay_path.is_file())
    try:
        if any(present):
            if not all(present):
                raise SplitStoreError("partial canonical split store")
            coordinator = SplitStoreCoordinator(
                manual_path=manual_path,
                replay_path=replay_path,
                compatibility_path=registry_path,
            )
            return coordinator.load(), True
        return json.loads(registry_path.read_text(encoding="utf-8")), False
    except (OSError, json.JSONDecodeError, SplitStoreError) as exc:
        raise DashboardCatchupError(
            "canonical_registry_invalid",
            f"canonical Dashboard registry validation failed: {exc}",
        ) from exc


def _calendar_dates(start: str, end: str) -> list[str]:
    cursor = date.fromisoformat(start)
    final = date.fromisoformat(end)
    values: list[str] = []
    while cursor <= final:
        values.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return values


def _streak_count(path: Path) -> int:
    if not path.is_file():
        return 0
    return int(json.loads(path.read_text(encoding="utf-8")).get("consecutive_eligible_successes") or 0)


def _write_refresh_receipt(path: Path, payload: dict[str, Any]) -> bool:
    if path.is_file():
        existing = json.loads(path.read_text(encoding="utf-8"))
        stable_keys = ("status", "source_date", "source_generation_sha256", "observation_id", "registry_after_sha256")
        if any(existing.get(key) != payload.get(key) for key in stable_keys):
            raise DashboardCatchupError("refresh_receipt_conflict", "existing Dashboard refresh receipt conflicts with this source generation")
        return False
    atomic_write_json(path, payload)
    return True


def run_contiguous_catchup(
    *,
    output_path: Path,
    registry_path: Path,
    wiki_root: Path,
    memory_root: Path,
    source_root: Path,
    stage1_receipt_path: Path,
    stage2_root: Path,
    refresh_receipt_root: Path,
    h5_builder: Callable[..., dict[str, Any]],
    source_gate: Callable[[str], dict[str, Any]],
    snapshot_fn: Callable[..., dict[str, Any]],
    shadow_compare_fn: Callable[..., dict[str, Any]],
    atomic_h5_writer: Callable[[Path, dict[str, Any]], None],
    now: datetime | None = None,
    transaction_root: Path = DEFAULT_TRANSACTION_ROOT,
    lock_path: Path = DEFAULT_LOCK_PATH,
) -> dict[str, Any]:
    """Apply every derived missing date in order, with one recoverable commit per date."""
    transaction = RefreshTransaction(
        transaction_root=transaction_root,
        registry_path=registry_path,
        output_path=output_path,
        stage2_root=stage2_root,
        refresh_receipt_root=refresh_receipt_root,
    )
    with exclusive_refresh_lock(lock_path):
        recovered_incomplete_transaction = transaction.recover()
        dates = derive_contiguous_dates(output_path=output_path, now=now)
        if not dates:
            return {
                "ok": True,
                "skipped": True,
                "routing_status": "idempotent_complete",
                "recovered_incomplete_transaction": recovered_incomplete_transaction,
                "committed_dates": [],
                "daily_updated_through": _parse_h5_date(output_path).isoformat(),
            }
        canonical_registry, split_cutover = _validated_registry_state(registry_path)
        registry_dates = _registry_daily_dates(canonical_registry)
        registry_max = registry_dates[-1] if registry_dates else ""
        if registry_max and registry_max > dates[-1]:
            raise DashboardCatchupError(
                "registry_ahead_of_target_window",
                f"canonical registry contains daily evidence after target window {dates[-1]}",
                target_date=dates[-1],
            )
        gate_cache: dict[str, dict[str, Any]] = {}
        if registry_max and registry_max > dates[0]:
            if not split_cutover:
                raise DashboardCatchupError(
                    "registry_ahead_of_checkpoint",
                    f"legacy canonical registry contains daily evidence after checkpoint {dates[0]}",
                    target_date=dates[0],
                )
            # Validate the complete interval before the first H5 mutation.  An
            # ahead replay is acceptable only when every intervening two-source
            # generation has its valid deposition evidence; gaps and invalid or
            # unreceipted future evidence fail closed.
            for candidate in _calendar_dates(dates[0], registry_max):
                candidate_gate = source_gate(candidate)
                gate_cache[candidate] = candidate_gate
                if not candidate_gate.get("ready"):
                    raise DashboardCatchupError(
                        "registry_ahead_source_gate_failed",
                        f"ahead split-store evidence is not eligible at {candidate}",
                        target_date=candidate,
                        gate=candidate_gate,
                    )
        committed: list[dict[str, Any]] = []
        for target in dates:
            gate = gate_cache.get(target) or source_gate(target)
            if not gate.get("ready"):
                raise DashboardCatchupError(
                    "contiguous_source_gate_failed",
                    f"contiguous Dashboard refresh stopped at {target}",
                    target_date=target,
                    gate=gate,
                )
            canonical_registry, current_split_cutover = _validated_registry_state(registry_path)
            registry_dates = _registry_daily_dates(canonical_registry)
            registry_max = registry_dates[-1] if registry_dates else ""
            read_only_projection = bool(registry_max and registry_max > target)
            if read_only_projection and not current_split_cutover:
                raise DashboardCatchupError(
                    "registry_ahead_of_checkpoint",
                    f"legacy canonical registry contains daily evidence after checkpoint {target}",
                    target_date=target,
                    gate=gate,
                )
            generation = str(gate["source_generation_sha256"])
            observation_id = f"daily-refresh:{target}:{generation[:16]}"
            receipt_name = f"dashboard-refresh-{target}-{generation[:16]}.json"
            receipt_path = refresh_receipt_root / receipt_name
            transaction.begin(target_date=target, refresh_receipt_name=receipt_name)
            registry_before = sha256_path(registry_path)
            h5_before = sha256_path(output_path)
            split_before = {
                "manual": sha256_path(registry_path.with_name("project-dashboard-manual-state.json")),
                "replay": sha256_path(registry_path.with_name("project-dashboard-replay-state.json")),
                "compatibility": registry_before,
            }
            streak_before = _streak_count(stage2_root / "stage-2-streak.json")
            try:
                canonical_snapshot = snapshot_fn(
                    source_through_date=target,
                    read_only_projection=read_only_projection,
                )
                payload = h5_builder(canonical_snapshot, synced_through=target)
                shadow = shadow_compare_fn(
                    registry_path=registry_path,
                    wiki_root=wiki_root,
                    memory_root=memory_root,
                    stage1_receipt_path=stage1_receipt_path,
                    output_root=stage2_root,
                    h5_snapshot_path=output_path,
                    h5_builder=h5_builder,
                    synced_through=target,
                    observation_id=observation_id,
                    guard_paths={
                        "raw": memory_root / "raw",
                        "sources": wiki_root / "sources",
                        "daily_receipts": wiki_root / "review-cycles/daily-deposition",
                        "promotion_ledger": wiki_root / "review-cycles/promotion-ledger.json",
                    },
                )
                atomic_h5_writer(output_path, payload)
                stage2_receipt = Path(str(shadow.get("receipt_path") or ""))
                streak_after = _streak_count(stage2_root / "stage-2-streak.json")
                committed_at = datetime.now(TZ).isoformat()
                manual_state_path = registry_path.with_name("project-dashboard-manual-state.json")
                replay_state_path = registry_path.with_name("project-dashboard-replay-state.json")
                split_cutover = manual_state_path.is_file() and replay_state_path.is_file()
                split_generation_id = ""
                if split_cutover:
                    manual_state = json.loads(manual_state_path.read_text(encoding="utf-8"))
                    replay_state = json.loads(replay_state_path.read_text(encoding="utf-8"))
                    if manual_state.get("generation_id") != replay_state.get("generation_id"):
                        raise DashboardCatchupError("split_generation_mismatch", "canonical split stores do not share one generation")
                    split_generation_id = str(manual_state.get("generation_id") or "")
                if read_only_projection:
                    split_after = {
                        "manual": sha256_path(manual_state_path),
                        "replay": sha256_path(replay_state_path),
                        "compatibility": sha256_path(registry_path),
                    }
                    if split_after != split_before:
                        raise DashboardCatchupError(
                            "historical_projection_mutated_canonical_state",
                            "read-only historical H5 projection changed canonical split-store state",
                            target_date=target,
                            gate=gate,
                        )
                refresh_receipt = {
                    "schema_version": 2,
                    "kind": "dashboard_contiguous_refresh_receipt",
                    "status": "completed",
                    "source_date": target,
                    "source_generation_sha256": generation,
                    "observation_id": observation_id,
                    "inputs": gate.get("inputs") or {},
                    "registry_before_sha256": registry_before,
                    "registry_after_sha256": sha256_path(registry_path),
                    "h5_before_sha256": h5_before,
                    "h5_after_sha256": sha256_path(output_path),
                    "stage2_receipt": stage2_receipt.resolve().as_posix() if stage2_receipt.is_file() else "",
                    "stage2_receipt_sha256": sha256_path(stage2_receipt),
                    "legacy_stage2_streak_before": streak_before,
                    "legacy_stage2_streak_after": streak_after,
                    "legacy_stage2_streak_action": "frozen_no_change",
                    "production_source": "canonical_manual_split_store" if split_cutover else "canonical_compatibility_registry",
                    "manual_state_sha256": sha256_path(manual_state_path) if split_cutover else "",
                    "replay_state_sha256": sha256_path(replay_state_path) if split_cutover else "",
                    "split_generation_id": split_generation_id,
                    "provenance_enforcement_version": ENFORCEMENT_VERSION,
                    "dual_write_enabled": split_cutover,
                    "cutover_enabled": split_cutover,
                    "historical_projection": read_only_projection,
                    "validated_registry_ahead_through": registry_max if read_only_projection else "",
                    "committed_at": committed_at,
                }
                refresh_receipt["commit_chain_sha256"] = refresh_commit_chain(refresh_receipt)
                refresh_receipt_root.mkdir(parents=True, exist_ok=True)
                _write_refresh_receipt(receipt_path, refresh_receipt)
                transaction.commit()
                try:
                    gate_observation = record_committed_scheduled_refresh(
                        refresh_receipt_path=receipt_path,
                        registry_path=registry_path,
                        h5_snapshot_path=output_path,
                        gate_root=wiki_root / "review-cycles/registry-migration-gate",
                        legacy_streak_path=stage2_root / "stage-2-streak.json",
                    )
                except Exception as exc:
                    # Production is already atomically committed.  Gate evidence
                    # fails closed without pretending the production checkpoint
                    # was rolled back or accepted.
                    gate_observation = {
                        "recorded": False,
                        "eligible": False,
                        "failure_reasons": [f"gate_recording_error:{type(exc).__name__}"],
                    }
                committed.append({
                    "source_date": target,
                    "source_generation_sha256": generation,
                    "observation_id": observation_id,
                    "refresh_receipt": receipt_path.resolve().as_posix(),
                    "registry_sha256": refresh_receipt["registry_after_sha256"],
                    "h5_sha256": refresh_receipt["h5_after_sha256"],
                    "stage2_receipt": refresh_receipt["stage2_receipt"],
                    "legacy_stage2_streak_before": streak_before,
                    "legacy_stage2_streak_after": streak_after,
                    "legacy_stage2_streak_action": refresh_receipt["legacy_stage2_streak_action"],
                    "migration_gate_observation": gate_observation,
                    "case_count": int(payload.get("summary", {}).get("case_count") or 0),
                    "value_item_count": int(payload.get("summary", {}).get("value_item_count") or 0),
                })
            except Exception:
                transaction.rollback()
                raise
        return {
            "ok": True,
            "skipped": False,
            "routing_status": "supported_execution_account",
            "recovered_incomplete_transaction": recovered_incomplete_transaction,
            "committed_dates": committed,
            "daily_updated_through": dates[-1],
            "output": output_path.resolve().as_posix(),
        }
