"""Row-position normalization — the trickiest, most correctness-critical logic."""
from app.rows import normalize_row


def test_amc_letters_skip_i_and_o():
    # A..H = 1..8, then I is skipped so J = 9, and O is skipped so P = 14.
    assert normalize_row("amc", "A").physical_row == 1
    assert normalize_row("amc", "H").physical_row == 8
    assert normalize_row("amc", "J").physical_row == 9  # I skipped
    assert normalize_row("amc", "N").physical_row == 13
    assert normalize_row("amc", "P").physical_row == 14  # O skipped


def test_amc_prefers_dom_order_over_label():
    # Recliner renumbering: DOM order is authoritative when present.
    r = normalize_row("amc", "J", dom_order_index=4)
    assert r.physical_row == 5
    assert r.strategy_used == "dom_order"
    # Label is still carried for display.
    assert "Row J" in r.display and "physical row 5" in r.display


def test_cinemark_numeric_and_aliases():
    assert normalize_row("cinemark", "7").physical_row == 7
    # Century / Tinseltown are Cinemark brands -> resolve via aliases.
    assert normalize_row("century", "3").physical_row == 3
    assert normalize_row("tinseltown", "12").physical_row == 12


def test_generic_chain_falls_back_to_dom_order():
    assert normalize_row("marcus", "whatever", dom_order_index=2).physical_row == 3
    assert normalize_row("totally-unknown-chain", "X", dom_order_index=0).physical_row == 1


def test_unknown_with_no_signal_defaults_to_one():
    r = normalize_row("cinemark", "notanumber")
    assert r.physical_row == 1
    assert r.strategy_used == "unknown"


def test_double_letter_rows_for_large_auditoriums():
    # After Z (position 24 in the no-I/O alphabet) comes AA.
    assert normalize_row("amc", "Z").physical_row == 24
    assert normalize_row("amc", "AA").physical_row == 25
