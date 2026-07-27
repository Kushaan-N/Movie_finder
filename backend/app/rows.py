"""Per-chain row-position normalization.

The user's "minimum row" means a PHYSICAL position measured from the screen
(row 1 = closest to the screen), NOT a literal label match. Seat maps label
rows inconsistently:

  * some use letters (A, B, C...) and AMC skips I and O
  * some use numbers
  * rows get removed for recliner / ADA conversions, so the Nth label is not
    always the Nth physical row

Strategy, in order of trust:

  1. DOM/API order — the order rows are returned top-to-bottom in the seat map
     is the most reliable signal of physical position. This is the SAFE default
     whenever chain confidence is low.
  2. A chain-specific label rule (e.g. AMC letter-skip-I/O) as a fallback and
     for display.
  3. A hand-editable overrides table in row_mappings.json for theaters whose
     mapping has been verified/corrected against a real seat map.

Everything is driven by row_mappings.json so chains can be refined by hand
without touching code.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

from .config import get_settings

logger = logging.getLogger("showtime_finder.rows")

# Alphabet with the two letters cinemas most commonly skip (I, O) removed.
_ALPHA_NO_IO = [c for c in "ABCDEFGHJKLMNPQRSTUVWXYZ"]  # note: no I, no O
_LETTER_TO_POS = {c: i + 1 for i, c in enumerate(_ALPHA_NO_IO)}


@dataclass
class RowResult:
    physical_row: int
    raw_label: Optional[str]
    strategy_used: str

    @property
    def display(self) -> str:
        if self.raw_label:
            return f"Row {self.raw_label} → physical row {self.physical_row}"
        return f"Physical row {self.physical_row}"


@lru_cache
def _load_mappings() -> dict:
    settings = get_settings()
    try:
        with open(settings.row_mappings_file, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as exc:  # pragma: no cover - config error
        logger.warning("Could not load row_mappings.json (%s); using DOM order only.", exc)
        return {"default_strategy": "dom_order", "chains": {}}


def _chain_config(chain: str) -> dict:
    mappings = _load_mappings()
    chains = mappings.get("chains", {})
    chain = (chain or "").lower()
    if chain in chains:
        return chains[chain]
    # Resolve brand aliases (e.g. century/tinseltown -> cinemark).
    for name, cfg in chains.items():
        if chain in [a.lower() for a in cfg.get("aliases", [])]:
            return cfg
    return {}


def _letter_skip_io(label: str) -> Optional[int]:
    """Map an AMC-style letter label to a physical position, skipping I and O.

    Handles single letters (A..Z minus I/O) and double letters (AA, AB...)
    for very large auditoriums.
    """
    label = (label or "").strip().upper()
    if not label or not label.isalpha():
        return None
    pos = 0
    base = len(_ALPHA_NO_IO)
    for ch in label:
        if ch not in _LETTER_TO_POS:
            return None
        pos = pos * base + _LETTER_TO_POS[ch]
    return pos


def _numeric(label: str) -> Optional[int]:
    label = (label or "").strip()
    digits = "".join(c for c in label if c.isdigit())
    return int(digits) if digits else None


def normalize_row(
    chain: str,
    raw_label: Optional[str],
    dom_order_index: Optional[int] = None,
    theater_id: Optional[str] = None,
) -> RowResult:
    """Return the physical row for a seat-map row.

    Args:
        chain: theater chain key (amc, regal, cinemark, ...).
        raw_label: the label shown on the seat map (e.g. "H" or "7"), if any.
        dom_order_index: 0-based index of this row in the order the seat map
            returned it (top of map = closest to screen = index 0). Most
            reliable signal when available.
        theater_id: used to look up per-theater overrides.
    """
    cfg = _chain_config(chain)
    strategy = cfg.get("strategy", _load_mappings().get("default_strategy", "dom_order"))
    prefer_dom = cfg.get("prefer_dom_order", strategy == "dom_order")

    # 1. Hand-verified per-theater override wins outright.
    overrides = cfg.get("overrides", {})
    if theater_id and raw_label:
        ov_key = f"{theater_id}:{raw_label}"
        if ov_key in overrides:
            return RowResult(int(overrides[ov_key]), raw_label, "override")

    # Always log the raw label so mappings can be spot-checked / refined later.
    if raw_label is not None:
        logger.debug(
            "row-normalize chain=%s theater=%s label=%s dom_index=%s",
            chain, theater_id, raw_label, dom_order_index,
        )

    # 2. Prefer DOM order when the chain config says so (recliner-safe default).
    if prefer_dom and dom_order_index is not None:
        return RowResult(dom_order_index + 1, raw_label, "dom_order")

    # 3. Chain-specific label rule.
    label_pos: Optional[int] = None
    if strategy == "letter_skip_io":
        label_pos = _letter_skip_io(raw_label or "")
    elif strategy == "numeric":
        label_pos = _numeric(raw_label or "")
    # (dom_order strategy has no label rule.)

    if label_pos is not None:
        return RowResult(label_pos, raw_label, strategy)

    # 4. Safe fallback: DOM order if we have it, else assume row 1 (unknown).
    if dom_order_index is not None:
        return RowResult(dom_order_index + 1, raw_label, "dom_order_fallback")

    logger.info(
        "Could not resolve physical row for chain=%s label=%s; defaulting to 1.",
        chain, raw_label,
    )
    return RowResult(1, raw_label, "unknown")
