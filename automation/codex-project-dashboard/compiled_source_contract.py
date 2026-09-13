"""Load the canonical compiled-source contract without copying its rules."""

from __future__ import annotations

import importlib.util
from pathlib import Path


CANONICAL_CONTRACT_PATH = (
    Path(__file__).resolve().parents[2]
    / "codex-skills/chatgpt-daily-report/scripts/compiled_source_contract.py"
)
_SPEC = importlib.util.spec_from_file_location("canonical_compiled_source_contract", CANONICAL_CONTRACT_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"canonical compiled-source contract is unavailable: {CANONICAL_CONTRACT_PATH}")
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)

CONTRACT_VERSION = _MODULE.CONTRACT_VERSION
RECEIPT_REQUIRED_FROM = _MODULE.RECEIPT_REQUIRED_FROM
SourceContractError = _MODULE.SourceContractError
load_compiled_source = _MODULE.load_compiled_source
source_deposition_state = _MODULE.source_deposition_state
receipt_path = _MODULE.receipt_path
stable_candidate_id = _MODULE.stable_candidate_id
extract_structured_candidates = _MODULE.extract_structured_candidates
extract_instrument_candidates = _MODULE.extract_instrument_candidates
parse_frontmatter = _MODULE.parse_frontmatter
