"""
app/money.py

Every rupee amount that crosses a trust boundary (API request, DB row,
payment provider call) is handled as an integer number of paise (minor
units), converted through Decimal — never through float — so that
0.1 + 0.2 != 0.3 style bugs can never silently corrupt a checkout total.

Rule of thumb: floats never touch money in this codebase. If you see a
`float` being multiplied against a price anywhere outside this file,
that's a bug.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

MINOR_UNITS_PER_MAJOR = 100  # paise per rupee


class InvalidMoneyError(ValueError):
    pass


def to_decimal(value: str | float | int | Decimal) -> Decimal:
    """Convert user input to Decimal safely. Rejects NaN/garbage."""
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise InvalidMoneyError(f"'{value}' is not a valid monetary amount") from exc
    if not d.is_finite():
        raise InvalidMoneyError(f"'{value}' is not a finite monetary amount")
    return d


def rupees_to_minor(value: str | float | int | Decimal) -> int:
    """Rupees (major units, e.g. 499.50) -> integer paise (49950)."""
    d = to_decimal(value)
    if d < 0:
        raise InvalidMoneyError("Monetary amounts must be non-negative")
    minor = (d * MINOR_UNITS_PER_MAJOR).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(minor)


def minor_to_rupees(minor: int) -> Decimal:
    """Integer paise -> Decimal rupees, for display/audit only."""
    return (Decimal(minor) / MINOR_UNITS_PER_MAJOR).quantize(Decimal("0.01"))


def pct_to_bp100(pct: str | float | int | Decimal) -> int:
    """Percentage (e.g. 8.25) -> integer "basis points * 100" (82500) so
    that discount percentages also avoid float drift. Divide by 100_00
    to get back the original percentage as a Decimal."""
    d = to_decimal(pct)
    if d < 0 or d > 100:
        raise InvalidMoneyError("Discount percentage must be between 0 and 100")
    return int((d * 10_000).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def bp100_to_pct(bp100: int) -> Decimal:
    return (Decimal(bp100) / Decimal(10_000)).quantize(Decimal("0.01"))


def apply_discount_minor(gross_minor: int, discount_bp100: int) -> int:
    """Apply a discount (in bp100 units) to a gross amount (in minor
    units) and return the net amount in minor units, rounded half-up."""
    gross = Decimal(gross_minor)
    discount_fraction = Decimal(discount_bp100) / Decimal(1_000_000)  # bp100 -> fraction
    net = gross * (Decimal(1) - discount_fraction)
    return int(net.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
