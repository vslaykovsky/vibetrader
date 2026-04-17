import json

from strategies import utils


def test_serialize_data_json():
    doc = utils.DataJson(
        strategy_name="Example",
        charts=[
            utils.LightweightChartsChart(
                title="Price and EMA(50)",
                series=[
                    utils.LwcCandlestickSeries(
                        label="Price",
                        options={"upColor": "#26a69a", "downColor": "#ef5350"},
                        data=[
                            utils.LwcCandlestickPoint(
                                time="2024-01-02",
                                open=100.0,
                                high=105.0,
                                low=99.0,
                                close=103.0,
                            )
                        ],
                        markers=[
                            utils.LwcMarker(
                                time="2024-01-15",
                                position="belowBar",
                                color="#26a69a",
                                shape="arrowUp",
                                text="BUY",
                            )
                        ],
                    ),
                    utils.LwcTimeValueSeries(
                        type="Line",
                        label="EMA 50",
                        options={"color": "#f6c90e", "lineWidth": 2},
                        data=[utils.LwcTimeValuePoint(time="2024-01-02", value=101.5)],
                    ),
                ],
            ),
            utils.PlotlyChart(
                title="PnL Distribution",
                data=[{"type": "histogram", "x": [1.2, -0.5, 3.1], "marker": {"color": "#26a69a"}}],
                layout={"xaxis": {"title": "Return %"}, "yaxis": {"title": "Count"}},
            ),
        ],
        table=[
            {"Ticker": "AAPL", "Close": 189.5, "SMA50": 185.2, "Distance_pct": 2.32, "Side": "above"},
            {"Ticker": "MSFT", "Close": 410.3, "SMA50": 412.1, "Distance_pct": -0.44, "Side": "below"},
        ],
        metrics=utils.Metrics(
            total_return=12.5,
            sharpe_ratio=1.234,
            max_drawdown=-8.2,
            win_rate=62.5,
            num_trades=47,
            final_equity=112500.0,
        ),
    )
    expected = {
        "strategy_name": "Example",
        "charts": [
            {
                "type": "lightweight-charts",
                "title": "Price and EMA(50)",
                "series": [
                    {
                        "type": "Candlestick",
                        "label": "Price",
                        "options": {"upColor": "#26a69a", "downColor": "#ef5350"},
                        "data": [
                            {"time": "2024-01-02", "open": 100.0, "high": 105.0, "low": 99.0, "close": 103.0}
                        ],
                        "markers": [
                            {
                                "time": "2024-01-15",
                                "position": "belowBar",
                                "color": "#26a69a",
                                "shape": "arrowUp",
                                "text": "BUY",
                            }
                        ],
                    },
                    {
                        "type": "Line",
                        "label": "EMA 50",
                        "options": {"color": "#f6c90e", "lineWidth": 2},
                        "data": [{"time": "2024-01-02", "value": 101.5}],
                    },
                ],
            },
            {
                "type": "plotly",
                "title": "PnL Distribution",
                "data": [{"type": "histogram", "x": [1.2, -0.5, 3.1], "marker": {"color": "#26a69a"}}],
                "layout": {"xaxis": {"title": "Return %"}, "yaxis": {"title": "Count"}},
            },
        ],
        "table": [
            {"Ticker": "AAPL", "Close": 189.5, "SMA50": 185.2, "Distance_pct": 2.32, "Side": "above"},
            {"Ticker": "MSFT", "Close": 410.3, "SMA50": 412.1, "Distance_pct": -0.44, "Side": "below"},
        ],
        "metrics": {
            "total_return": 12.5,
            "sharpe_ratio": 1.234,
            "max_drawdown": -8.2,
            "win_rate": 62.5,
            "num_trades": 47,
            "final_equity": 112500.0,
        },
    }
    assert utils.serialize_data_json(doc) == expected


def test_save_data_json(tmp_path):
    path = tmp_path / "backtest.json"
    utils.save_data_json(utils.DataJson(strategy_name="T", charts=[], table=[]), path=path)
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded == {"strategy_name": "T", "charts": [], "table": []}
