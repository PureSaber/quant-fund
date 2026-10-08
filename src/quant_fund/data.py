"""Explicit data contracts. No inferred disclosure dates or synthetic backfilling."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd


def day(value) -> pd.Timestamp:
    result = pd.Timestamp(value)
    if pd.isna(result) or result.tzinfo is not None or result != result.normalize():
        raise ValueError(f"日期必须是无时区的YYYY-MM-DD：{value}")
    return result


@dataclass(frozen=True)
class Fund:
    fund_id: str
    name: str
    manager: str
    strategy: str
    kind: str
    inception: str
    known_at: str
    currency: str = "CNY"
    end_date: str | None = None
    share_group: str = ""
    confirm_lag: int = 1
    settle_lag: int = 3
    notice_days: int = 0
    lock_days: int = 0
    buy_fee: float = 0.0
    sell_tiers: list[list[float]] = field(default_factory=lambda: [[100000, 0.0]])
    open_dates: list[str] = field(default_factory=list)
    min_buy: float = 0.0
    max_stale_days: int = 10
    nav_fee_basis: str = "net_all_fund_fees"
    execution_policy: object | None = None

    def __post_init__(self):
        from .dealing import ExecutionPolicy

        if self.execution_policy is not None:
            policy = self.execution_policy
            if isinstance(policy, dict):
                policy = ExecutionPolicy(**policy)
            if not isinstance(policy, ExecutionPolicy) or self.buy_fee != 0:
                raise ValueError("Explicit execution policy requires buy_fee=0 and a valid policy")
            object.__setattr__(self, "execution_policy", policy)
        if not self.fund_id or not self.name or not self.manager or not self.strategy:
            raise ValueError("基金代码、名称、管理人和策略分类不能为空")
        if self.kind not in {"public", "private", "etf"}:
            raise ValueError("kind必须为public/private/etf")
        day(self.inception)
        day(self.known_at)
        if self.end_date and day(self.end_date) < day(self.inception):
            raise ValueError("终止日期早于成立日期")
        for name in ("confirm_lag", "settle_lag", "notice_days", "lock_days", "max_stale_days"):
            value = getattr(self, name)
            if not isinstance(value, int) or value < 0:
                raise ValueError(f"{name}必须为非负整数")
        if self.settle_lag < self.confirm_lag:
            raise ValueError("到账滞后不能小于确认滞后")
        if not np.isfinite(self.buy_fee) or not 0 <= self.buy_fee < 1:
            raise ValueError("申购费率无效")
        if not np.isfinite(self.min_buy) or self.min_buy < 0:
            raise ValueError("最低申购金额无效")
        previous = -1
        for upper, rate in self.sell_tiers:
            if not np.isfinite([upper, rate]).all() or upper <= previous or not 0 <= rate < 1:
                raise ValueError("赎回阶梯必须按持有天数严格递增，费率位于[0,1)")
            previous = upper
        if not self.sell_tiers or self.sell_tiers[-1][0] < 100000:
            raise ValueError("赎回费率表必须显式覆盖长期持有（末档上界至少100000天）")
        if self.kind == "private" and not self.open_dates:
            raise ValueError("私募基金必须提供明确的开放日期")
        for value in self.open_dates:
            day(value)
        if self.nav_fee_basis != "net_all_fund_fees":
            raise ValueError("第一版只接受已扣基金层费用的净值；未扣业绩报酬数据需先规范化")
        object.__setattr__(self, "sell_tiers", tuple(tuple(tier) for tier in self.sell_tiers))
        object.__setattr__(self, "open_dates", tuple(self.open_dates))

    def eligible(self, date) -> bool:
        date = day(date)
        return max(day(self.inception), day(self.known_at)) <= date and (
            self.end_date is None or date < day(self.end_date)
        )

    def sell_rate(self, holding_days: int) -> float:
        for upper, rate in self.sell_tiers:
            if holding_days < upper:
                return rate
        raise ValueError("持有期超出费率表覆盖范围")


@dataclass(frozen=True)
class FundIdentity:
    """Immutable identity fields; all mutable classifications and terms live in versions."""

    fund_id: str
    name: str
    inception: str
    known_at: str

    def __post_init__(self):
        if not self.fund_id or not self.name:
            raise ValueError("基金代码和名称不能为空")
        day(self.inception)
        day(self.known_at)


NAV_COLUMNS = ["fund_id", "nav_date", "known_at", "unit_nav", "total_return_nav", "source"]


def validate_nav(frame: pd.DataFrame, fund_ids=None) -> pd.DataFrame:
    missing = set(NAV_COLUMNS) - set(frame)
    if missing:
        raise ValueError(f"净值缺少字段：{sorted(missing)}")
    result = frame[NAV_COLUMNS].copy()
    if result.empty:
        raise ValueError("净值表为空")
    if result.isna().any().any():
        raise ValueError("净值表存在空值；不能用0或累计净值代替缺失的复权净值")
    result["fund_id"] = result.fund_id.astype(str)
    for name in ("nav_date", "known_at"):
        result[name] = result[name].map(day)
    if (result.known_at < result.nav_date).any():
        raise ValueError("known_at不能早于净值所属日期")
    for name in ("unit_nav", "total_return_nav"):
        result[name] = pd.to_numeric(result[name], errors="raise")
        if not np.isfinite(result[name]).all() or (result[name] <= 0).any():
            raise ValueError(f"{name}必须为有限正数")
    keys = ["fund_id", "nav_date", "known_at"]
    if result.duplicated(keys).any():
        raise ValueError("存在重复的基金/净值日期/获知日期；每日模型不支持同日多次修订")
    if result.source.astype(str).str.strip().eq("").any():
        raise ValueError("必须提供数据来源")
    if fund_ids is not None and not set(result.fund_id).issubset(fund_ids):
        raise ValueError("净值包含基金主表未登记的代码")
    return result.sort_values(keys).reset_index(drop=True)


@dataclass
class Dataset:
    funds: dict[str, Fund]
    nav: pd.DataFrame
    calendar: pd.DatetimeIndex
    distributions: pd.DataFrame
    metadata: dict
    fingerprint: str
    purpose_calendars: object | None = None
    disclosures: pd.DataFrame | None = None
    term_versions: object | None = None
    input_files: tuple[str, ...] = ()

    @property
    def historical_terms(self):
        return self.term_versions is not None

    @property
    def terms_mode(self):
        return "historical_pit" if self.historical_terms else "legacy_static"

    def fund_at(self, fund_id, effective_on, known_on=None):
        from .terms import legacy_fund

        if fund_id not in self.funds:
            raise ValueError(f"未知基金代码：{fund_id}")
        known_on = effective_on if known_on is None else known_on
        if self.term_versions is None:
            return legacy_fund(self.funds[fund_id])
        return self.term_versions.resolve(fund_id, effective_on, known_on)

    def term_evidence(self, fund_id, effective_on, known_on=None, *, use=None):
        known_on = effective_on if known_on is None else known_on
        return self.fund_at(fund_id, effective_on, known_on).evidence(
            effective_on=effective_on, known_on=known_on, use=use
        )

    @property
    def account_calendar(self):
        if self.purpose_calendars is None:
            return self.calendar
        all_days = set(self.calendar)
        for calendar in self.purpose_calendars.calendars:
            all_days.update(pd.DatetimeIndex(calendar.open_days))
        return pd.DatetimeIndex(sorted(all_days))

    def purpose_calendar(self, purpose, on, known_on):
        if self.purpose_calendars is None:
            raise ValueError("缺少分用途日历，不能把交易日当作资金到账日")
        at = day(known_on).tz_localize("Asia/Shanghai") + pd.Timedelta(hours=23, minutes=59)
        calendar_id = self.metadata["calendar_ids"][purpose]
        return self.purpose_calendars.asof(calendar_id, purpose, at, on), at

    @classmethod
    def load(cls, directory: str | Path) -> Dataset:
        root = Path(directory)
        names = ["funds.json", "nav.csv", "calendar.csv", "distributions.csv", "dataset.json"]
        optional_names = ["calendars.json", "holdings.csv", "fund_terms.json"]
        loaded_names = [*names, *(name for name in optional_names if (root / name).exists())]
        digest = hashlib.sha256()
        for name in loaded_names:
            digest.update(name.encode())
            digest.update((root / name).read_bytes())
        items = json.loads((root / "funds.json").read_text(encoding="utf-8"))
        if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
            raise ValueError("funds.json必须是对象数组")
        if (root / "fund_terms.json").exists():
            identity_fields = {"fund_id", "name", "inception", "known_at"}
            for item in items:
                if set(item) != identity_fields:
                    raise ValueError(
                        "历史条款模式的funds.json只能包含不可变身份字段："
                        "fund_id/name/inception/known_at"
                    )
            funds = {item["fund_id"]: FundIdentity(**item) for item in items}
        else:
            funds = {item["fund_id"]: Fund(**item) for item in items}
        if not funds or len(funds) != len(items):
            raise ValueError("基金主表为空或存在重复代码")
        nav = validate_nav(pd.read_csv(root / "nav.csv", dtype={"fund_id": str}), set(funds))
        calendar = pd.DatetimeIndex(pd.read_csv(root / "calendar.csv")["date"].map(day))
        if calendar.empty or calendar.has_duplicates or not calendar.is_monotonic_increasing:
            raise ValueError("交易日历必须非空、唯一且升序")
        distributions = pd.read_csv(root / "distributions.csv", dtype={"fund_id": str})
        required = {"fund_id", "ex_date", "pay_date", "known_at", "cash_per_share"}
        if not required.issubset(distributions):
            raise ValueError("分红表字段不完整")
        for col in ("ex_date", "pay_date", "known_at"):
            distributions[col] = pd.to_datetime(distributions[col].map(day))
        distributions["cash_per_share"] = pd.to_numeric(distributions.cash_per_share)
        if not set(distributions.fund_id).issubset(funds):
            raise ValueError("分红表包含未知基金")
        if distributions.duplicated(["fund_id", "ex_date"]).any():
            raise ValueError("同一基金同一除息日只能有一个分红事件")
        if (distributions.known_at > distributions.ex_date).any():
            raise ValueError("分红公告获知日晚于除息日；缺少当时可得的分红记录")
        if (distributions.pay_date < distributions.ex_date).any():
            raise ValueError("分红支付日不能早于除息日")
        if (
            not np.isfinite(distributions.cash_per_share).all()
            or (distributions.cash_per_share <= 0).any()
        ):
            raise ValueError("每份分红必须为有限正数")
        if not set(distributions.ex_date).issubset(calendar):
            raise ValueError("交易日历必须覆盖所有除息日")
        metadata = json.loads((root / "dataset.json").read_text(encoding="utf-8"))
        if metadata.get("classification") not in {"synthetic", "user_provided", "public_source"}:
            raise ValueError("必须标明数据分类")
        if not metadata.get("calendar_source") or not metadata.get("known_at_policy"):
            raise ValueError("必须说明交易日历来源和历史获知日期的来源")
        purposes, disclosures = None, None
        if (root / "calendars.json").exists():
            from quant_data_kit.financial.calendars import CalendarBook, PurposeCalendar

            records = json.loads((root / "calendars.json").read_text(encoding="utf-8"))
            purposes = CalendarBook([PurposeCalendar(**item) for item in records])
            if not {"dealing", "confirmation", "banking"}.issubset(
                metadata.get("calendar_ids", {})
            ):
                raise ValueError("需显式指定申赎、确认和资金日历ID")
        elif metadata["classification"] != "synthetic":
            raise ValueError("真实基金账本需提供calendars.json分用途日历；不使用周末代理")
        if (root / "holdings.csv").exists():
            from quant_data_kit.financial.holdings import validate_holdings

            disclosures = validate_holdings(pd.read_csv(root / "holdings.csv", dtype=str))
        term_versions = None
        if (root / "fund_terms.json").exists():
            from .terms import FundTermsTable

            term_versions = FundTermsTable(
                funds,
                json.loads((root / "fund_terms.json").read_text(encoding="utf-8")),
            )
        return cls(
            funds,
            nav,
            calendar,
            distributions,
            metadata,
            digest.hexdigest(),
            purposes,
            disclosures,
            term_versions,
            tuple(loaded_names),
        )

    def as_of(self, date) -> pd.DataFrame:
        date = day(date)
        rows = self.nav.loc[(self.nav.known_at <= date) & (self.nav.nav_date <= date)]
        return rows.sort_values("known_at").drop_duplicates(["fund_id", "nav_date"], keep="last")

    def quote(self, fund_id, date, *, exact_date=None):
        rows = self.as_of(date)
        rows = rows.loc[rows.fund_id == fund_id]
        if exact_date is not None:
            rows = rows.loc[rows.nav_date == day(exact_date)]
        if rows.empty:
            return None
        return rows.sort_values("nav_date").iloc[-1]

    def returns(self, date, frequency="ME", window=24) -> pd.DataFrame:
        if frequency not in {"ME", "W-FRI"}:
            raise ValueError("研究频率只支持月度ME或周度W-FRI")
        date = day(date)
        rows = self.as_of(date)
        series = {}
        for code, identity in self.funds.items():
            if date < max(day(identity.inception), day(identity.known_at)):
                continue
            fund = self.fund_at(code, date, date)
            if not fund.eligible(date):
                continue
            values = rows.loc[rows.fund_id == code].set_index("nav_date").sort_index()
            if values.empty or (date - values.index[-1]).days > fund.max_stale_days:
                continue
            price = values.total_return_nav.resample(frequency).last()
            last_date = pd.Series(values.index, index=values.index).resample(frequency).last()
            age = (last_date.index.to_series() - last_date).dt.days
            endpoints = pd.Series(self.calendar, index=self.calendar).resample(frequency).last()
            # A disclosed mid-period price is not a completed month/week observation.
            complete = last_date >= endpoints.reindex(last_date.index)
            stale_limits = pd.Series(
                {
                    endpoint: self.fund_at(code, endpoint, date).max_stale_days
                    for endpoint in last_date.index
                    if endpoint <= date
                },
                dtype=float,
            )
            price = price.where((age <= stale_limits.reindex(age.index)) & complete)
            price = price.loc[price.index <= date]
            series[code] = price.pct_change(fill_method=None)
        return pd.DataFrame(series).tail(window).replace([np.inf, -np.inf], np.nan)


def import_observations(path, database, mapping=None) -> dict:
    """Append immutable versions to SQLite. A conflicting version aborts the transaction."""
    path, database = Path(path), Path(database)
    if path.suffix.lower() == ".csv":
        frame = pd.read_csv(path, dtype=str)
    elif path.suffix.lower() == ".xlsx":
        frame = pd.read_excel(path, dtype=str)
    else:
        raise ValueError("仅支持CSV或XLSX")
    frame = validate_nav(frame.rename(columns=mapping or {}))
    database.parent.mkdir(parents=True, exist_ok=True)
    added = 0
    with sqlite3.connect(database) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS nav (
            fund_id TEXT, nav_date TEXT, known_at TEXT, unit_nav REAL,
            total_return_nav REAL, source TEXT,
            PRIMARY KEY (fund_id, nav_date, known_at))""")
        for row in frame.itertuples(index=False, name=None):
            row = tuple(x.strftime("%Y-%m-%d") if isinstance(x, pd.Timestamp) else x for x in row)
            existing = conn.execute(
                "SELECT * FROM nav WHERE fund_id=? AND nav_date=? AND known_at=?", row[:3]
            ).fetchone()
            if existing is not None:
                if existing != row:
                    raise ValueError(f"历史版本冲突，已回滚本次导入：{row[:3]}")
                continue
            conn.execute("INSERT INTO nav VALUES (?,?,?,?,?,?)", row)
            added += 1
    return {"rows": len(frame), "inserted": added, "unchanged": len(frame) - added}


def write_funds(funds, path):
    Path(path).write_text(
        json.dumps([asdict(f) for f in funds], ensure_ascii=False, indent=2), encoding="utf-8"
    )
