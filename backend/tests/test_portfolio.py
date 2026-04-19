import pytest

from application.services.portfolio import Portfolio
from strategies_v2.utils import InputPortfolioDataPoint


def test_portfolio_buy_uses_deposit_fraction_of_cash():
    p = Portfolio(initial_deposit=10_000.0, ticker="SPY")
    p.apply_market_order(direction="buy", deposit_ratio=0.5, price=100.0, unixtime=1)
    assert p.cash == pytest.approx(5000.0)
    assert p.position_qty == pytest.approx(50.0)
    assert p.avg_entry_price == pytest.approx(100.0)
    assert p.equity(100.0) == pytest.approx(10_000.0)


def test_portfolio_sell_partial_realized_pnl():
    p = Portfolio(initial_deposit=10_000.0, ticker="SPY")
    p.apply_market_order(direction="buy", deposit_ratio=1.0, price=100.0, unixtime=1)
    p.apply_market_order(direction="sell", deposit_ratio=0.5, price=110.0, unixtime=2)
    assert p.position_qty == pytest.approx(50.0)
    assert p.realized_pnl == pytest.approx(500.0)
    assert len(p.trades) == 2


def test_portfolio_sell_all_clears_position():
    p = Portfolio(initial_deposit=10_000.0, ticker="SPY")
    p.apply_market_order(direction="buy", deposit_ratio=1.0, price=100.0, unixtime=1)
    p.apply_market_order(direction="sell", deposit_ratio=1.0, price=100.0, unixtime=2)
    assert p.position_qty == 0.0
    assert p.avg_entry_price is None


def test_portfolio_record_equity():
    p = Portfolio(initial_deposit=10_000.0, ticker="SPY")
    p.apply_market_order(direction="buy", deposit_ratio=1.0, price=100.0, unixtime=1)
    p.record_equity(10, 120.0)
    ut, eq = p.equity_points[-1]
    assert ut == 10
    assert eq == pytest.approx(12_000.0)


def test_portfolio_rejects_bad_deposit():
    p = Portfolio(initial_deposit=100.0, ticker="X")
    with pytest.raises(ValueError):
        p.apply_market_order(direction="buy", deposit_ratio=0.0, price=1.0, unixtime=1)


def test_portfolio_rejects_nonpositive_deposit_init():
    with pytest.raises(ValueError):
        Portfolio(initial_deposit=0.0, ticker="X")


def test_portfolio_to_portfolio_datapoint_flat():
    p = Portfolio(initial_deposit=100.0, ticker="SPY")
    pt = p.to_portfolio_datapoint()
    assert isinstance(pt, InputPortfolioDataPoint)
    assert pt.positions == []


def test_portfolio_to_portfolio_datapoint_in_position():
    p = Portfolio(initial_deposit=10_000.0, ticker="SPY")
    p.apply_market_order(direction="buy", deposit_ratio=1.0, price=100.0, unixtime=1)
    pt = p.to_portfolio_datapoint()
    assert len(pt.positions) == 1
    assert pt.positions[0].ticker == "SPY"
    assert pt.positions[0].order_type == "long"
