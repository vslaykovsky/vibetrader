import uuid

import pytest
from alpaca.common.exceptions import APIError
from alpaca.trading.enums import TimeInForce

from db.models import LiveRun, LiveRunOrder, Ticker
from db.session import SessionLocal, engine, init_database
from scripts.run_alpaca_strategy import (
    _add_rejection_comment,
    _alpaca_api_error_details,
    _maybe_execute_market_order,
    _portfolio_snapshot_from_alpaca,
    _time_in_force_for_symbol,
)
from strategies_v2.utils import OutputMarketTradeOrder


class FakeAccount:
    cash = "100000"
    equity = "101000"
    buying_power = "5000"


class FakePosition:
    symbol = "SPY"
    qty = "3"
    avg_entry_price = "400"


class FakePlacedOrder:
    id = "alpaca-order-1"


class FakeResponse:
    status_code = 403


class FakeHttpError:
    response = FakeResponse()


class FakeClient:
    def __init__(self):
        self.submitted = []

    def get_account(self):
        return FakeAccount()

    def get_all_positions(self):
        return [FakePosition()]

    def submit_order(self, req):
        self.submitted.append(req)
        return FakePlacedOrder()


def test_portfolio_snapshot_from_alpaca_includes_account_fields():
    pt = _portfolio_snapshot_from_alpaca(FakeClient())
    assert pt.model_dump(mode="json") == {
        "kind": "portfolio",
        "cash": 100000.0,
        "equity": 101000.0,
        "buying_power": 5000.0,
        "positions": [
            {
                "ticker": "SPY",
                "order_type": "long",
                "deposit_ratio": 1200.0 / 101000.0,
                "volume_weighted_avg_entry_price": 400.0,
            }
        ],
    }


def test_maybe_execute_market_order_caps_buy_notional_by_buying_power():
    init_database(engine)
    run_id = str(uuid.uuid4())
    client = FakeClient()
    with SessionLocal() as session:
        session.add(LiveRun(id=run_id, thread_id="thread", status="running"))
        session.commit()
        result = _maybe_execute_market_order(
            client,
            session=session,
            run_id=run_id,
            unixtime=1,
            order=OutputMarketTradeOrder(
                ticker="SPY",
                direction="buy",
                deposit_ratio=1.0,
            ),
            enable_trading=True,
        )
        session.commit()
        row = session.query(LiveRunOrder).filter_by(run_id=run_id).one()
    assert result == {
        "client_order_id": f"{run_id}:1:SPY:buy:1.000000",
        "alpaca_order_id": "alpaca-order-1",
        "status": "submitted",
        "notional": 5000.0,
        "cash": 100000.0,
        "buying_power": 5000.0,
    }
    assert row.alpaca_order_id == "alpaca-order-1"
    assert len(client.submitted) == 1
    assert float(client.submitted[0].notional) == pytest.approx(5000.0)


def test_alpaca_api_error_details_includes_code_message_and_http_status():
    exc = APIError(
        '{"code":"40310000","message":"insufficient buying power"}',
        http_error=FakeHttpError(),
    )
    assert _alpaca_api_error_details(exc) == {
        "error": "insufficient buying power",
        "alpaca_error_message": "insufficient buying power",
        "alpaca_error_code": "40310000",
        "alpaca_status_code": 403,
        "alpaca_error_raw": '{"code":"40310000","message":"insufficient buying power"}',
    }


def test_add_rejection_comment_appends_alpaca_error_to_short_explanation():
    payload = {
        "short_explanation": "Enter long",
        "alpaca_error_message": "invalid crypto time_in_force",
        "alpaca_error_code": "42210000",
        "alpaca_status_code": 422,
    }
    assert _add_rejection_comment(payload) == {
        "short_explanation": (
            "Enter long; Alpaca rejected order: invalid crypto time_in_force "
            "(code 42210000, HTTP 422)"
        ),
        "alpaca_error_message": "invalid crypto time_in_force",
        "alpaca_error_code": "42210000",
        "alpaca_status_code": 422,
    }


def test_time_in_force_for_symbol_uses_gtc_for_crypto_pairs():
    init_database(engine)
    with SessionLocal() as session:
        session.merge(Ticker(ticker="BTC/USD", provider="alpaca", tags=["crypto"]))
        session.merge(Ticker(ticker="SPY", provider="alpaca", tags=["stock"]))
        session.commit()

        assert _time_in_force_for_symbol("BTCUSD", session=session) == TimeInForce.GTC
        assert _time_in_force_for_symbol("SPY", session=session) == TimeInForce.DAY
