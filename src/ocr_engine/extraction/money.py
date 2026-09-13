"""Money parsing: Indonesian receipts mix '.' and ',' as grouping separators.

Rule: the last separator is a decimal point only when it is followed by
exactly two digits at the very end of the number (e.g. "12,75"); every other
separator is grouping ("27,800" = 27800, "12.765" = 12765). The result is the
smallest unit: whole rupiah for zero decimals, hundredths otherwise.
"""

from __future__ import annotations

import re

_MONEY_RE = re.compile(
    r"(?<![0-9.,])\s*(?:rp\.?\s*)?([-+]?)((?:\d{1,3}(?:[.,\s]\d{1,3})+|\d+)(?:[.,]\d{2})?)(?![0-9])",
    re.IGNORECASE,
)


def parse_money(text: str) -> int | None:
    """Return the amount in the smallest unit, or None when no money token exists."""
    match = _MONEY_RE.search(text.replace("\u00a0", " "))
    if not match:
        return None

    sign, digits = match.group(1), match.group(2).strip()
    last_sep = _last_separator(digits)
    if last_sep is not None and len(digits) - last_sep - 1 == 2 and digits[last_sep] in ".,":
        # Decimal: the concatenated digits already ARE the smallest unit
        # ("12,75" -> 1275 hundredths; "1.234.567,89" -> 123456789).
        cleaned = re.sub(r"[.,\s]", "", digits)
    else:
        cleaned = re.sub(r"[.,\s]", "", digits)

    try:
        value = int(cleaned)
    except ValueError:
        return None
    return -value if sign == "-" else value


def _last_separator(digits: str) -> int | None:
    for idx in range(len(digits) - 1, -1, -1):
        if digits[idx] in "., ":
            return idx
    return None


def format_money(amount: int, currency: str = "IDR") -> str:
    sign = "-" if amount < 0 else ""
    return f"{sign}{currency} {abs(amount):,}".replace(",", ".")
