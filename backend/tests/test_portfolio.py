import pytest

from application.services.portfolio import Portfolio
from strategies_v2.utils import InputPortfolioDataPoint, OutputMarketTradeOrder


def test_portfolio_buy_uses_deposit_fraction_of_cash():
    p = Portfolio(initial_deposit=10_000.0, ticker="SPY")
    p.apply_market_order(direction="buy", deposit_ratio=0.5, price=100.0, unixtime=1)
    assert p.cash == pytest.approx(5000.0)
    assert p.position_qty == pytest.approx(50.0)
    assert p.avg_entry_price == pytest.approx(100.0)
    assert p.equity(100.0) == pytest.approx(10_000.0)


def test_portfolio_apply_market_orders_uses_batch_cash_for_buys():
    p = Portfolio(initial_deposit=10_000.0, ticker="SPY")
    p.apply_market_orders(
        [
            OutputMarketTradeOrder(
                ticker="SPY",
                direction="buy",
                deposit_ratio=0.25,
                short_explanation="momentum breakout",
            ),
            OutputMarketTradeOrder(ticker="AAPL", direction="buy", deposit_ratio=0.25),
        ],
        prices={"SPY": 100.0, "AAPL": 50.0},
        unixtime=1,
    )
    assert p.cash == pytest.approx(5000.0)
    assert p.position_qty == pytest.approx(25.0)
    assert p.positions["AAPL"].qty == pytest.approx(50.0)
    assert p.trades[0].reason == "momentum breakout"
    assert p.equity({"SPY": 100.0, "AAPL": 50.0}) == pytest.approx(10_000.0)

    p2 = Portfolio(initial_deposit=10_000.0, ticker="SPY")
    p2.apply_market_orders(
        [
            OutputMarketTradeOrder(ticker="SPY", direction="buy", deposit_ratio=0.75),
            OutputMarketTradeOrder(
                ticker="AAPL",
                direction="buy",
                deposit_ratio=0.75,
                short_explanation="second entry",
            ),
        ],
        prices={"SPY": 100.0, "AAPL": 50.0},
        unixtime=1,
    )
    assert p2.cash == pytest.approx(10_000.0)
    assert p2.positions == {}
    assert [t.action for t in p2.trades] == ["invalid", "invalid"]
    assert [t.qty for t in p2.trades] == [pytest.approx(75.0), pytest.approx(150.0)]
    assert [t.position_before_order for t in p2.trades] == [pytest.approx(0.0), pytest.approx(0.0)]
    assert [t.position_after_order_filled for t in p2.trades] == [pytest.approx(0.0), pytest.approx(0.0)]
    assert [t.reason for t in p2.trades] == [
        "market_order buy batch exceeds available cash",
        "second entry: market_order buy batch exceeds available cash",
    ]

    p3 = Portfolio(initial_deposit=10_000.0, ticker="SPY")
    p3.apply_market_order(direction="sell", deposit_ratio=1.0, price=100.0, unixtime=1)
    p3.apply_market_orders(
        [
            OutputMarketTradeOrder(ticker="SPY", direction="buy", deposit_ratio=1.0),
            OutputMarketTradeOrder(ticker="SPY", direction="buy", deposit_ratio=1.0),
        ],
        prices={"SPY": 100.0},
        unixtime=2,
    )
    assert p3.cash == pytest.approx(0.0)
    assert p3.position_qty == pytest.approx(100.0)
    assert [t.action for t in p3.trades] == ["sell_short", "buy_to_cover", "buy"]
    assert [t.position_before_order for t in p3.trades] == [
        pytest.approx(0.0),
        pytest.approx(-100.0),
        pytest.approx(0.0),
    ]
    assert [t.position_after_order_filled for t in p3.trades] == [
        pytest.approx(-100.0),
        pytest.approx(0.0),
        pytest.approx(100.0),
    ]

    p4 = Portfolio(initial_deposit=10_000.0, ticker="SPY")
    p4.apply_market_order(direction="sell", deposit_ratio=1.0, price=100.0, unixtime=1)
    p4.apply_market_orders(
        [
            OutputMarketTradeOrder(ticker="AAPL", direction="buy", deposit_ratio=0.5),
            OutputMarketTradeOrder(ticker="MSFT", direction="buy", deposit_ratio=0.5),
        ],
        prices={"AAPL": 50.0, "MSFT": 25.0},
        unixtime=2,
    )
    assert p4.cash == pytest.approx(20_000.0)
    assert sorted(p4.positions) == ["SPY"]
    assert [t.action for t in p4.trades] == ["sell_short", "invalid", "invalid"]
    assert [t.reason for t in p4.trades] == [
        "",
        "max_leverage exceeded",
        "max_leverage exceeded",
    ]


def test_portfolio_max_leverage_prevents_margin_but_allows_opt_in_and_reductions():
    p = Portfolio(initial_deposit=10_000.0, ticker="SPY")
    p.apply_market_order(direction="sell", deposit_ratio=1.0, price=100.0, unixtime=1)
    p.apply_market_order(
        ticker="AAPL",
        direction="buy",
        deposit_ratio=1.0,
        price=50.0,
        unixtime=2,
    )
    assert p.cash == pytest.approx(20_000.0)
    assert sorted(p.positions) == ["SPY"]
    assert [t.action for t in p.trades] == ["sell_short", "invalid"]
    assert p.trades[-1].qty == pytest.approx(400.0)
    assert p.trades[-1].reason == "max_leverage exceeded"

    p.apply_market_order(direction="buy", deposit_ratio=1.0, price=100.0, unixtime=3)
    assert p.cash == pytest.approx(10_000.0)
    assert p.positions == {}
    assert [t.action for t in p.trades] == ["sell_short", "invalid", "buy_to_cover"]

    p2 = Portfolio(initial_deposit=10_000.0, ticker="SPY", max_leverage=3.0)
    p2.apply_market_order(direction="sell", deposit_ratio=1.0, price=100.0, unixtime=1)
    p2.apply_market_order(
        ticker="AAPL",
        direction="buy",
        deposit_ratio=1.0,
        price=50.0,
        unixtime=2,
    )
    assert p2.cash == pytest.approx(0.0)
    assert p2.positions["SPY"].qty == pytest.approx(-100.0)
    assert p2.positions["AAPL"].qty == pytest.approx(400.0)
    assert [t.action for t in p2.trades] == ["sell_short", "buy"]


def test_portfolio_sell_partial_realized_pnl():
    p = Portfolio(initial_deposit=10_000.0, ticker="SPY")
    p.apply_market_order(direction="buy", deposit_ratio=1.0, price=100.0, unixtime=1)
    p.apply_market_order(direction="sell", deposit_ratio=0.5, price=110.0, unixtime=2)
    assert p.position_qty == pytest.approx(50.0)
    assert p.realized_pnl == pytest.approx(500.0)
    assert len(p.trades) == 2
    assert [t.action for t in p.trades] == ["buy", "sell"]
    assert [t.label for t in p.trades] == ["BUY", "SELL"]


def test_portfolio_sell_all_clears_position():
    p = Portfolio(initial_deposit=10_000.0, ticker="SPY")
    p.apply_market_order(direction="buy", deposit_ratio=1.0, price=100.0, unixtime=1)
    p.apply_market_order(direction="sell", deposit_ratio=1.0, price=100.0, unixtime=2)
    assert p.position_qty == 0.0
    assert p.avg_entry_price is None
    p.apply_market_order(direction="sell", deposit_ratio=1.0, price=100.0, unixtime=3)
    assert p.position_qty == pytest.approx(-100.0)
    p.apply_market_order(direction="buy", deposit_ratio=1.0, price=90.0, unixtime=4)
    assert p.position_qty == 0.0
    assert p.avg_entry_price is None
    assert p.realized_pnl == pytest.approx(1000.0)
    assert [t.action for t in p.trades] == ["buy", "sell", "sell_short", "buy_to_cover"]
    assert [t.label for t in p.trades] == ["BUY", "SELL", "SELL SHORT", "BUY TO COVER"]


def test_portfolio_record_equity():
    p = Portfolio(initial_deposit=10_000.0, ticker="SPY")
    p.apply_market_order(direction="buy", deposit_ratio=1.0, price=100.0, unixtime=1)
    p.record_equity(10, 120.0)
    ut, eq = p.equity_points[-1]
    assert ut == 10
    assert eq == pytest.approx(12_000.0)


def test_portfolio_records_invalid_bad_deposit():
    p = Portfolio(initial_deposit=100.0, ticker="X")
    p.apply_market_order(
        direction="buy",
        deposit_ratio=0.0,
        price=1.0,
        unixtime=1,
        reason="bad size",
    )
    assert p.cash == pytest.approx(100.0)
    assert p.position_qty == 0.0
    assert len(p.trades) == 1
    assert p.trades[0].direction == "buy"
    assert p.trades[0].action == "invalid"
    assert p.trades[0].label == "INVALID"
    assert p.trades[0].valid is False
    assert p.trades[0].reason == "bad size: deposit_ratio must be in (0, 1]"
    assert p.trades[0].position_before_order == pytest.approx(0.0)
    assert p.trades[0].position_after_order_filled == pytest.approx(0.0)


def test_portfolio_rejects_nonpositive_deposit_init():
    with pytest.raises(ValueError):
        Portfolio(initial_deposit=0.0, ticker="X")
    with pytest.raises(ValueError):
        Portfolio(initial_deposit=100.0, ticker="X", max_leverage=0.0)


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
    assert pt.positions[0].volume_weighted_avg_entry_price == pytest.approx(100.0)
    p.apply_market_order(direction="sell", deposit_ratio=1.0, price=100.0, unixtime=2)
    p.apply_market_order(direction="sell", deposit_ratio=0.5, price=100.0, unixtime=3)
    pt = p.to_portfolio_datapoint()
    assert len(pt.positions) == 1
    assert pt.positions[0].ticker == "SPY"
    assert pt.positions[0].order_type == "short"
    assert pt.positions[0].volume_weighted_avg_entry_price == pytest.approx(100.0)
