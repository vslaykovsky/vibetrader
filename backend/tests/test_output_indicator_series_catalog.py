import pytest
from pydantic import ValidationError

from strategies_v2.utils import (
    OutputIndicatorSeriesCatalog,
    OutputIndicatorSeriesCatalogEntry,
    OutputTickerSubscription,
    StrategyOutput,
)


def test_strategy_output_accepts_indicator_series_catalog_with_subscriptions():
    startup = StrategyOutput(
        [
            OutputTickerSubscription(id="price", ticker="SPY", scale="1d"),
            OutputIndicatorSeriesCatalog(
                series=[
                    OutputIndicatorSeriesCatalogEntry(
                        name="sig", description="Custom signal for chart help"
                    )
                ]
            ),
        ]
    )
    again = StrategyOutput.model_validate_json(startup.model_dump_json())
    assert len(again.root) == 2
    assert again.root[1].kind == "indicator_series_catalog"
    assert again.root[1].series[0].name == "sig"


def test_output_indicator_series_catalog_rejects_duplicate_names():
    with pytest.raises(ValidationError):
        OutputIndicatorSeriesCatalog(
            series=[
                OutputIndicatorSeriesCatalogEntry(name="x", description="a"),
                OutputIndicatorSeriesCatalogEntry(name="x", description="b"),
            ]
        )


def test_ticker_subscription_accepts_session_values():
    default_sub = OutputTickerSubscription(id="price", ticker="SPY", scale="1h")
    regular_sub = OutputTickerSubscription(
        id="regular_price", ticker="SPY", scale="1h", session="regular"
    )

    assert default_sub.session == "all"
    assert regular_sub.session == "regular"
    with pytest.raises(ValidationError):
        OutputTickerSubscription(id="bad", ticker="SPY", scale="1h", session="overnight")
