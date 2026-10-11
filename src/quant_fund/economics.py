"""Optional FOF-level economics for a fixed-capital, CNY research account.

Underlying fund NAV already includes its own expenses. These charges belong only
to the enclosing FOF. Investor subscriptions/redemptions and tax are not modeled.
"""

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .data import day


@dataclass(frozen=True)
class FOFPolicy:
    management_rate: float = 0.0
    custody_rate: float = 0.0
    cash_interest_rate: float = 0.0
    performance_rate: float = 0.0
    known_at: str = ""
    day_count: str = "ACT/365F"
    crystallization_dates: tuple[str, ...] = ()
    payment_dates: tuple[str, ...] = ()
    payment_frequency: str = "daily"

    def __post_init__(self):
        for name in ("management_rate", "custody_rate", "performance_rate"):
            value = getattr(self, name)
            if isinstance(value, bool) or not np.isfinite(value) or not 0 <= value < 1:
                raise ValueError(f"FOF {name} must be finite in [0,1)")
        if (
            isinstance(self.cash_interest_rate, bool)
            or not np.isfinite(self.cash_interest_rate)
            or not -1 < self.cash_interest_rate < 1
        ):
            raise ValueError("FOF cash interest must be finite in (-1,1)")
        if self.day_count != "ACT/365F":
            raise ValueError("FOF only supports explicit ACT/365F")
        if self.payment_frequency not in {"daily", "explicit"}:
            raise ValueError("FOF payment frequency must be daily or explicit")
        if self.payment_frequency == "daily" and self.payment_dates:
            raise ValueError("daily payment cannot also specify payment dates")
        for name in ("crystallization_dates", "payment_dates"):
            values = getattr(self, name)
            if not isinstance(values, (list, tuple)):
                raise ValueError(f"FOF {name} must be a sequence")
            canonical = tuple(str(day(d).date()) for d in values)
            if tuple(values) != canonical or len(set(canonical)) != len(canonical):
                raise ValueError(f"FOF {name} needs unique ISO dates")
            object.__setattr__(self, name, canonical)
        if self.enabled and not self.known_at:
            raise ValueError("FOF policy known_at is required")
        if self.known_at and str(day(self.known_at).date()) != self.known_at:
            raise ValueError("FOF known_at must be an ISO date")

    @property
    def enabled(self):
        return any(
            (
                self.management_rate,
                self.custody_rate,
                self.cash_interest_rate,
                self.performance_rate,
            )
        )

    def validate_calendar(self, dates):
        if self.enabled and day(self.known_at) > dates[0]:
            raise ValueError("FOF policy was not known at the first account date")
        for value in self.crystallization_dates + self.payment_dates:
            if day(value) not in dates:
                raise ValueError("FOF payment/crystallization date outside the account calendar")


@dataclass
class FOFEconomics:
    policy: FOFPolicy
    high_water_mark: float
    fixed_payable: float = 0.0
    performance_reserve: float = 0.0
    records: list = field(default_factory=list)

    @property
    def liability(self):
        return self.fixed_payable + self.performance_reserve

    def close(self, date, previous, gross_assets, cash):
        """Accrue at close, crystallize on named dates, then pay available cash.

        Time-based expenses use previous close net assets; cash interest uses
        previous unencumbered cash. Performance reserves reverse with losses until
        crystallization. The HWM never decreases and is post-performance-fee NAV.
        """
        if not self.policy.enabled:
            return cash
        if day(self.policy.known_at) > date:
            raise ValueError("FOF policy unavailable at account close")
        days = 0 if previous is None else (date - previous["date"]).days
        basis = self.high_water_mark if previous is None else previous["total_value"]
        interest_basis = (
            0 if previous is None else max(0.0, previous["cash"] - previous["fof_fee_liability"])
        )
        fraction = days / 365
        management = basis * self.policy.management_rate * fraction
        custody = basis * self.policy.custody_rate * fraction
        interest = interest_basis * self.policy.cash_interest_rate * fraction
        # An expense cannot be financed with frozen cash or an unsettled receivable.
        if cash + interest < -1e-8:
            raise ValueError("insufficient available cash for negative cash interest")
        cash += interest
        gross_assets += interest
        self.fixed_payable += management + custody
        old_reserve = self.performance_reserve
        net_before_performance = gross_assets - self.fixed_payable
        self.performance_reserve = (
            max(0.0, net_before_performance - self.high_water_mark) * self.policy.performance_rate
        )
        reserve_change = self.performance_reserve - old_reserve
        net_assets = gross_assets - self.liability
        if not np.isfinite(net_assets) or net_assets <= 0:
            raise ValueError("FOF expenses exhaust net assets")
        crystallized = 0.0
        if str(date.date()) in self.policy.crystallization_dates:
            crystallized = self.performance_reserve
            self.fixed_payable += crystallized
            self.performance_reserve = 0.0
            self.high_water_mark = max(self.high_water_mark, net_assets)
        paid = 0.0
        if (
            self.policy.payment_frequency == "daily"
            or str(date.date()) in self.policy.payment_dates
        ):
            paid = min(cash, self.fixed_payable)
            cash -= paid
            self.fixed_payable -= paid
        self.records.append(
            {
                "date": date,
                "elapsed_calendar_days": days,
                "previous_net_assets": basis,
                "interest_cash_basis": interest_basis,
                "management_accrual": management,
                "custody_accrual": custody,
                "cash_interest": interest,
                "performance_reserve_change": reserve_change,
                "performance_crystallized": crystallized,
                "cash_paid": paid,
                "fixed_payable": self.fixed_payable,
                "performance_reserve": self.performance_reserve,
                "fee_liability": self.liability,
                "high_water_mark": self.high_water_mark,
                "net_assets": net_assets,
            }
        )
        return cash

    def frame(self):
        return pd.DataFrame(self.records)
