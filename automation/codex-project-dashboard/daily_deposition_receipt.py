"""Compatibility exports for the canonical compiled-source receipt contract."""

from compiled_source_contract import (
    RECEIPT_REQUIRED_FROM,
    receipt_path as _canonical_receipt_path,
    source_deposition_state,
)


def receipt_path(memory_root, family, source_date):
    """Preserve the Dashboard's historical ``chatgpt-daily`` call form."""
    return _canonical_receipt_path(memory_root, str(family).removesuffix("-daily"), source_date)

__all__ = ["RECEIPT_REQUIRED_FROM", "receipt_path", "source_deposition_state"]
