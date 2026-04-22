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
class Portfolio:
    """Synthetic single-ticker book: long-only, market fills at given price."""

    initial_deposit: float
    ticker: str
    cash: float = field(init=False)
    position_qty: float = field(init=False, default=0.0)
    avg_entry_price: float | None = field(init=False, default=None)
    realized_pnl: float = field(init=False, default=0.0)
    trades: list[Trade] = field(default_factory=list)
    equity_points: list[tuple[int, float]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.initial_deposit <= 0:
            raise ValueError("initial_deposit must be positive")
        self.cash = float(self.initial_deposit)

    def equity(self, mark_price: float) -> float:
        return self.cash + self.position_qty * mark_price

    def apply_market_order(
        self,
        *,
        direction: str,
        deposit_ratio: float,
        price: float,
        unixtime: int,
        reason: str = "",
    ) -> None:
        d = direction.lower().strip()
        dr = float(deposit_ratio)
        if dr <= 0 or dr > 1:
            raise ValueError("deposit_ratio must be in (0, 1]")
        if price <= 0:
            raise ValueError("price must be positive")
        if d == "buy":
            spend = self.cash * dr
            if spend <= 0:
                return
            qty = spend / price
            if self.position_qty <= 0:
                self.avg_entry_price = price
            else:
                assert self.avg_entry_price is not None
                total_qty = self.position_qty + qty
                self.avg_entry_price = (
                    self.avg_entry_price * self.position_qty + price * qty
                ) / total_qty
            self.position_qty += qty
            self.cash -= spend
            self.trades.append(
                Trade(
                    unixtime=unixtime,
                    ticker=self.ticker,
                    direction="buy",
                    price=price,
                    qty=qty,
                    deposit_ratio=dr,
                    reason=reason,
                )
            )
        elif d == "sell":
            if self.position_qty <= 0 or self.avg_entry_price is None:
                return
            qty = self.position_qty * dr
            proceeds = qty * price
            pnl_leg = qty * (price - self.avg_entry_price)
            self.realized_pnl += pnl_leg
            self.cash += proceeds
            self.position_qty -= qty
            if self.position_qty <= 1e-12:
                self.position_qty = 0.0
                self.avg_entry_price = None
            self.trades.append(
                Trade(
                    unixtime=unixtime,
                    ticker=self.ticker,
                    direction="sell",
                    price=price,
                    qty=qty,
                    deposit_ratio=dr,
                    reason=reason,
                )
            )
        else:
            raise ValueError(f"Unsupported direction: {direction!r}")

    def record_equity(self, unixtime: int, mark_price: float) -> None:
        self.equity_points.append((unixtime, self.equity(mark_price)))

    def to_portfolio_datapoint(self) -> InputPortfolioDataPoint:
        positions: list[PortfolioPosition] = []
        if self.position_qty > 1e-12:
            assert self.avg_entry_price is not None
            positions.append(
                PortfolioPosition(
                    ticker=self.ticker,
                    order_type="long",
                    deposit_ratio=1.0,
                    volume_weighted_avg_entry_price=float(self.avg_entry_price),
                )
            )
        return InputPortfolioDataPoint(kind="portfolio", positions=positions)
