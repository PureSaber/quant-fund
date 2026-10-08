"""Point-in-time fund terms with bitemporal effective and knowledge dates."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

TERM_FIELDS = (
    "manager",
    "strategy",
    "kind",
    "currency",
    "end_date",
    "share_group",
    "confirm_lag",
    "settle_lag",
    "notice_days",
    "lock_days",
    "buy_fee",
    "sell_tiers",
    "open_dates",
    "min_buy",
    "max_stale_days",
    "nav_fee_basis",
    "execution_policy",
)


def _day(value) -> pd.Timestamp:
    result = pd.Timestamp(value)
    if pd.isna(result) or result.tzinfo is not None or result != result.normalize():
        raise ValueError(f"日期必须是无时区的YYYY-MM-DD：{value}")
    return result


@dataclass(frozen=True)
class FundTermsVersion:
    fund_id: str
    terms_id: str
    version_id: str
    effective_from: pd.Timestamp
    effective_to: pd.Timestamp | None
    known_at: pd.Timestamp
    values: tuple[tuple[str, object], ...]

    @classmethod
    def from_mapping(cls, item, funds):
        required = {
            "fund_id",
            "terms_id",
            "version_id",
            "effective_from",
            "effective_to",
            "known_at",
            *TERM_FIELDS,
        }
        missing = required - {"execution_policy"} - set(item)
        if missing:
            raise ValueError(f"历史条款版本缺少字段：{sorted(missing)}")
        extra = set(item) - required
        if extra:
            raise ValueError(f"历史条款版本包含未知字段：{sorted(extra)}")
        if item["fund_id"] is None or not str(item["fund_id"]).strip():
            raise ValueError("历史条款fund_id不能为空")
        fund_id = str(item["fund_id"])
        if fund_id not in funds:
            raise ValueError(f"历史条款包含基金主表未登记的代码：{fund_id}")
        if item["terms_id"] is None or item["version_id"] is None:
            raise ValueError("terms_id和version_id不能为空")
        terms_id, version_id = str(item["terms_id"]).strip(), str(item["version_id"]).strip()
        if not terms_id or not version_id:
            raise ValueError("terms_id和version_id不能为空")
        effective_from = _day(item["effective_from"])
        if item["effective_to"] == "":
            raise ValueError("effective_to无截止时必须为null，不能使用空字符串")
        effective_to = _day(item["effective_to"]) if item["effective_to"] is not None else None
        if effective_to is not None and effective_to <= effective_from:
            raise ValueError("历史条款effective_to必须晚于effective_from")
        known_at = _day(item["known_at"])
        values = {name: item.get(name) for name in TERM_FIELDS}
        values["sell_tiers"] = tuple(tuple(tier) for tier in values["sell_tiers"])
        values["open_dates"] = tuple(values["open_dates"])

        # Import lazily to keep data.py as the owner of the public Fund contract.
        from .data import Fund

        base = funds[fund_id]
        validated = Fund(
            fund_id=fund_id,
            name=base.name,
            inception=base.inception,
            known_at=base.known_at,
            **values,
        )
        values["execution_policy"] = validated.execution_policy
        return cls(
            fund_id,
            terms_id,
            version_id,
            effective_from,
            effective_to,
            known_at,
            tuple((name, values[name]) for name in TERM_FIELDS),
        )

    def active_on(self, date) -> bool:
        date = _day(date)
        return self.effective_from <= date and (
            self.effective_to is None or date < self.effective_to
        )


@dataclass(frozen=True)
class ResolvedFund:
    fund: object
    terms_id: str
    version_id: str
    effective_from: pd.Timestamp
    effective_to: pd.Timestamp | None
    terms_known_at: pd.Timestamp
    historical_pit: bool

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, "fund"), name)

    def evidence(self, *, effective_on, known_on, use=None):
        return {
            "fund_id": self.fund.fund_id,
            "terms_id": self.terms_id,
            "version_id": self.version_id,
            "effective_from": self.effective_from,
            "effective_to": self.effective_to,
            "terms_known_at": self.terms_known_at,
            "effective_on": _day(effective_on),
            "known_on": _day(known_on),
            "use": use,
            "historical_pit": self.historical_pit,
        }


class FundTermsTable:
    """Resolve latest-known revision first, then select its effective interval."""

    def __init__(self, funds, items):
        if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
            raise ValueError("fund_terms.json必须是对象数组")
        self.funds = funds
        self.versions = tuple(FundTermsVersion.from_mapping(item, funds) for item in items)
        if not self.versions:
            raise ValueError("fund_terms.json不能为空")
        version_ids = [item.version_id for item in self.versions]
        if len(version_ids) != len(set(version_ids)):
            raise ValueError("历史条款version_id必须全局唯一")
        keys = [(x.fund_id, x.terms_id, x.known_at) for x in self.versions]
        if len(keys) != len(set(keys)):
            raise ValueError("同一terms_id在同一known_at只能有一个修订版本")
        covered = {item.fund_id for item in self.versions}
        if covered != set(funds):
            raise ValueError(f"历史条款必须覆盖基金主表全部代码：{sorted(set(funds) - covered)}")
        self._validate_visible_slices()

    def _visible(self, fund_id, known_on):
        latest = {}
        for item in self.versions:
            if item.fund_id != fund_id or item.known_at > known_on:
                continue
            previous = latest.get(item.terms_id)
            if previous is None or item.known_at > previous.known_at:
                latest[item.terms_id] = item
        return tuple(latest.values())

    def _validate_visible_slices(self):
        for fund_id in self.funds:
            cutoffs = sorted({x.known_at for x in self.versions if x.fund_id == fund_id})
            for cutoff in cutoffs:
                visible = sorted(self._visible(fund_id, cutoff), key=lambda x: x.effective_from)
                for left, right in zip(visible, visible[1:], strict=False):
                    if left.effective_to is None or right.effective_from < left.effective_to:
                        raise ValueError(
                            f"基金{fund_id}在{cutoff.date()}已知切片存在重叠条款："
                            f"{left.version_id}/{right.version_id}"
                        )

    def resolve(self, fund_id, effective_on, known_on):
        effective_on, known_on = _day(effective_on), _day(known_on)
        visible = self._visible(fund_id, known_on)
        active = [item for item in visible if item.active_on(effective_on)]
        if len(active) != 1:
            reason = "缺失" if not active else "歧义"
            raise ValueError(
                f"基金{fund_id}在effective_on={effective_on.date()}、known_on={known_on.date()}"
                f"的历史条款{reason}"
            )
        item = active[0]
        from .data import Fund

        base = self.funds[fund_id]
        fund = Fund(
            fund_id=fund_id,
            name=base.name,
            inception=base.inception,
            known_at=base.known_at,
            **dict(item.values),
        )
        return ResolvedFund(
            fund,
            item.terms_id,
            item.version_id,
            item.effective_from,
            item.effective_to,
            item.known_at,
            True,
        )


def legacy_fund(fund):
    return ResolvedFund(
        fund,
        f"legacy-static:{fund.fund_id}",
        f"legacy-static:{fund.fund_id}",
        _day(fund.inception),
        _day(fund.end_date) if fund.end_date else None,
        _day(fund.known_at),
        False,
    )
