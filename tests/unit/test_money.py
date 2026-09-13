"""Money parsing covers the separator chaos observed on real receipts."""

from __future__ import annotations

import pytest

from ocr_engine.extraction.money import format_money, parse_money


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("27,800", 27800),
        ("27.800", 27800),
        ("1.234.567", 1234567),
        ("1,234,567", 1234567),
        ("TOTAL: 27,800", 27800),
        ("Rp12.500", 12500),
        ("rp 169.825", 169825),
        ("-5.000", -5000),
        ("(3,500)", 3500),
        ("12,75", 1275),
        ("1.234.567,89", 123456789),
        ("100 000", 100000),
        ("no digits here", None),
        ("", None),
        ("2.845", 2845),
        ("24.500", 24500),
    ],
)
def test_parse_money(text: str, expected: int | None) -> None:
    assert parse_money(text) == expected


def test_format_money() -> None:
    assert format_money(169825) == "IDR 169.825"
    assert format_money(-5000) == "-IDR 5.000"
