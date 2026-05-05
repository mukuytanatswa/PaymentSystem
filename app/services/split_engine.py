from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from app.models.schemas import SplitInput


class SplitValidationError(Exception):
    pass


class SplitResult:
    def __init__(
        self,
        vendor_id: UUID,
        gross_amount: Decimal,
        fee_rate: Decimal,
    ):
        self.vendor_id = vendor_id
        self.gross_amount = gross_amount
        self.platform_fee = (gross_amount * fee_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        self.net_amount = gross_amount - self.platform_fee


def calculate_splits(
    splits_input: list[SplitInput],
    total_amount: Decimal,
    platform_fee_percentage: Decimal,
    vendor_fee_overrides: dict[UUID, Decimal | None],
) -> list[SplitResult]:
    gross_sum = sum(s.gross_amount for s in splits_input)

    if gross_sum.quantize(Decimal("0.01")) != total_amount.quantize(Decimal("0.01")):
        raise SplitValidationError(
            f"Split amounts ({gross_sum}) do not sum to total_amount ({total_amount})"
        )

    results = []
    for split in splits_input:
        vendor_override = vendor_fee_overrides.get(split.vendor_id)
        fee_rate = (
            vendor_override if vendor_override is not None else platform_fee_percentage
        ) / Decimal("100")

        results.append(SplitResult(split.vendor_id, split.gross_amount, fee_rate))

    return results
