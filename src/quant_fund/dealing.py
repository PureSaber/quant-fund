"""Explicit, versionable subscription fees and accounting precision."""

import re
from dataclasses import dataclass
from decimal import ROUND_DOWN, ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal, InvalidOperation

ROUNDINGS = {"half_up": ROUND_HALF_UP, "half_even": ROUND_HALF_EVEN, "down": ROUND_DOWN}


def number(value):
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("Invalid dealing number") from exc
    if not result.is_finite():
        raise ValueError("Dealing numbers must be finite")
    return result


@dataclass(frozen=True)
class ExecutionPolicy:
    subscription_tiers: tuple
    money_decimals: int
    share_decimals: int
    money_rounding: str
    share_rounding: str
    redemption_fee_rounding: str
    residual_destination: str
    source_ref: str
    source_sha256: str

    def __post_init__(self):
        for value, maximum in ((self.money_decimals, 6), (self.share_decimals, 8)):
            if type(value) is not int or not 0 <= value <= maximum:
                raise ValueError("Invalid dealing decimal precision")
        if self.money_rounding not in ROUNDINGS or self.share_rounding not in ROUNDINGS:
            raise ValueError("Unsupported dealing rounding mode")
        if self.redemption_fee_rounding not in {"per_lot", "aggregate"}:
            raise ValueError("Redemption rounding must be per_lot or aggregate")
        if self.residual_destination != "fund":
            raise ValueError("Only explicit fund-owned rounding residuals are supported")
        if not isinstance(self.source_ref, str) or not self.source_ref.strip():
            raise ValueError("Execution policy requires a source reference")
        if not isinstance(self.source_sha256, str) or not re.fullmatch(
            "[0-9a-f]{64}", self.source_sha256
        ):
            raise ValueError("Execution policy requires the source SHA-256")
        tiers, previous = [], Decimal(-1)
        for tier in self.subscription_tiers:
            if not isinstance(tier, (list, tuple)) or len(tier) != 3:
                raise ValueError(
                    "Subscription tiers require [inclusive_minimum, rate|fixed, value]"
                )
            lower, kind, value = tier
            lower, value = number(lower), number(value)
            if lower <= previous or lower < 0 or value < 0 or kind not in {"rate", "fixed"}:
                raise ValueError("Invalid subscription fee tier")
            if kind == "rate" and value >= 1:
                raise ValueError("Subscription rate must be below one")
            tiers.append((str(lower), kind, str(value)))
            previous = lower
        if not tiers or number(tiers[0][0]) != 0:
            raise ValueError("Subscription fee tiers must start at zero")
        object.__setattr__(self, "subscription_tiers", tuple(tiers))

    def quantize(self, value, *, shares=False, down=False):
        precision = self.share_decimals if shares else self.money_decimals
        mode = self.share_rounding if shares else self.money_rounding
        return number(value).quantize(
            Decimal(1).scaleb(-precision), rounding=ROUND_DOWN if down else ROUNDINGS[mode]
        )

    def validate_request(self, value, *, shares=False):
        if number(value) != self.quantize(value, shares=shares):
            raise ValueError("Instruction exceeds explicitly configured dealing precision")


def subscription(fund, amount, nav):
    policy = fund.execution_policy
    if policy is None:
        net = amount / (1 + fund.buy_fee)
        return {"gross": amount, "fee": amount - net, "shares": net / nav}
    amount, nav = number(amount), number(nav)
    policy.validate_request(amount)
    _, kind, cost = [x for x in policy.subscription_tiers if number(x[0]) <= amount][-1]
    cost = number(cost)
    net = policy.quantize(amount / (1 + cost) if kind == "rate" else amount - cost)
    fee = amount - net
    shares = policy.quantize(net / nav, shares=True)
    if fee < 0 or net <= 0 or shares <= 0:
        raise ValueError("Subscription fee or rounding leaves no valid shares")
    return {
        "gross": float(amount),
        "fee": float(fee),
        "shares": float(shares),
        "rounding_residual": float(net - shares * nav),
    }


def redemption(fund, shares, nav, allocations):
    """Allocations are (shares, holding_days), frozen by the original order."""
    policy = fund.execution_policy
    if policy is None:
        return {
            "shares": shares,
            "gross": shares * nav,
            "fee": sum(take * nav * fund.sell_rate(days) for take, days in allocations),
        }
    policy.validate_request(shares, shares=True)
    raw_gross = number(shares) * number(nav)
    parts = [
        number(take) * number(nav) * number(fund.sell_rate(days)) for take, days in allocations
    ]
    fee = (
        sum((policy.quantize(part) for part in parts), Decimal(0))
        if policy.redemption_fee_rounding == "per_lot"
        else policy.quantize(sum(parts))
    )
    gross = policy.quantize(raw_gross)
    if not 0 <= fee <= gross:
        raise ValueError("Rounded redemption fee exceeds proceeds")
    return {
        "shares": shares,
        "gross": float(gross),
        "fee": float(fee),
        "rounding_residual": float(raw_gross - gross),
    }
