from __future__ import annotations

import logging
import uuid
from typing import Any

from flask import Blueprint, g, jsonify, request

from auth import require_auth
from services.supabase_trading_settings import (
    delete_alpaca_account,
    fetch_trading_settings_payload,
    insert_alpaca_account,
    service_role_configured,
    update_alpaca_account,
    upsert_profile_alpaca_keys,
)

logger = logging.getLogger(__name__)

settings_blueprint = Blueprint("settings", __name__)


def _bad(message: str, code: int = 400) -> tuple:
    return jsonify({"error": message}), code


@settings_blueprint.get("/settings/trading")
@require_auth
def settings_trading_get() -> tuple:
    if not service_role_configured():
        return _bad("Trading settings are not configured on the server", 503)
    uid = str(g.user_id)
    payload = fetch_trading_settings_payload(uid)
    if payload is None:
        return _bad("Failed to load trading settings", 502)
    return jsonify(payload), 200


@settings_blueprint.put("/settings/trading/profile")
@require_auth
def settings_trading_profile_put() -> tuple:
    if not service_role_configured():
        return _bad("Trading settings are not configured on the server", 503)
    body = request.get_json(silent=True) or {}
    api_key = body.get("alpaca_api_key")
    secret_key = body.get("alpaca_secret_key")
    ak = None
    sk = None
    if "alpaca_api_key" in body:
        if not isinstance(api_key, str):
            return _bad("alpaca_api_key must be a string")
        ak = api_key
    if "alpaca_secret_key" in body:
        if not isinstance(secret_key, str):
            return _bad("alpaca_secret_key must be a string")
        sk = secret_key
    ok, err = upsert_profile_alpaca_keys(str(g.user_id), alpaca_api_key=ak, alpaca_secret_key=sk)
    if not ok:
        return _bad(err or "Save failed", 502)
    return jsonify({"ok": True}), 200


@settings_blueprint.post("/settings/trading/alpaca-accounts")
@require_auth
def settings_alpaca_accounts_post() -> tuple:
    if not service_role_configured():
        return _bad("Trading settings are not configured on the server", 503)
    body = request.get_json(silent=True) or {}
    account = str(body.get("account") or "")
    label = str(body.get("label") or "")
    is_live = bool(body.get("is_live"))
    row, err = insert_alpaca_account(str(g.user_id), account=account, label=label, is_live=is_live)
    if not row:
        return _bad(err or "Create failed", 502)
    return jsonify({"account": _serialize_account(row)}), 201


@settings_blueprint.patch("/settings/trading/alpaca-accounts/<account_id>")
@require_auth
def settings_alpaca_accounts_patch(account_id: str) -> tuple:
    if not service_role_configured():
        return _bad("Trading settings are not configured on the server", 503)
    aid = (account_id or "").strip()
    try:
        uuid.UUID(aid)
    except ValueError:
        return _bad("invalid account_id")
    body = request.get_json(silent=True) or {}
    ac = body.get("account")
    lab = body.get("label")
    live = body.get("is_live")
    ac_opt: str | None = None
    if "account" in body:
        if not isinstance(ac, str):
            return _bad("account must be a string")
        ac_opt = str(ac or "").strip()
    lab_opt = None
    if "label" in body:
        if not isinstance(lab, str):
            return _bad("label must be a string")
        lab_opt = str(lab or "").strip()
    live_opt: bool | None = None
    if "is_live" in body:
        if not isinstance(live, bool):
            return _bad("is_live must be a boolean")
        live_opt = live
    ok, err = update_alpaca_account(
        str(g.user_id),
        aid,
        account=ac_opt if "account" in body else None,
        label=lab_opt,
        is_live=live_opt,
    )
    if not ok:
        return _bad(err or "Update failed", 502)
    return jsonify({"ok": True}), 200


@settings_blueprint.delete("/settings/trading/alpaca-accounts/<account_id>")
@require_auth
def settings_alpaca_accounts_delete(account_id: str) -> tuple:
    if not service_role_configured():
        return _bad("Trading settings are not configured on the server", 503)
    aid = (account_id or "").strip()
    try:
        uuid.UUID(aid)
    except ValueError:
        return _bad("invalid account_id")
    ok, err = delete_alpaca_account(str(g.user_id), aid)
    if not ok:
        return _bad(err or "Delete failed", 502)
    return jsonify({"ok": True}), 200


def _serialize_account(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row.get("id") or ""),
        "account": str(row.get("account") or ""),
        "label": str(row.get("label") or ""),
        "is_live": bool(row.get("is_live")),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }
