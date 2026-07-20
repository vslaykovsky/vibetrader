from __future__ import annotations

import logging
import uuid
from typing import Any

from flask import Blueprint, g, jsonify, request

from auth import require_auth
from services.eula import (
    EULA_DOCUMENT,
    EULA_VERSION,
    fetch_eula_acceptance,
    normalize_client_ip,
    record_eula_acceptance,
)
from services.supabase_trading_settings import (
    delete_alpaca_account,
    fetch_trading_settings_payload,
    insert_alpaca_account,
    service_role_configured,
    update_alpaca_account,
    upsert_profile_settings,
)

logger = logging.getLogger(__name__)

settings_blueprint = Blueprint("settings", __name__)


def _bad(message: str, code: int = 400) -> tuple:
    return jsonify({"error": message}), code


def _no_store(response):
    response.headers["Cache-Control"] = "no-store"
    return response


@settings_blueprint.get("/eula")
@require_auth
def eula_get() -> tuple:
    if not service_role_configured():
        return _bad("EULA status is not configured on the server", 503)
    acceptance = fetch_eula_acceptance(str(g.actor_user_id))
    if acceptance is None:
        return _bad("Failed to load EULA status", 502)
    response = jsonify(
        {
            "agreement": EULA_DOCUMENT,
            "acceptance": acceptance.to_dict(),
        }
    )
    return _no_store(response), 200


@settings_blueprint.post("/eula/accept")
@require_auth
def eula_accept_post() -> tuple:
    if not service_role_configured():
        return _bad("EULA acceptance is not configured on the server", 503)
    body = request.get_json(silent=True) or {}
    if body.get("accepted") is not True:
        return _bad("accepted must be true")
    if body.get("age_confirmed") is not True:
        return _bad("age_confirmed must be true")
    if body.get("risk_acknowledged") is not True:
        return _bad("risk_acknowledged must be true")
    if str(body.get("version") or "").strip() != EULA_VERSION:
        return _bad("EULA version is no longer current", 409)

    forwarded_for = request.headers.get("X-Forwarded-For")
    client_ip = normalize_client_ip(forwarded_for) or normalize_client_ip(request.remote_addr)
    acceptance = record_eula_acceptance(
        str(g.actor_user_id),
        email=getattr(g, "actor_user_email", None),
        client_ip=client_ip,
        user_agent=request.headers.get("User-Agent"),
    )
    if acceptance is None:
        return _bad("Failed to record EULA acceptance", 502)
    response = jsonify({"ok": True, "acceptance": acceptance.to_dict()})
    return _no_store(response), 200


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
    tz = body.get("timezone")
    fmt = body.get("hour_format")
    adjust = body.get("adjust_for_dividends")
    lang = body.get("interface_language")
    user_timezone = None
    hour_format = None
    adjust_for_dividends = None
    interface_language = None
    if "timezone" in body:
        if not isinstance(tz, str):
            return _bad("timezone must be a string")
        user_timezone = tz
    if "hour_format" in body:
        if not isinstance(fmt, str):
            return _bad("hour_format must be a string")
        hour_format = fmt
    if "adjust_for_dividends" in body:
        if not isinstance(adjust, bool):
            return _bad("adjust_for_dividends must be a boolean")
        adjust_for_dividends = bool(adjust)
    if "interface_language" in body:
        if not isinstance(lang, str):
            return _bad("interface_language must be a string")
        interface_language = lang
    ok, err = upsert_profile_settings(
        str(g.user_id),
        user_timezone=user_timezone,
        hour_format=hour_format,
        adjust_for_dividends=adjust_for_dividends,
        interface_language=interface_language,
    )
    if not ok:
        return _bad(err or "Save failed", 502)
    return jsonify({"ok": True}), 200


@settings_blueprint.post("/settings/trading/alpaca-accounts")
@require_auth
def settings_alpaca_accounts_post() -> tuple:
    if not service_role_configured():
        return _bad("Trading settings are not configured on the server", 503)
    body = request.get_json(silent=True) or {}
    label = str(body.get("label") or "")
    if not label.strip():
        return _bad("label is required")
    api_key = body.get("alpaca_api_key")
    secret_key = body.get("alpaca_secret_key")
    if not isinstance(api_key, str):
        return _bad("alpaca_api_key must be a string")
    if not isinstance(secret_key, str):
        return _bad("alpaca_secret_key must be a string")
    row, err = insert_alpaca_account(
        str(g.user_id),
        label=label,
        alpaca_api_key=api_key,
        alpaca_secret_key=secret_key,
    )
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
    lab = body.get("label")
    api_key = body.get("alpaca_api_key")
    secret_key = body.get("alpaca_secret_key")
    lab_opt = None
    if "label" in body:
        if not isinstance(lab, str):
            return _bad("label must be a string")
        lab_opt = str(lab or "").strip()
    ak_opt = None
    if "alpaca_api_key" in body:
        if not isinstance(api_key, str):
            return _bad("alpaca_api_key must be a string")
        ak_opt = str(api_key or "").strip()
    sk_opt = None
    if "alpaca_secret_key" in body:
        if not isinstance(secret_key, str):
            return _bad("alpaca_secret_key must be a string")
        sk_opt = str(secret_key or "").strip()
    ok, err = update_alpaca_account(
        str(g.user_id),
        aid,
        label=lab_opt,
        alpaca_api_key=ak_opt,
        alpaca_secret_key=sk_opt,
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
    ak = str(row.get("alpaca_api_key") or "").strip()
    sk = str(row.get("alpaca_secret_key") or "").strip()
    return {
        "id": str(row.get("id") or ""),
        "label": str(row.get("label") or ""),
        "is_live": bool(row.get("is_live")),
        "has_alpaca_api_key": bool(ak),
        "has_alpaca_secret_key": bool(sk),
        "alpaca_api_key_hint": "****" + ak[-4:] if len(ak) > 4 else ("****" if ak else ""),
        "alpaca_secret_key_hint": "****" + sk[-4:] if len(sk) > 4 else ("****" if sk else ""),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }
