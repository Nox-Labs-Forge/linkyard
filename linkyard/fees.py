"""US card fee estimate: 2.9% + $0.30. Not a payout guarantee."""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

PERCENT = Decimal("0.029")
FLAT_CENTS = 30
WARN_BELOW_CENTS = 200


def percent_fee_cents(amount_cents: int, percent: Decimal = PERCENT) -> int:
    if amount_cents < 0:
        raise ValueError("amount_cents must be >= 0")
    return int(
        (Decimal(amount_cents) * percent).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )


def stripe_fee_cents(
    amount_cents: int,
    percent: Decimal = PERCENT,
    flat_cents: int = FLAT_CENTS,
) -> int:
    return percent_fee_cents(amount_cents, percent) + flat_cents


def net_cents(
    amount_cents: int,
    percent: Decimal = PERCENT,
    flat_cents: int = FLAT_CENTS,
) -> int:
    return amount_cents - stripe_fee_cents(amount_cents, percent, flat_cents)


def cents_to_dollars(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    return f"{sign}${cents // 100}.{cents % 100:02d}"


def small_sku_warning(amount_cents: int) -> str | None:
    if amount_cents < WARN_BELOW_CENTS:
        net = net_cents(amount_cents)
        return (
            f"amount_cents={amount_cents} < {WARN_BELOW_CENTS}; "
            f"$0.30 flat fee eats tiny SKUs (est. net {cents_to_dollars(net)})"
        )
    return None
