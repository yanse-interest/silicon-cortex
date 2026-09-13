#!/usr/bin/env python3
"""Verify that a private Sites deployment is usable from the current network."""

from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request
from collections.abc import Mapping
from typing import Any


LOCAL_H5_FALLBACK = "http://mbp.local:8792/"
AUTH_ENV = "SITES_READBACK_BEARER_TOKEN"


def _headers_dict(headers: Mapping[str, str] | None) -> dict[str, str]:
    return {str(key).lower(): str(value) for key, value in (headers or {}).items()}


def _cf_colo(ray: str) -> str:
    suffix = ray.rsplit("-", 1)[-1].strip().upper() if "-" in ray else ""
    return suffix if suffix.isalnum() and len(suffix) <= 8 else ""


def classify_delivery(
    *,
    status: int,
    headers: Mapping[str, str] | None,
    body: bytes,
    expected_date: str,
    expected_generated_at: str,
) -> dict[str, Any]:
    normalized = _headers_dict(headers)
    server = normalized.get("server", "")
    cf_ray = normalized.get("cf-ray", "")
    text = body.decode("utf-8", errors="replace")
    common = {
        "http_status": status,
        "cf_ray": cf_ray,
        "cf_colo": _cf_colo(cf_ray),
        "local_h5_fallback": LOCAL_H5_FALLBACK,
    }
    if status == 200:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return {
                **common,
                "delivery_status": "delivery_marker_mismatch",
                "routing_status": "failed_medium",
                "reason_codes": ["delivery_response_invalid"],
                "recommended_next_effort": "none",
                "safe_to_retry": True,
            }
        actual_date = str(payload.get("daily_updated_through") or "")
        actual_generated_at = str(payload.get("generated_at") or "")
        markers_match = (
            actual_date == expected_date
            and actual_generated_at == expected_generated_at
        )
        if markers_match:
            return {
                **common,
                "delivery_status": "usable",
                "daily_updated_through": actual_date,
                "generated_at": actual_generated_at,
                "routing_status": "normal_complete",
                "reason_codes": [],
                "recommended_next_effort": "none",
                "safe_to_retry": True,
            }
        return {
            **common,
            "delivery_status": "delivery_marker_mismatch",
            "daily_updated_through": actual_date,
            "generated_at": actual_generated_at,
            "routing_status": "failed_medium",
            "reason_codes": ["delivery_marker_mismatch"],
            "recommended_next_effort": "none",
            "safe_to_retry": True,
        }

    blocked_text = "sorry, you have been blocked" in text.lower()
    if status == 403 and server.lower() == "cloudflare" and (cf_ray or blocked_text):
        return {
            **common,
            "delivery_status": "deployed_but_edge_blocked",
            "routing_status": "user_handoff",
            "reason_codes": ["cloudflare_edge_blocked"],
            "recommended_next_effort": "none",
            "safe_to_retry": True,
        }
    if status in {401, 403}:
        return {
            **common,
            "delivery_status": "private_auth_failed",
            "routing_status": "user_handoff",
            "reason_codes": ["auth_block"],
            "recommended_next_effort": "none",
            "safe_to_retry": False,
        }
    return {
        **common,
        "delivery_status": "delivery_check_failed",
        "routing_status": "failed_medium",
        "reason_codes": ["delivery_readback_failed"],
        "recommended_next_effort": "none",
        "safe_to_retry": True,
    }


def check_delivery(
    *,
    base_url: str,
    expected_date: str,
    expected_generated_at: str,
    bearer_token: str,
    timeout: float,
) -> dict[str, Any]:
    snapshot_url = f"{base_url.rstrip('/')}/dashboard-snapshot.json"
    request = urllib.request.Request(
        snapshot_url,
        headers={
            "Accept": "application/json",
            "OAI-Sites-Authorization": f"Bearer {bearer_token}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = int(response.status)
            headers = dict(response.headers.items())
            body = response.read()
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        headers = dict(exc.headers.items())
        body = exc.read()
    except (OSError, urllib.error.URLError) as exc:
        return {
            "delivery_status": "delivery_check_failed",
            "error_type": type(exc).__name__,
            "local_h5_fallback": LOCAL_H5_FALLBACK,
            "routing_status": "failed_medium",
            "reason_codes": ["delivery_readback_failed"],
            "recommended_next_effort": "none",
            "safe_to_retry": True,
        }
    result = classify_delivery(
        status=status,
        headers=headers,
        body=body,
        expected_date=expected_date,
        expected_generated_at=expected_generated_at,
    )
    result["readback_url"] = snapshot_url
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--expected-date", required=True)
    parser.add_argument("--expected-generated-at", required=True)
    parser.add_argument("--timeout", type=float, default=20.0)
    args = parser.parse_args()
    token = os.environ.get(AUTH_ENV, "")
    if not token:
        result = {
            "delivery_status": "private_auth_failed",
            "local_h5_fallback": LOCAL_H5_FALLBACK,
            "routing_status": "user_handoff",
            "reason_codes": ["auth_block"],
            "recommended_next_effort": "none",
            "safe_to_retry": False,
        }
    else:
        result = check_delivery(
            base_url=args.url,
            expected_date=args.expected_date,
            expected_generated_at=args.expected_generated_at,
            bearer_token=token,
            timeout=args.timeout,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["delivery_status"] == "usable" else 3


if __name__ == "__main__":
    raise SystemExit(main())
