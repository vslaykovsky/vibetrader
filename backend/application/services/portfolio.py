from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from strategies_v2.utils import InputPortfolioDataPoint, PortfolioPosition


@dataclass
class Trade:
    unixtime: int
    ticker: str
    direction: Literal["buy", "sell"]
    price: float
    qty: float
    deposit_ratio: float
    reason: str = ""


@dataclass
class Position:
    qty: float
    avg_entry_price: float


@dataclass
class Portfolio:
    initial_deposit: float
    ticker: str
    cash: float = field(init=False)
    positions: dict[str, Position] = field(default_factory=dict)
    realized_pnl: float = field(init=False, default=0.0)
    trades: list[Trade] = field(default_factory=list)
    equity_points: list[tuple[int, float]] = field(default_factory=list)
    last_marks: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.initial_deposit <= 0:
            raise ValueError("initial_deposit must be positive")
        self.cash = float(self.initial_deposit)
        self.ticker = str(self.ticker).strip()

    @property
    def position_qty(self) -> float:
        pos = self.positions.get(self.ticker)
        return 0.0 if pos is None else pos.qty

    @property
    def avg_entry_price(self) -> float | None:
        pos = self.positions.get(self.ticker)
        return None if pos is None else pos.avg_entry_price

    def equity(self, mark_price: float | dict[str, float]) -> float:
        if isinstance(mark_price, dict):
            marks = {str(k).strip(): float(v) for k, v in mark_price.items()}
        else:
            marks = {self.ticker: float(mark_price)}
        for t, px in marks.items():
            if px > 0:
                self.last_marks[t] = px
        total = self.cash
        for t, pos in self.positions.items():
            px = marks.get(t, self.last_marks.get(t, pos.avg_entry_price))
            total += pos.qty * px
        return total

    def apply_market_order(
        self,
        *,
        ticker: str | None = None,
        direction: str,
        deposit_ratio: float,
        price: float,
        unixtime: int,
        reason: str = "",
        cash_basis: float | None = None,
    ) -> None:
        t = str(ticker or self.ticker).strip()
        if not t:
            raise ValueError("ticker is required")
        d = direction.lower().strip()
        dr = float(deposit_ratio)
        if dr <= 0 or dr > 1:
            raise ValueError("deposit_ratio must be in (0, 1]")
        if price <= 0:
            raise ValueError("price must be positive")
        if d == "buy":
            basis = self.cash if cash_basis is None else float(cash_basis)
            spend = basis * dr
            if spend <= 0:
                return
            if spend > self.cash + 1e-9:
                raise ValueError("insufficient cash for market_order batch")
            qty = spend / price
            pos = self.positions.get(t)
            if pos is None or pos.qty <= 0:
                self.positions[t] = Position(qty=qty, avg_entry_price=price)
            else:
                total_qty = pos.qty + qty
                pos.avg_entry_price = (
                    pos.avg_entry_price * pos.qty + price * qty
                ) / total_qty
                pos.qty = total_qty
            self.cash -= spend
            self.trades.append(
                Trade(
                    unixtime=unixtime,
                    ticker=t,
                    direction="buy",
                    price=price,
                    qty=qty,
                    deposit_ratio=dr,
                    reason=reason,
                )
            )
        elif d == "sell":
            pos = self.positions.get(t)
            if pos is None or pos.qty <= 0:
                return
            qty = pos.qty * dr
            proceeds = qty * price
            pnl_leg = qty * (price - pos.avg_entry_price)
            self.realized_pnl += pnl_leg
            self.cash += proceeds
            pos.qty -= qty
            if pos.qty <= 1e-12:
                self.positions.pop(t, None)
            self.trades.append(
                Trade(
                    unixtime=unixtime,
                    ticker=t,
                    direction="sell",
                    price=price,
                    qty=qty,
                    deposit_ratio=dr,
                    reason=reason,
                )
            )
        else:
            raise ValueError(f"Unsupported direction: {direction!r}")

    def apply_market_orders(
        self,
        orders: list,
        *,
        prices: dict[str, float],
        unixtime: int,
        reason: str = "",
    ) -> None:
        sell_orders = []
        buy_orders = []
        for item in orders:
            d = str(item.direction).lower().strip()
            if d == "sell":
                sell_orders.append(item)
            elif d == "buy":
                buy_orders.append(item)
            else:
                raise ValueError(f"Unsupported direction: {item.direction!r}")

        for item in sell_orders:
            t = str(item.ticker).strip()
            px = prices.get(t)
            if px is None:
                raise ValueError(f"no fill price available for ticker {t!r}")
            self.apply_market_order(
                ticker=t,
                direction=item.direction,
                deposit_ratio=item.deposit_ratio,
                price=px,
                unixtime=unixtime,
                reason=reason,
            )

        batch_cash = self.cash
        total_spend = sum(float(item.deposit_ratio) * batch_cash for item in buy_orders)
        if total_spend > self.cash + 1e-9:
            raise ValueError("market_order buy batch exceeds available cash")
        for item in buy_orders:
            t = str(item.ticker).strip()
            px = prices.get(t)
            if px is None:
                raise ValueError(f"no fill price available for ticker {t!r}")
            self.apply_market_order(
                ticker=t,
                direction=item.direction,
                deposit_ratio=item.deposit_ratio,
                price=px,
                unixtime=unixtime,
                reason=reason,
                cash_basis=batch_cash,
            )

    def record_equity(self, unixtime: int, mark_price: float | dict[str, float]) -> None:
        self.equity_points.append((unixtime, self.equity(mark_price)))

    def to_portfolio_datapoint(self) -> InputPortfolioDataPoint:
        positions: list[PortfolioPosition] = []
        eq = self.equity(self.last_marks) if self.last_marks else self.initial_deposit
        denom = eq if eq > 0 else self.initial_deposit
        for t in sorted(self.positions):
            pos = self.positions[t]
            px = self.last_marks.get(t, pos.avg_entry_price)
            ratio = (pos.qty * px / denom) if denom > 0 else 0.0
            positions.append(
                PortfolioPosition(
                    ticker=t,
                    order_type="long",
                    deposit_ratio=max(0.0, min(1.0, float(ratio))),
                    volume_weighted_avg_entry_price=float(pos.avg_entry_price),
                )
            )
        return InputPortfolioDataPoint(kind="portfolio", positions=positions)
