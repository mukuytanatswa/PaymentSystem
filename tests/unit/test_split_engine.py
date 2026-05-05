import uuid
from decimal import Decimal

import pytest

from app.models.schemas import SplitInput
from app.services.split_engine import SplitValidationError, calculate_splits


def _vendor():
    return uuid.uuid4()


def _split(vendor_id, amount):
    return SplitInput(vendor_id=vendor_id, gross_amount=Decimal(amount))


PLATFORM_FEE = Decimal("2.5")


def test_two_vendors_happy_path():
    v1, v2 = _vendor(), _vendor()
    splits = calculate_splits(
        [_split(v1, "60.00"), _split(v2, "40.00")],
        total_amount=Decimal("100.00"),
        platform_fee_percentage=PLATFORM_FEE,
        vendor_fee_overrides={v1: None, v2: None},
    )
    assert len(splits) == 2
    r1 = next(r for r in splits if r.vendor_id == v1)
    assert r1.gross_amount == Decimal("60.00")
    assert r1.platform_fee == Decimal("1.50")   # 60 * 2.5%
    assert r1.net_amount == Decimal("58.50")

    r2 = next(r for r in splits if r.vendor_id == v2)
    assert r2.platform_fee == Decimal("1.00")   # 40 * 2.5%
    assert r2.net_amount == Decimal("39.00")


def test_vendor_fee_override():
    v1, v2 = _vendor(), _vendor()
    splits = calculate_splits(
        [_split(v1, "100.00"), _split(v2, "100.00")],
        total_amount=Decimal("200.00"),
        platform_fee_percentage=PLATFORM_FEE,
        vendor_fee_overrides={v1: Decimal("5.0"), v2: None},  # v1 custom, v2 platform default
    )
    r1 = next(r for r in splits if r.vendor_id == v1)
    assert r1.platform_fee == Decimal("5.00")   # 100 * 5%
    assert r1.net_amount == Decimal("95.00")

    r2 = next(r for r in splits if r.vendor_id == v2)
    assert r2.platform_fee == Decimal("2.50")   # 100 * 2.5%
    assert r2.net_amount == Decimal("97.50")


def test_validation_error_sum_mismatch():
    v1, v2 = _vendor(), _vendor()
    with pytest.raises(SplitValidationError):
        calculate_splits(
            [_split(v1, "60.00"), _split(v2, "30.00")],  # sums to 90, not 100
            total_amount=Decimal("100.00"),
            platform_fee_percentage=PLATFORM_FEE,
            vendor_fee_overrides={v1: None, v2: None},
        )


def test_single_vendor_full_amount():
    v1 = _vendor()
    splits = calculate_splits(
        [_split(v1, "250.00")],
        total_amount=Decimal("250.00"),
        platform_fee_percentage=PLATFORM_FEE,
        vendor_fee_overrides={v1: None},
    )
    assert len(splits) == 1
    assert splits[0].platform_fee == Decimal("6.25")
    assert splits[0].net_amount == Decimal("243.75")


def test_zero_fee_net_equals_gross():
    v1 = _vendor()
    splits = calculate_splits(
        [_split(v1, "100.00")],
        total_amount=Decimal("100.00"),
        platform_fee_percentage=Decimal("0"),
        vendor_fee_overrides={v1: None},
    )
    assert splits[0].platform_fee == Decimal("0.00")
    assert splits[0].net_amount == splits[0].gross_amount


def test_rounding_half_up():
    # gross=0.10, fee_rate=5% → 0.10 * 0.05 = 0.005
    # ROUND_HALF_UP → 0.01  (ROUND_HALF_EVEN would give 0.00)
    v1 = _vendor()
    splits = calculate_splits(
        [_split(v1, "0.10")],
        total_amount=Decimal("0.10"),
        platform_fee_percentage=Decimal("5"),
        vendor_fee_overrides={v1: None},
    )
    assert splits[0].platform_fee == Decimal("0.01")
    assert splits[0].net_amount == Decimal("0.09")
