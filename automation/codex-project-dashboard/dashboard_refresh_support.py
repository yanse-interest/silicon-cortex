#!/usr/bin/env python3
"""Shared account, source-gate, and snapshot support for dashboard refreshes."""

from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from dashboard_model import CaseRegistry
from compiled_source_contract import RECEIPT_REQUIRED_FROM, SourceContractError, load_compiled_source
from daily_deposition_receipt import receipt_path as deposition_receipt_path, source_deposition_state


PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = PROJECT_DIR / "dashboard-refresh.json"
ROUTERCTL = Path("/Users/shiba/bin/routerctl")
WIKI_ROOT = Path("/Users/shiba/Documents/Obsidian/Silicon Cortex/Codex Memory/wiki")
REGISTRY_PATH = WIKI_ROOT / "project-dashboard-case-registry.json"
DAILY_SOURCE_ROOT = WIKI_ROOT / "sources/conversations"
TZ = ZoneInfo("Asia/Shanghai")
SUPPORTED_EXECUTION_ACCOUNT_IDS = ("account-1", "account-2")


class DashboardRefreshError(RuntimeError):
    pass


def load_config(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    policy = data.get("account_policy")
    allowed = policy.get("allowed_account_ids") if isinstance(policy, dict) else None
    if (
        not isinstance(allowed, list)
        or any(not isinstance(account_id, str) for account_id in allowed)
        or tuple(allowed) != SUPPORTED_EXECUTION_ACCOUNT_IDS
        or policy.get("require_paused") is not True
        or policy.get("require_auto_switch_off") is not True
        or policy.get("require_automation_disabled") is not True
    ):
        raise DashboardRefreshError("Dashboard refresh account policy is incomplete")
    return data


def account_guard(policy: dict[str, Any]) -> dict[str, Any]:
    handoff = (
        "user_handoff: supported_execution_account_required; "
        "账号或 auto-off 状态无法确认，刷新已停止且不会切号、登录、绑定、回退或修复认证"
    )
    try:
        completed = subprocess.run(
            [str(ROUTERCTL), "status", "--json"],
            cwd=PROJECT_DIR,
            text=True,
            capture_output=True,
            timeout=20,
            check=False,
        )
        if completed.returncode != 0:
            raise DashboardRefreshError(handoff)
        status = json.loads(completed.stdout)
        if not isinstance(status, dict):
            raise DashboardRefreshError(handoff)
    except DashboardRefreshError:
        raise
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        raise DashboardRefreshError(handoff) from exc

    desktop = status.get("desktop")
    if not isinstance(desktop, dict):
        raise DashboardRefreshError(handoff)
    allowed = policy.get("allowed_account_ids") or []
    current_account_id = desktop.get("currentAccountId")
    accounts = status.get("accounts")
    selected_account = next(
        (
            account
            for account in accounts
            if isinstance(account, dict) and account.get("id") == current_account_id
        ),
        None,
    ) if isinstance(accounts, list) else None
    valid = (
        current_account_id in allowed
        and isinstance(selected_account, dict)
        and selected_account.get("loggedIn") is True
        and status.get("paused") is True
        and status.get("autoSwitchWhenSafe") is False
        and status.get("automationStatus") == "disabled"
    )
    if not valid:
        raise DashboardRefreshError(handoff)
    return {
        "account_id": current_account_id,
        "logged_in": True,
        "paused": True,
        "auto_switch": False,
        "automation_status": "disabled",
    }


def snapshot(*, source_through_date: str = "", read_only_projection: bool = False) -> dict[str, Any]:
    if not read_only_projection:
        return CaseRegistry(
            wiki_root=WIKI_ROOT,
            registry_path=REGISTRY_PATH,
            require_deposition_receipts=True,
            memory_root=WIKI_ROOT.parent,
            source_through_date=source_through_date,
        ).snapshot()

    # The split replay/compatibility projection may legitimately be newer than
    # the last committed H5 checkpoint (for example after a local preview or a
    # normal server refresh).  Build the older checkpoint from an isolated copy
    # of the already validated compatibility generation.  This preserves the
    # canonical manual/replay generation byte-for-byte while CaseRegistry
    # applies the requested source-through bound and refreshes only the copy.
    from registry_split_store import SplitStoreCoordinator

    coordinator = SplitStoreCoordinator(
        manual_path=REGISTRY_PATH.with_name("project-dashboard-manual-state.json"),
        replay_path=REGISTRY_PATH.with_name("project-dashboard-replay-state.json"),
        compatibility_path=REGISTRY_PATH,
    )
    canonical = coordinator.load()
    with tempfile.TemporaryDirectory(prefix="dashboard-historical-projection-", dir="/private/tmp") as temporary:
        projection_path = Path(temporary) / REGISTRY_PATH.name
        projection_path.write_text(json.dumps(canonical, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return CaseRegistry(
            wiki_root=WIKI_ROOT,
            registry_path=projection_path,
            reviews_root=WIKI_ROOT / "reviews",
            daily_root=WIKI_ROOT / "sources/conversations/chatgpt-daily",
            codex_daily_root=WIKI_ROOT / "sources/conversations/codex-daily",
            live_state_path=Path(temporary) / "disabled-live-state.json",
            require_deposition_receipts=True,
            memory_root=WIKI_ROOT.parent,
            source_through_date=source_through_date,
        ).snapshot()


def frontmatter_value(path: Path, key: str) -> str:
    if not path.exists():
        return ""
    for line in path.read_text(encoding="utf-8").splitlines()[:20]:
        if line.startswith(f"{key}:"):
            return line.split(":", 1)[1].strip()
    return ""


def daily_source_gate(
    target: str,
    *,
    source_root: Path = DAILY_SOURCE_ROOT,
) -> dict[str, Any]:
    """Validate one exact daily generation through the canonical source contract."""
    try:
        parsed = date.fromisoformat(target)
    except ValueError as exc:
        raise DashboardRefreshError("daily source target must be YYYY-MM-DD") from exc
    if parsed.isoformat() != target:
        raise DashboardRefreshError("daily source target must be canonical YYYY-MM-DD")
    year = target[:4]
    specs = {
        "chatgpt": source_root / "chatgpt-daily" / year / f"chatgpt-daily-report-{target}.md",
        "codex": source_root / "codex-daily" / year / f"codex-daily-report-{target}.md",
    }
    # ``source_root`` is injectable for tests and repair tooling; derive the
    # matching vault root instead of silently validating receipts in the live
    # vault when a staged source tree is supplied.
    memory_root = source_root.parents[2]
    receipt_required = target >= RECEIPT_REQUIRED_FROM
    missing: list[str] = []
    invalid: list[str] = []
    incomplete_deposition: list[str] = []
    inputs: dict[str, dict[str, Any]] = {}
    for family, path in specs.items():
        if not path.exists():
            missing.append(family)
            continue
        state = source_deposition_state(path, memory_root)
        if not state["ready"]:
            if state.get("reason") == "daily_deposition_receipt_missing":
                incomplete_deposition.append(family)
            else:
                invalid.append(family)
            continue
        try:
            compiled = load_compiled_source(
                path,
                memory_root=memory_root,
                expected_family=family,
                legacy_compatible=not receipt_required,
                require_structured=receipt_required,
                require_instrument=False,
            )
        except (OSError, SourceContractError):
            invalid.append(family)
            continue
        receipt = Path(str(state.get("receipt") or ""))
        if not receipt.is_file():
            historical_receipt = deposition_receipt_path(memory_root, f"{family}-daily", target)
            if historical_receipt.is_file():
                receipt = historical_receipt
        if compiled.source_date != target or (receipt_required and (compiled.raw_path is None or not receipt.is_file())):
            invalid.append(family)
            continue
        details = {
            "source_path": path.resolve().as_posix(),
            "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "candidate_count": int(state.get("candidate_count") or 0),
        }
        if compiled.raw_path is not None:
            details.update({
                "raw_path": compiled.raw_path.resolve().as_posix(),
                "raw_sha256": hashlib.sha256(compiled.raw_path.read_bytes()).hexdigest(),
            })
        if receipt.is_file():
            try:
                receipt_payload = json.loads(receipt.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                invalid.append(family)
                continue
            if (
                receipt_payload.get("status") != "completed"
                or receipt_payload.get("source_sha256") != details["source_sha256"]
                or (details.get("raw_sha256") and receipt_payload.get("raw_sha256") != details["raw_sha256"])
            ):
                invalid.append(family)
                continue
            details.update({
                "receipt_path": receipt.resolve().as_posix(),
                "receipt_sha256": hashlib.sha256(receipt.read_bytes()).hexdigest(),
            })
        inputs[family] = details
    ready = not missing and not invalid and not incomplete_deposition and len(inputs) == 2
    generation_basis = json.dumps(inputs, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        "ready": ready,
        "target_date": target,
        "missing_sources": missing,
        "invalid_sources": invalid,
        "incomplete_deposition": incomplete_deposition,
        "inputs": inputs,
        "source_generation_sha256": hashlib.sha256(generation_basis.encode("utf-8")).hexdigest() if ready else "",
    }


def previous_day_source_gate(
    *,
    now: datetime | None = None,
    source_root: Path = DAILY_SOURCE_ROOT,
) -> dict[str, Any]:
    current = now or datetime.now(TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=TZ)
    target = (current.astimezone(TZ).date() - timedelta(days=1)).isoformat()
    return daily_source_gate(target, source_root=source_root)
