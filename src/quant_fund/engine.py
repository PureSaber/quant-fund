"""Daily, close-decision, explicit-calendar OTC subscription/redemption ledger."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd

from .data import Dataset, day
from .research import allocate


@dataclass
class Lot:
    fund_id: str
    bought: pd.Timestamp
    shares: float
    reserved: float = 0.0


@dataclass
class Order:
    order_id: int
    fund_id: str
    side: str
    submitted: pd.Timestamp
    deal_date: pd.Timestamp
    confirm_date: pd.Timestamp | None
    settle_date: pd.Timestamp | None
    amount: float = 0.0
    shares: float = 0.0
    status: str = "pending"
    lots: list = field(default_factory=list, repr=False)


class Ledger:
    def __init__(self, dataset: Dataset, initial_cash=1_000_000.0):
        if not np.isfinite(initial_cash) or initial_cash <= 0:
            raise ValueError("初始资金必须为有限正数")
        if any(f.currency != "CNY" for f in dataset.funds.values()):
            raise ValueError("第一版账本只支持CNY；外币基金须提供经验证的汇率与换汇账本后接入")
        self.dataset = dataset
        if any(f.kind == "etf" for f in dataset.funds.values()):
            raise ValueError("ETF可用于收益研究；交易回测需另接交易价格、分红和成交成本模型")
        self.initial_cash = initial_cash
        self.cash = initial_cash
        self.frozen = 0.0
        self.receivables = []
        self.lots: list[Lot] = []
        self.orders: list[Order] = []
        self.trades = []
        self.history = []
        self.current_date = None
        self.dividends_processed = set()

    def offset(self, date, lag, purpose=None):
        if purpose is not None and self.dataset.purpose_calendars is not None:
            calendar, at = self.dataset.purpose_calendar(purpose, date, self.current_date or date)
            return calendar.advance(date, lag, at=at, purpose=purpose)
        calendar = self.dataset.account_calendar
        index = calendar.searchsorted(day(date)) + lag
        return calendar[index] if index < len(calendar) else None

    def dealing_date(self, code, submitted, *, not_before=None):
        fund = self.dataset.funds[code]
        earliest = max(
            day(submitted) + pd.Timedelta(days=1),
            day(submitted) + pd.Timedelta(days=fund.notice_days),
        )
        if not_before is not None:
            earliest = max(earliest, day(not_before))
        candidates = self.dataset.calendar[self.dataset.calendar >= earliest]
        if self.dataset.purpose_calendars is not None:
            calendar, at = self.dataset.purpose_calendar("dealing", earliest, submitted)
            candidates = candidates.intersection(pd.DatetimeIndex(calendar.open_days))
        if fund.open_dates:
            candidates = candidates.intersection(pd.DatetimeIndex(fund.open_dates))
        if fund.end_date:
            candidates = candidates[candidates < day(fund.end_date)]
        return candidates[0] if len(candidates) else None

    def submit(self, code, side, *, amount=0.0, shares=0.0):
        if self.current_date is None:
            raise ValueError("必须先推进到决策日")
        fund = self.dataset.funds[code]
        if side not in {"BUY", "SELL"}:
            raise ValueError("指令方向必须为BUY或SELL")
        if not np.isfinite([amount, shares]).all():
            raise ValueError("指令数量无效")
        if not fund.eligible(self.current_date) and side == "BUY":
            raise ValueError("当前日期基金不在可申购范围")
        deal = self.dealing_date(code, self.current_date)
        if deal is None:
            raise ValueError("日历范围内没有下一开放日")
        quote = self.dataset.quote(code, self.current_date)
        if quote is None or (self.current_date - quote.nav_date).days > fund.max_stale_days:
            raise ValueError("最新已知净值缺失或过期")
        # Resolve calendars before reserving money/shares; any failure is mutation-free.
        confirmation = self.offset(deal, fund.confirm_lag, "confirmation")
        settlement = self.offset(deal, fund.settle_lag, "banking")
        if settlement is not None and confirmation is not None and settlement < confirmation:
            raise ValueError("资金到账日不能早于份额确认日")
        reserved = []
        if side == "BUY":
            if amount <= 0 or amount < fund.min_buy or amount > self.cash + 1e-8 or shares != 0:
                raise ValueError("申购金额、最低申购额或可用资金不满足要求")
            self.cash -= amount
            self.frozen += amount
        else:
            if shares <= 0 or amount != 0:
                raise ValueError("赎回份额必须为正且申购金额为0")
            remaining = shares
            for lot in self.lots:
                if lot.fund_id != code or (deal - lot.bought).days < fund.lock_days:
                    continue
                take = min(remaining, lot.shares - lot.reserved)
                if take > 1e-10:
                    reserved.append((lot, take))
                    remaining -= take
            if remaining > 1e-8:
                raise ValueError("已解锁且未冻结的份额不足")
            for lot, take in reserved:
                lot.reserved += take
        order = Order(
            len(self.orders) + 1,
            code,
            side,
            self.current_date,
            deal,
            confirmation,
            settlement,
            amount,
            shares,
            lots=reserved,
        )
        self.orders.append(order)
        self.check()
        return order

    def _settle(self, date):
        unpaid = []
        for item in self.receivables:
            bank_open = True
            if self.dataset.purpose_calendars is not None:
                calendar, at = self.dataset.purpose_calendar("banking", date, date)
                bank_open = calendar.is_open(date, at=at, purpose="banking")
            if item["due"] is not None and item["due"] <= date and bank_open:
                self.cash += item["amount"]
            else:
                unpaid.append(item)
        self.receivables = unpaid

    @contextmanager
    def _advance_transaction(self):
        balances = self.current_date, self.cash, self.frozen
        lots, orders = list(self.lots), list(self.orders)
        records = [(record, vars(record).copy()) for record in lots + orders]
        receivables = self.receivables
        receivable_items = list(receivables)
        dividends = set(self.dividends_processed)
        # Daily processing only appends trades/history; do not copy the growing
        # history or the read-only market dataset for each trading day.
        trade_count, history_count = len(self.trades), len(self.history)
        try:
            yield
        except BaseException:
            self.current_date, self.cash, self.frozen = balances
            # Restore records in place: submitted orders retain references to
            # their reserved lots, and callers may hold either object.
            for record, values in records:
                vars(record).clear()
                vars(record).update(values)
            self.lots[:] = lots
            self.orders[:] = orders
            receivables[:] = receivable_items
            self.receivables = receivables
            self.dividends_processed.clear()
            self.dividends_processed.update(dividends)
            del self.trades[trade_count:]
            del self.history[history_count:]
            raise

    def advance(self, date):
        date = day(date)
        if date not in self.dataset.account_calendar:
            raise ValueError("日期不在显式交易日历中")
        if self.current_date is not None:
            expected = self.offset(self.current_date, 1)
            if date != expected:
                raise ValueError("账本必须按交易日历逐日推进，不能跳过事件")
        with self._advance_transaction():
            self._advance_day(date)

    def _advance_day(self, date):
        self.current_date = date
        self._settle(date)
        for order in self.orders:
            if order.status != "pending" or order.confirm_date is None or date < order.confirm_date:
                continue
            if self.dataset.purpose_calendars is not None:
                calendar, at = self.dataset.purpose_calendar("confirmation", date, date)
                if not calendar.is_open(date, at=at, purpose="confirmation"):
                    continue
            quote = self.dataset.quote(order.fund_id, date, exact_date=order.deal_date)
            if quote is None:
                continue  # Exact dealing NAV is required; never execute at a stale carried price.
            nav = float(quote.unit_nav)
            fund = self.dataset.funds[order.fund_id]
            if order.side == "BUY":
                net = order.amount / (1 + fund.buy_fee)
                fee = order.amount - net
                shares = net / nav
                self.frozen -= order.amount
                self.lots.append(Lot(order.fund_id, order.deal_date, shares))
                gross = order.amount
            else:
                shares = order.shares
                fee = sum(
                    take * nav * fund.sell_rate((order.deal_date - lot.bought).days)
                    for lot, take in order.lots
                )
                gross = shares * nav
                for lot, take in order.lots:
                    lot.shares -= take
                    lot.reserved -= take
                self.receivables.append(
                    {
                        "amount": gross - fee,
                        "due": order.settle_date,
                        "kind": "redemption",
                        "fund_id": order.fund_id,
                    }
                )
            order.status = "confirmed"
            self.trades.append(
                {
                    "order_id": order.order_id,
                    "fund_id": order.fund_id,
                    "side": order.side,
                    "submitted": order.submitted,
                    "deal_date": order.deal_date,
                    "confirmed": date,
                    "settle_date": order.settle_date,
                    "unit_nav": nav,
                    "shares": shares,
                    "gross": gross,
                    "fee": fee,
                }
            )
        self._dividends(date)
        # Covers same-day arrival and NAV that was disclosed after its expected settlement date.
        self._settle(date)
        self.check()
        self.history.append(self.snapshot())

    def _dividends(self, date):
        events = self.dataset.distributions
        for row in events.loc[events.ex_date == date].itertuples():
            key = (row.fund_id, row.ex_date)
            if key in self.dividends_processed:
                continue
            if any(
                o.status == "pending" and o.fund_id == row.fund_id and o.deal_date < date
                for o in self.orders
            ):
                raise ValueError("除息日存在此前未确认申赎，需提供登记日权益后再模拟")
            entitled = sum(
                lot.shares for lot in self.lots if lot.fund_id == row.fund_id and lot.bought < date
            )
            # Shares sold at today's ex-dividend NAV still owned the prior day's entitlement.
            entitled += sum(
                trade["shares"]
                for trade in self.trades
                if trade["fund_id"] == row.fund_id
                and trade["side"] == "SELL"
                and trade["deal_date"] == date
                and trade["confirmed"] == date
            )
            if entitled > 0:
                self.receivables.append(
                    {
                        "amount": entitled * row.cash_per_share,
                        "due": row.pay_date,
                        "kind": "dividend",
                        "fund_id": row.fund_id,
                    }
                )
            self.dividends_processed.add(key)

    def holdings(self):
        rows = []
        for code, fund in self.dataset.funds.items():
            shares = sum(lot.shares for lot in self.lots if lot.fund_id == code)
            if shares <= 1e-10:
                continue
            quote = self.dataset.quote(code, self.current_date)
            if quote is None:
                raise ValueError(f"持仓{code}缺少估值")
            # Remove announced ex-dividend cash from a still cum-dividend stale valuation.
            events = self.dataset.distributions
            adjustments = events.loc[
                (events.fund_id == code)
                & (events.ex_date > quote.nav_date)
                & (events.ex_date <= self.current_date)
                & (events.known_at <= self.current_date),
                "cash_per_share",
            ].sum()
            mark = float(quote.unit_nav - adjustments)
            if mark <= 0:
                raise ValueError("除息调整后的估值非正，需核对分红与单位净值")
            rows.append(
                {
                    "fund_id": code,
                    "name": fund.name,
                    "manager": fund.manager,
                    "strategy": fund.strategy,
                    "kind": fund.kind,
                    "shares": shares,
                    "reserved_shares": sum(
                        lot.reserved for lot in self.lots if lot.fund_id == code
                    ),
                    "mark": mark,
                    "value": shares * mark,
                    "nav_date": quote.nav_date,
                    "stale_days": (self.current_date - quote.nav_date).days,
                }
            )
        return pd.DataFrame(rows)

    def snapshot(self):
        holdings = self.holdings()
        value = holdings.value.sum() if not holdings.empty else 0.0
        transit = sum(item["amount"] for item in self.receivables)
        total = self.cash + self.frozen + transit + value
        return {
            "date": self.current_date,
            "cash": self.cash,
            "frozen_cash": self.frozen,
            "receivables": transit,
            "holdings_value": value,
            "total_value": total,
            "nav": total / self.initial_cash,
            "pending_orders": sum(o.status == "pending" for o in self.orders),
        }

    def check(self):
        if not np.isfinite([self.cash, self.frozen]).all() or min(self.cash, self.frozen) < -1e-7:
            raise ValueError("现金账本异常")
        for lot in self.lots:
            if (
                not np.isfinite([lot.shares, lot.reserved]).all()
                or lot.reserved < -1e-8
                or lot.shares < lot.reserved - 1e-8
            ):
                raise ValueError("份额账本异常")
        pending_amount = sum(
            o.amount for o in self.orders if o.status == "pending" and o.side == "BUY"
        )
        if not np.isclose(self.frozen, pending_amount, atol=1e-6, rtol=0):
            raise ValueError("冻结资金与待确认申购无法对账")

    def orders_frame(self):
        return pd.DataFrame(
            [{k: v for k, v in asdict(o).items() if k != "lots"} for o in self.orders]
        )


@dataclass(frozen=True)
class BacktestConfig:
    start: str = "2024-01-02"
    end: str = "2026-08-31"
    strategy: str = "optimal_risk"
    initial_cash: float = 1_000_000.0
    frequency: str = "ME"
    lookback: int = 24
    min_periods: int = 12
    max_weight: float = 0.4
    cash_buffer: float = 0.05
    min_trade: float = 1000.0

    def __post_init__(self):
        if day(self.start) > day(self.end):
            raise ValueError("开始日期晚于结束日期")
        if self.min_periods < 3 or self.lookback < self.min_periods:
            raise ValueError("历史窗口至少覆盖3期且不小于最低样本数")
        if not np.isfinite(self.min_trade) or self.min_trade <= 0:
            raise ValueError("最小调仓金额必须为有限正数")


def run_backtest(dataset: Dataset, config: BacktestConfig):
    ledger = Ledger(dataset, config.initial_cash)
    dates = dataset.account_calendar[
        (dataset.account_calendar >= day(config.start))
        & (dataset.account_calendar <= day(config.end))
    ]
    if len(dates) < 2:
        raise ValueError("回测区间至少需要两个交易日")
    # First session of each month: selection uses information known at that close.
    dealing_days = dates.intersection(dataset.calendar)
    month = dealing_days.to_period("M")
    decision_dates = set(dealing_days[~month.duplicated()])
    decisions, targets, events = [], [], []
    for date in dates:
        ledger.advance(date)
        if date not in decision_dates:
            continue
        try:
            weights, info = allocate(
                dataset,
                date,
                config.strategy,
                frequency=config.frequency,
                window=config.lookback,
                min_periods=config.min_periods,
                max_weight=config.max_weight,
                cash_buffer=config.cash_buffer,
            )
        except ValueError as error:
            decisions.append({"date": date, "status": "blocked", "reason": str(error)})
            continue
        decisions.append({"date": date, "status": "allocated", "reason": "", **info})
        targets.extend(
            {"date": date, "fund_id": code, "weight": float(weight)}
            for code, weight in weights.items()
        )
        snapshot = ledger.snapshot()
        holdings = ledger.holdings()
        current = holdings.set_index("fund_id") if not holdings.empty else pd.DataFrame()
        pending = {o.fund_id for o in ledger.orders if o.status == "pending"}
        desired = {
            code: weights.get(code, 0) * snapshot["total_value"]
            for code in sorted(set(weights.index) | set(current.index))
        }
        buys = {}
        for code, target in desired.items():
            if code in pending:
                events.append({"date": date, "fund_id": code, "reason": "已有待确认指令"})
                continue
            actual = float(current.loc[code, "value"]) if code in current.index else 0.0
            delta = target - actual
            if delta < -config.min_trade:
                try:
                    next_deal = ledger.dealing_date(code, date)
                    if next_deal is None:
                        raise ValueError("日历范围内没有赎回开放日")
                    fund = dataset.funds[code]
                    available = sum(
                        lot.shares - lot.reserved
                        for lot in ledger.lots
                        if lot.fund_id == code and (next_deal - lot.bought).days >= fund.lock_days
                    )
                    quantity = min(-delta / current.loc[code, "mark"], available)
                    if quantity <= 1e-10:
                        raise ValueError("持仓仍处于锁定期")
                    ledger.submit(code, "SELL", shares=quantity)
                except ValueError as error:
                    events.append({"date": date, "fund_id": code, "reason": str(error)})
            elif delta >= max(config.min_trade, dataset.funds[code].min_buy):
                buys[code] = delta
        # Do not spend anticipated sale proceeds. Scale buys against already available cash.
        spendable = max(0, ledger.cash - config.cash_buffer * snapshot["total_value"])
        scale = min(1, spendable / sum(buys.values())) if buys else 0
        for code, amount in buys.items():
            amount *= scale
            if amount < max(config.min_trade, dataset.funds[code].min_buy):
                continue
            try:
                ledger.submit(code, "BUY", amount=amount)
            except ValueError as error:
                events.append({"date": date, "fund_id": code, "reason": str(error)})
        ledger.history[-1] = ledger.snapshot()
    return {
        "ledger": ledger,
        "nav": pd.DataFrame(ledger.history).set_index("date"),
        "trades": pd.DataFrame(ledger.trades),
        "orders": ledger.orders_frame(),
        "decisions": pd.DataFrame(decisions),
        "targets": pd.DataFrame(targets),
        "events": pd.DataFrame(events),
        "config": asdict(config),
    }
