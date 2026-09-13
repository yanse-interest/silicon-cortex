#!/usr/bin/env python3
"""Verify that the running local H5 service loaded the latest built snapshot."""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from pathlib import Path
from typing import Any


DEFAULT_SNAPSHOT = (
    Path(__file__).resolve().parent.parent
    / "codex-project-dashboard-h5"
    / "public"
    / "dashboard-snapshot.json"
)
DEFAULT_URLS = ["http://127.0.0.1:8792", "http://mbp.local:8792"]


def _header(headers: Mapping[str, str] | None, name: str) -> str:
    values = [str(value) for key, value in (headers or {}).items() if str(key).lower() == name.lower()]
    return ", ".join(values)


def _topics(snapshot: dict[str, Any]) -> list[str]:
    return sorted({
        str(item.get("topic") or "").strip()
        for domain in snapshot.get("capability_domains") or []
        for item in domain.get("items") or []
        if str(item.get("topic") or "").strip()
    })


def classify_local_delivery(
    *,
    expected: dict[str, Any],
    root_status: int,
    root_headers: Mapping[str, str] | None,
    root_body: bytes,
    snapshot_status: int,
    snapshot_headers: Mapping[str, str] | None,
    snapshot_body: bytes,
) -> dict[str, Any]:
    common = {
        "expected_date": str(expected.get("daily_updated_through") or ""),
        "expected_generated_at": str(expected.get("generated_at") or ""),
        "root_http_status": root_status,
        "snapshot_http_status": snapshot_status,
    }
    if root_status != 200 or snapshot_status != 200:
        return {
            **common,
            "local_delivery_status": "local_service_unavailable",
            "routing_status": "failed_medium",
            "reason_codes": ["local_h5_unavailable"],
            "recommended_next_effort": "none",
            "safe_to_retry": True,
        }
    try:
        served = json.loads(snapshot_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {
            **common,
            "local_delivery_status": "local_snapshot_invalid",
            "routing_status": "failed_medium",
            "reason_codes": ["local_h5_snapshot_invalid"],
            "recommended_next_effort": "none",
            "safe_to_retry": True,
        }
    root_text = root_body.decode("utf-8", errors="replace")
    topics = _topics(expected)
    markers_present = (
        str(expected.get("generated_at") or "") in root_text
        and str(expected.get("daily_updated_through") or "") in root_text
        and all(topic in root_text for topic in topics)
    )
    exact_snapshot = served == expected
    root_no_store = "no-store" in _header(root_headers, "cache-control").lower()
    snapshot_no_store = "no-store" in _header(snapshot_headers, "cache-control").lower()
    if exact_snapshot and markers_present and root_no_store and snapshot_no_store:
        return {
            **common,
            "local_delivery_status": "usable",
            "topic_count": len(topics),
            "routing_status": "normal_complete",
            "reason_codes": [],
            "recommended_next_effort": "none",
            "safe_to_retry": True,
        }
    reasons: list[str] = []
    if not exact_snapshot:
        reasons.append("local_snapshot_mismatch")
    if not markers_present:
        reasons.append("local_server_bundle_stale")
    if not root_no_store or not snapshot_no_store:
        reasons.append("local_h5_cache_policy_invalid")
    return {
        **common,
        "local_delivery_status": "stale_or_cacheable",
        "snapshot_matches": exact_snapshot,
        "rendered_markers_match": markers_present,
        "root_no_store": root_no_store,
        "snapshot_no_store": snapshot_no_store,
        "routing_status": "failed_medium",
        "reason_codes": reasons,
        "recommended_next_effort": "none",
        "safe_to_retry": True,
    }


def _get(url: str, timeout: float) -> tuple[int, dict[str, str], bytes]:
    request = urllib.request.Request(url, headers={"Accept": "text/html,application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return int(response.status), dict(response.headers.items()), response.read()


def check_url(
    base_url: str,
    expected: dict[str, Any],
    timeout: float,
    attempts: int,
    retry_delay: float,
) -> dict[str, Any]:
    for attempt in range(1, attempts + 1):
        try:
            root_status, root_headers, root_body = _get(f"{base_url.rstrip('/')}/", timeout)
            snapshot_status, snapshot_headers, snapshot_body = _get(
                f"{base_url.rstrip('/')}/dashboard-snapshot.json", timeout
            )
            break
        except (OSError, urllib.error.URLError, urllib.error.HTTPError) as exc:
            if attempt == attempts:
                return {
                    "url": base_url,
                    "attempts": attempt,
                    "local_delivery_status": "local_service_unavailable",
                    "error_type": type(exc).__name__,
                    "routing_status": "failed_medium",
                    "reason_codes": ["local_h5_unavailable"],
                    "recommended_next_effort": "none",
                    "safe_to_retry": True,
                }
            time.sleep(retry_delay)
    result = classify_local_delivery(
        expected=expected,
        root_status=root_status,
        root_headers=root_headers,
        root_body=root_body,
        snapshot_status=snapshot_status,
        snapshot_headers=snapshot_headers,
        snapshot_body=snapshot_body,
    )
    result["url"] = base_url
    result["attempts"] = attempt
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument("--url", action="append", dest="urls")
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--attempts", type=int, default=5)
    parser.add_argument("--retry-delay", type=float, default=1.0)
    args = parser.parse_args()
    if args.attempts < 1 or args.retry_delay < 0:
        parser.error("--attempts must be positive and --retry-delay cannot be negative")
    expected = json.loads(args.snapshot.read_text(encoding="utf-8"))
    checks = [
        check_url(url, expected, args.timeout, args.attempts, args.retry_delay)
        for url in (args.urls or DEFAULT_URLS)
    ]
    usable = all(check.get("local_delivery_status") == "usable" for check in checks)
    payload = {
        "ok": usable,
        "local_delivery_status": "usable" if usable else "failed",
        "checks": checks,
        "routing_status": "normal_complete" if usable else "failed_medium",
        "reason_codes": [] if usable else sorted({code for check in checks for code in check.get("reason_codes") or []}),
        "recommended_next_effort": "none",
        "safe_to_retry": True,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if usable else 3


if __name__ == "__main__":
    raise SystemExit(main())
