"""Small immutable companion for the time an original daily report was captured."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path


def record(target: Path, *, result: str, raw: bytes, source_locator: str = "") -> dict[str, str | None]:
    companion = target.with_suffix(".capture.json")
    digest = hashlib.sha256(raw).hexdigest()
    if not companion.is_file() and result in {"created", "unchanged"}:
        observed_at = datetime.now(timezone(timedelta(hours=8))).isoformat()
        payload = {
            "version": 1,
            "raw_sha256": digest,
            "observed_at": observed_at,
            "observed_at_kind": "local_capture_clock_not_source_generation",
            "source_locator": source_locator or None,
        }
        encoded = (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
        temporary = None
        try:
            descriptor, temporary = tempfile.mkstemp(prefix=".capture-", dir=target.parent)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.link(temporary, companion)
        except FileExistsError:
            pass
        except OSError as exc:
            return {"capture_metadata": None, "observed_at": None, "provenance_warning": str(exc)}
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)
    try:
        payload = json.loads(companion.read_text(encoding="utf-8"))
        if payload.get("raw_sha256") != digest:
            raise ValueError("capture provenance does not match immutable raw")
        return {"capture_metadata": companion.as_posix(), "observed_at": payload.get("observed_at")}
    except (OSError, ValueError) as exc:
        return {"capture_metadata": None, "observed_at": None, "provenance_warning": str(exc)}
