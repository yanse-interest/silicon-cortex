#!/usr/bin/env python3
"""Finalize source-grounded daily candidates before dashboard refresh.

The automation agent performs the semantic extraction.  This script makes that
stage explicit and auditable: ``prepare`` inventories every report unit, while
``finalize`` requires a completed extraction manifest, grounds every cited
excerpt in the immutable raw report or compiled source summary, writes the
canonical Structured Candidates block, runs the deterministic deposition
pipeline, and only then publishes a compact completion receipt.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
PIPELINE_PATH = SCRIPT_DIR / "deposition_pipeline.py"
SPEC = importlib.util.spec_from_file_location("daily_deposition_pipeline", PIPELINE_PATH)
PIPELINE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(PIPELINE)
CONTRACT_PATH = SCRIPT_DIR / "compiled_source_contract.py"
CONTRACT_SPEC = importlib.util.spec_from_file_location("daily_compiled_source_contract", CONTRACT_PATH)
CONTRACT = importlib.util.module_from_spec(CONTRACT_SPEC)
assert CONTRACT_SPEC.loader
CONTRACT_SPEC.loader.exec_module(CONTRACT)

MANIFEST_VERSION = 1
RECEIPT_VERSION = 1
RESULTS = {"candidates", "no_relevant_content"}
PRIVACY_MODES = {"non_reconstructable", "compliance_abstracted"}
SECRET_PATTERNS = (
    re.compile(r"(?i)\b(?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|password|cookie|authorization)\b\s*[:=]"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"/Users/[^\s`]+"),
    re.compile(r"https?://(?:www\.)?chatgpt\.com/c/", re.I),
)
EXPERIMENTAL_IDENTIFIER_PATTERNS = (
    re.compile(r"(?i)\b(?:customer|client|sample|batch|project)[:_/#-]+[A-Za-z0-9][A-Za-z0-9._-]*"),
    re.compile(r"(?:客户|样品|批次|项目)(?:编号|ID|代码|代号)[：:\s_-]*[A-Za-z0-9][A-Za-z0-9._-]*"),
)


class DailyDepositionError(ValueError):
    pass


def sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic_write_text(path: Path, text: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return "unchanged"
    existed = path.exists()
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise
    return "updated" if existed else "created"


def atomic_write_json(path: Path, payload: dict[str, Any]) -> str:
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    return atomic_write_text(path, text)


def read_source(source_path: Path, memory_root: Path) -> dict[str, Any]:
    try:
        parsed = CONTRACT.load_compiled_source(source_path, memory_root=memory_root)
    except CONTRACT.SourceContractError as exc:
        raise DailyDepositionError(str(exc)) from exc
    return {
        "text": parsed.text,
        "body": parsed.body,
        "frontmatter": parsed.frontmatter,
        "date": parsed.source_date,
        "family": parsed.family,
        "prefix": CONTRACT.FAMILY_SPECS[parsed.family].unit_prefix,
        "raw_path": parsed.raw_path,
        "raw_text": parsed.raw_text,
        "units": parsed.unit_ids,
        "structured_candidates": parsed.structured_candidates,
        "instrument_candidates": parsed.instrument_candidates,
        "contract_version": parsed.version,
    }


def prepare_manifest(source_path: Path, memory_root: Path, output: Path) -> dict[str, Any]:
    source = read_source(source_path, memory_root)
    manifest = {
        "version": MANIFEST_VERSION,
        "source_path": source_path.resolve().as_posix(),
        "source_sha256": sha256_path(source_path),
        "raw_sha256": sha256_path(source["raw_path"]),
        "source_type": source["frontmatter"]["type"],
        "source_date": source["date"],
        "unit_ids": source["units"],
        "reviewed_units": [],
        "result": "pending",
        "candidates": [],
    }
    write_result = atomic_write_json(output, manifest)
    return {
        "ok": True,
        "stage": "candidate_extraction_required",
        "manifest": output.as_posix(),
        "unit_count": len(source["units"]),
        "write": write_result,
    }


def normalize_excerpt(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def validate_private_text(value: str, *, field: str) -> None:
    if any(pattern.search(value) for pattern in SECRET_PATTERNS):
        raise DailyDepositionError(f"{field} contains prohibited secret, path, or private conversation locator")
    if any(pattern.search(value) for pattern in EXPERIMENTAL_IDENTIFIER_PATTERNS):
        raise DailyDepositionError(f"{field} contains a reconstructable experimental identifier")


def ground_candidates(
    manifest: dict[str, Any], source: dict[str, Any], source_path: Path
) -> list[dict[str, Any]]:
    if manifest.get("version") != MANIFEST_VERSION:
        raise DailyDepositionError("unsupported extraction manifest version")
    if manifest.get("source_path") != source_path.resolve().as_posix():
        raise DailyDepositionError("extraction manifest source_path mismatch")
    if manifest.get("source_sha256") != sha256_path(source_path):
        raise DailyDepositionError("source changed after candidate extraction was prepared")
    if manifest.get("raw_sha256") != sha256_path(source["raw_path"]):
        raise DailyDepositionError("immutable raw changed after candidate extraction was prepared")
    if manifest.get("source_type") != source["frontmatter"].get("type") or manifest.get("source_date") != source["date"]:
        raise DailyDepositionError("extraction manifest source metadata mismatch")
    unit_ids = manifest.get("unit_ids")
    reviewed = manifest.get("reviewed_units")
    if unit_ids != source["units"] or not isinstance(reviewed, list) or sorted(set(reviewed)) != source["units"]:
        raise DailyDepositionError("candidate extraction must review every formal Sxx/Txx unit")
    result = manifest.get("result")
    if result not in RESULTS:
        raise DailyDepositionError("candidate extraction result must be candidates or no_relevant_content")
    candidates = manifest.get("candidates")
    if not isinstance(candidates, list) or not all(isinstance(item, dict) for item in candidates):
        raise DailyDepositionError("candidate extraction candidates must be an array of objects")
    if result == "no_relevant_content" and candidates:
        raise DailyDepositionError("no_relevant_content extraction cannot contain candidates")
    if result == "candidates" and not candidates:
        raise DailyDepositionError("candidates extraction result requires at least one candidate")

    corpus = normalize_excerpt(source["raw_text"] + "\n" + source["text"])
    grounded: list[dict[str, Any]] = []
    for raw_candidate in candidates:
        candidate = dict(raw_candidate)
        candidate["source_date"] = source["date"]
        candidate["coverage"] = candidate.get("coverage") or source["frontmatter"].get("coverage", "unknown")
        candidate["candidate_id"] = PIPELINE.stable_candidate_id(
            str(candidate.get("type") or ""),
            str(candidate.get("domain") or ""),
            str(candidate.get("normalized_claim") or ""),
        )
        for field in ("domain", "normalized_claim", "evidence_boundary", "adoption_evidence", "action"):
            value = str(candidate.get(field) or "")
            if value:
                validate_private_text(value, field=f"candidate {candidate['candidate_id']} {field}")
        try:
            candidate = CONTRACT.validate_structured_candidate_evidence(
                candidate,
                unit_ids=source["units"],
                corpus=corpus,
                validate_text=validate_private_text,
            )
        except CONTRACT.SourceContractError as exc:
            raise DailyDepositionError(str(exc)) from exc
        grounded.append(
            PIPELINE.validate_candidate(
                candidate,
                source_date=source["date"],
                source_coverage=source["frontmatter"].get("coverage", "unknown"),
                source_path=source_path.as_posix(),
            )
        )
    candidate_ids = [item["candidate_id"] for item in grounded]
    if len(candidate_ids) != len(set(candidate_ids)):
        raise DailyDepositionError("candidate extraction contains duplicate stable candidate IDs")
    return grounded


def render_structured_candidates(text: str, candidates: list[dict[str, Any]]) -> str:
    payload = json.dumps(candidates, ensure_ascii=False, indent=2, sort_keys=True)
    section = f"## Structured Candidates\n\n```json\n{payload}\n```"
    pattern = re.compile(
        r"^## Structured Candidates\s*\n.*?(?=^##\s+|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    if pattern.search(text):
        return pattern.sub(section + "\n\n", text, count=1).rstrip() + "\n"
    return text.rstrip() + "\n\n" + section + "\n"


def receipt_path(memory_root: Path, family: str, source_date: str) -> Path:
    return CONTRACT.receipt_path(memory_root, family, source_date)


def finalize(
    source_path: Path,
    extraction_path: Path,
    memory_root: Path,
    cognitive_root: Path,
) -> dict[str, Any]:
    source = read_source(source_path, memory_root)
    manifest = json.loads(extraction_path.read_text(encoding="utf-8"))
    candidates = ground_candidates(manifest, source, source_path)
    updated_text = render_structured_candidates(source["text"], candidates)
    source_write = atomic_write_text(source_path, updated_text)
    source_after_hash = sha256_path(source_path)
    receipt = receipt_path(memory_root, source["family"], source["date"])
    if receipt.exists():
        current = json.loads(receipt.read_text(encoding="utf-8"))
        if not isinstance(current, dict):
            current = {}
        candidate_ids = sorted(item["candidate_id"] for item in candidates)
        if (
            current.get("version") == RECEIPT_VERSION
            and current.get("source_contract_version", CONTRACT.CONTRACT_VERSION) == CONTRACT.CONTRACT_VERSION
            and current.get("source_family") == source["family"]
            and current.get("source_date") == source["date"]
            and current.get("source_path") == source_path.resolve().as_posix()
            and current.get("source_sha256") == source_after_hash
            and current.get("raw_sha256") == sha256_path(source["raw_path"])
            and current.get("extraction_result") == manifest["result"]
            and current.get("reviewed_unit_count") == len(source["units"])
            and current.get("candidate_count") == len(candidates)
            and current.get("candidate_ids") == candidate_ids
            and current.get("raw_files_modified") == 0
            and current.get("actions_dispatched") == 0
            and current.get("status") == "completed"
        ):
            return {
                "ok": True,
                "status": "idempotent_complete",
                "source_write": source_write,
                "candidate_count": len(candidates),
                "receipt": receipt.as_posix(),
                "receipt_write": "unchanged",
                "raw_files_modified": 0,
                "actions_dispatched": 0,
            }

    # This call validates the final canonical source again and writes the common
    # ledger/promotions before the receipt can make the source dashboard-visible.
    deposition = PIPELINE.process_daily_source(
        source_path,
        memory_root,
        cognitive_root,
        dry_run=False,
    )
    if not deposition.get("ok") or deposition.get("raw_files_modified") != 0 or deposition.get("actions_dispatched") != 0:
        raise DailyDepositionError("daily deposition failed its immutable/action safety contract")
    payload = {
        "version": RECEIPT_VERSION,
        "source_contract_version": CONTRACT.CONTRACT_VERSION,
        "status": "completed",
        "source_family": source["family"],
        "source_date": source["date"],
        "source_path": source_path.resolve().as_posix(),
        "source_sha256": source_after_hash,
        "raw_sha256": sha256_path(source["raw_path"]),
        "extraction_result": manifest["result"],
        "reviewed_unit_count": len(source["units"]),
        "candidate_count": len(candidates),
        "candidate_ids": sorted(item["candidate_id"] for item in candidates),
        "raw_files_modified": 0,
        "actions_dispatched": 0,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    receipt_write = atomic_write_json(receipt, payload)
    return {
        "ok": True,
        "status": "normal_complete",
        "source_write": source_write,
        "candidate_count": len(candidates),
        "candidate_results": deposition.get("candidate_results", []),
        "receipt": receipt.as_posix(),
        "receipt_write": receipt_write,
        "raw_files_modified": 0,
        "actions_dispatched": 0,
    }


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    sub = root.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--daily-source", type=Path, required=True)
    prepare.add_argument("--memory-root", type=Path, required=True)
    prepare.add_argument("--output", type=Path, required=True)
    finish = sub.add_parser("finalize")
    finish.add_argument("--daily-source", type=Path, required=True)
    finish.add_argument("--extraction", type=Path, required=True)
    finish.add_argument("--memory-root", type=Path, required=True)
    finish.add_argument("--cognitive-root", type=Path, required=True)
    return root


def main() -> int:
    args = parser().parse_args()
    try:
        if args.command == "prepare":
            result = prepare_manifest(args.daily_source, args.memory_root, args.output)
        else:
            result = finalize(
                args.daily_source,
                args.extraction,
                args.memory_root,
                args.cognitive_root,
            )
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError, json.JSONDecodeError, PIPELINE.PipelineError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
