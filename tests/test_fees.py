from linkyard.fees import (
    WARN_BELOW_CENTS,
    cents_to_dollars,
    net_cents,
    percent_fee_cents,
    small_sku_warning,
    stripe_fee_cents,
)


def test_five_dollars_clears_one_dollar_after_fees():
    assert percent_fee_cents(500) == 15  # 2.9% of 500 = 14.5 → 15
    assert stripe_fee_cents(500) == 45
    assert net_cents(500) == 455
    assert net_cents(500) > 100


def test_one_dollar_sku_net():
    assert stripe_fee_cents(100) == 33
    assert net_cents(100) == 67


def test_rounding_half_up_not_bankers():
    # 50 * 0.029 = 1.45 → 1; 150 * 0.029 = 4.35 → 4
    assert percent_fee_cents(50) == 1
    assert percent_fee_cents(150) == 4
    # 500 * 0.029 = 14.5 must not banker's-round to 14
    assert percent_fee_cents(500) == 15


def test_small_sku_warning_threshold():
    assert WARN_BELOW_CENTS == 200
    assert small_sku_warning(199) is not None
    assert small_sku_warning(200) is None
    assert small_sku_warning(500) is None
    assert "0.30" in small_sku_warning(100)


def test_cents_to_dollars():
    assert cents_to_dollars(455) == "$4.55"
    assert cents_to_dollars(0) == "$0.00"
    assert cents_to_dollars(-20) == "-$0.20"


def test_fee_exceeds_tiny_price():
    assert net_cents(10) < 0
