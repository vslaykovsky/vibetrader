from __future__ import annotations

import json
import logging
import os
import urllib.parse
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests

logger = logging.getLogger(__name__)

_ALPACA_ACCOUNT_ENDPOINTS: tuple[tuple[bool, str], ...] = (
    (False, "https://paper-api.alpaca.markets/v2/account"),
    (True, "https://api.alpaca.markets/v2/account"),
)


def mask_secret_tail(value: str, *, tail: int = 4) -> str:
    s = (value or "").strip()
    if not s:
        return ""
    if len(s) <= tail:
        return "****"
    return "****" + s[-tail:]


def _supabase_url() -> str:
    return (os.environ.get("SUPABASE_URL") or "").strip().rstrip("/")


def _service_role_key() -> str:
    return (os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()


def service_role_configured() -> bool:
    return bool(_supabase_url() and _service_role_key())


def normalize_timezone(value: str | None) -> str:
    tz = (value or "").strip()
    if not tz:
        return ""
    try:
        ZoneInfo(tz)
    except ZoneInfoNotFoundError:
        return ""
    return tz


def normalize_hour_format(value: str | None) -> str:
    fmt = (value or "").strip().lower()
    if fmt in {"auto", "12h", "24h"}:
        return fmt
    return ""


def normalize_adjust_for_dividends(value: Any) -> bool:
    return bool(value) if isinstance(value, bool) else False


def _headers() -> dict[str, str]:
    key = _service_role_key()
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _get(path: str, params: dict[str, str] | None = None) -> requests.Response:
    base = _supabase_url()
    q = ""
    if params:
        q = "?" + urllib.parse.urlencode(params)
    return requests.get(f"{base}/rest/v1/{path}{q}", headers=_headers(), timeout=15)


def _patch(path: str, body: dict[str, Any]) -> requests.Response:
    base = _supabase_url()
    return requests.patch(
        f"{base}/rest/v1/{path}",
        headers={**_headers(), "Prefer": "return=representation"},
        data=json.dumps(body),
        timeout=15,
    )


def _post(path: str, body: Any) -> requests.Response:
    base = _supabase_url()
    return requests.post(
        f"{base}/rest/v1/{path}",
        headers={**_headers(), "Prefer": "return=representation"},
        data=json.dumps(body),
        timeout=15,
    )


def _delete(path_qs: str) -> requests.Response:
    base = _supabase_url()
    return requests.delete(f"{base}/rest/v1/{path_qs}", headers=_headers(), timeout=15)


def _alpaca_account_endpoint_authenticates(
    url: str,
    *,
    alpaca_api_key: str,
    alpaca_secret_key: str,
) -> tuple[bool, int, str]:
    try:
        r = requests.get(
            url,
            headers={
                "APCA-API-KEY-ID": alpaca_api_key,
                "APCA-API-SECRET-KEY": alpaca_secret_key,
                "Accept": "application/json",
            },
            timeout=15,
        )
    except requests.RequestException as exc:
        return False, 0, str(exc)
    if r.status_code == 200:
        return True, r.status_code, ""
    return False, r.status_code, ""


def detect_alpaca_account_is_live(
    *,
    alpaca_api_key: str,
    alpaca_secret_key: str,
) -> tuple[bool | None, str]:
    ak = (alpaca_api_key or "").strip()
    sk = (alpaca_secret_key or "").strip()
    if not ak or not sk:
        return None, "Alpaca API key and secret are required"

    matches: list[bool] = []
    failures: list[str] = []
    for is_live, url in _ALPACA_ACCOUNT_ENDPOINTS:
        ok, status_code, err = _alpaca_account_endpoint_authenticates(
            url,
            alpaca_api_key=ak,
            alpaca_secret_key=sk,
        )
        if ok:
            matches.append(is_live)
            continue
        label = "live" if is_live else "paper"
        if status_code:
            failures.append(f"{label} HTTP {status_code}")
        elif err:
            failures.append(f"{label} request failed")

    if len(matches) == 1:
        return matches[0], ""
    if len(matches) > 1:
        return None, "Alpaca credentials authenticated against both paper and live APIs"
    detail = f" ({', '.join(failures)})" if failures else ""
    return None, f"Alpaca credentials did not authenticate against paper or live trading APIs{detail}"


def _account_select(*, include_credentials: bool = False) -> str:
    cols = "id,label,is_live,created_at,updated_at"
    if include_credentials:
        cols += ",alpaca_api_key,alpaca_secret_key"
    return cols


def fetch_alpaca_account_for_user(
    user_id: str,
    account_id: str,
    *,
    include_credentials: bool = False,
) -> dict[str, Any] | None:
    uid = (user_id or "").strip()
    aid = (account_id or "").strip()
    if not uid or not aid or not service_role_configured():
        return None
    r = _get(
        "alpaca_accounts",
        {
            "id": f"eq.{urllib.parse.quote(aid, safe='')}",
            "user_id": f"eq.{urllib.parse.quote(uid, safe='')}",
            "select": _account_select(include_credentials=include_credentials),
        },
    )
    if r.status_code != 200:
        logger.warning("supabase alpaca_accounts fetch status=%s", r.status_code)
        return None
    rows = r.json()
    if not isinstance(rows, list) or not rows:
        return None
    row = rows[0]
    return row if isinstance(row, dict) else None


def fetch_alpaca_accounts_for_user(
    user_id: str,
    account_ids: list[str],
) -> dict[str, dict[str, Any]]:
    uid = (user_id or "").strip()
    aids = [a.strip() for a in account_ids if (a or "").strip()]
    if not uid or not aids or not service_role_configured():
        return {}
    ids_csv = ",".join(urllib.parse.quote(a, safe="") for a in aids)
    r = _get(
        "alpaca_accounts",
        {
            "id": f"in.({ids_csv})",
            "user_id": f"eq.{urllib.parse.quote(uid, safe='')}",
            "select": _account_select(include_credentials=False),
        },
    )
    if r.status_code != 200:
        logger.warning("supabase alpaca_accounts bulk fetch status=%s", r.status_code)
        return {}
    rows = r.json()
    if not isinstance(rows, list):
        return {}
    return {row["id"]: row for row in rows if isinstance(row, dict) and "id" in row}


def fetch_trading_settings_payload(user_id: str) -> dict[str, Any] | None:
    uid = (user_id or "").strip()
    if not uid or not service_role_configured():
        return None
    pr = _get(
        "profiles",
        {"id": f"eq.{uid}", "select": "timezone,hour_format,adjust_for_dividends,updated_at"},
    )
    if pr.status_code != 200:
        logger.warning("supabase profiles read status=%s", pr.status_code)
        return None
    prows = pr.json()
    prof: dict[str, Any] = {}
    if isinstance(prows, list) and prows:
        p0 = prows[0]
        prof = {
            "timezone": normalize_timezone(str(p0.get("timezone") or "")),
            "hour_format": normalize_hour_format(str(p0.get("hour_format") or "")) or "auto",
            "adjust_for_dividends": normalize_adjust_for_dividends(p0.get("adjust_for_dividends")),
            "updated_at": p0.get("updated_at"),
        }
    ar = _get(
        "alpaca_accounts",
        {
            "user_id": f"eq.{uid}",
            "select": _account_select(include_credentials=True),
            "order": "created_at.asc",
        },
    )
    accounts: list[dict[str, Any]] = []
    if ar.status_code == 200 and isinstance(ar.json(), list):
        for a in ar.json():
            if not isinstance(a, dict):
                continue
            ak = str(a.get("alpaca_api_key") or "").strip()
            sk = str(a.get("alpaca_secret_key") or "").strip()
            accounts.append(
                {
                    "id": str(a.get("id") or ""),
                    "label": str(a.get("label") or ""),
                    "is_live": bool(a.get("is_live")),
                    "has_alpaca_api_key": bool(ak),
                    "has_alpaca_secret_key": bool(sk),
                    "alpaca_api_key_hint": mask_secret_tail(ak) if ak else "",
                    "alpaca_secret_key_hint": mask_secret_tail(sk) if sk else "",
                    "created_at": a.get("created_at"),
                    "updated_at": a.get("updated_at"),
                }
            )
    return {"profile": prof, "alpaca_accounts": accounts}


def fetch_adjust_for_dividends(user_id: str) -> bool:
    uid = (user_id or "").strip()
    if not uid or not service_role_configured():
        return False
    r = _get(
        "profiles",
        {
            "id": f"eq.{urllib.parse.quote(uid, safe='')}",
            "select": "adjust_for_dividends",
        },
    )
    if r.status_code != 200:
        return False
    rows = r.json()
    if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
        return False
    return normalize_adjust_for_dividends(rows[0].get("adjust_for_dividends"))


def upsert_profile_settings(
    user_id: str,
    *,
    user_timezone: str | None = None,
    hour_format: str | None = None,
    adjust_for_dividends: bool | None = None,
) -> tuple[bool, str]:
    uid = (user_id or "").strip()
    if not uid or not service_role_configured():
        return False, "Trading settings are not configured on the server"
    if user_timezone is None and hour_format is None and adjust_for_dividends is None:
        return False, "No fields to update"
    body: dict[str, Any] = {}
    if user_timezone is not None:
        tz = normalize_timezone(user_timezone)
        if not tz:
            return False, "Invalid timezone"
        body["timezone"] = tz
    if hour_format is not None:
        fmt = normalize_hour_format(hour_format)
        if not fmt:
            return False, "Invalid hour format"
        body["hour_format"] = fmt
    if adjust_for_dividends is not None:
        body["adjust_for_dividends"] = bool(adjust_for_dividends)
    rget = _get(
        "profiles",
        {
            "id": f"eq.{urllib.parse.quote(uid, safe='')}",
            "select": "timezone,hour_format,adjust_for_dividends",
        },
    )
    has_row = False
    if rget.status_code == 200 and isinstance(rget.json(), list) and rget.json():
        has_row = True
    body["updated_at"] = datetime.now(timezone.utc).isoformat()
    if has_row:
        r = _patch(f"profiles?id=eq.{urllib.parse.quote(uid, safe='')}", body)
        if r.status_code in (200, 204):
            return True, ""
        logger.warning("supabase profiles patch status=%s body=%s", r.status_code, r.text[:500])
        return False, "Failed to save profile"
    ins = {"id": uid, **body}
    r2 = _post("profiles", ins)
    if r2.status_code in (200, 201):
        return True, ""
    logger.warning("supabase profiles insert status=%s body=%s", r2.status_code, r2.text[:500])
    return False, "Failed to save profile"


def insert_alpaca_account(
    user_id: str,
    *,
    label: str,
    alpaca_api_key: str,
    alpaca_secret_key: str,
) -> tuple[dict[str, Any] | None, str]:
    uid = (user_id or "").strip()
    if not uid or not service_role_configured():
        return None, "Trading settings are not configured on the server"
    ak = (alpaca_api_key or "").strip()
    sk = (alpaca_secret_key or "").strip()
    lab = (label or "").strip()
    if not lab:
        return None, "Account label is required"
    if not ak or not sk:
        return None, "Alpaca API key and secret are required"
    detected_is_live, detect_err = detect_alpaca_account_is_live(
        alpaca_api_key=ak,
        alpaca_secret_key=sk,
    )
    if detect_err:
        return None, detect_err
    now_iso = datetime.now(timezone.utc).isoformat()
    row = {
        "user_id": uid,
        "label": lab,
        "alpaca_api_key": ak,
        "alpaca_secret_key": sk,
        "is_live": bool(detected_is_live),
        "updated_at": now_iso,
    }
    r = _post("alpaca_accounts", row)
    if r.status_code in (200, 201) and r.text:
        data = r.json()
        if isinstance(data, list) and data and isinstance(data[0], dict):
            return data[0], ""
        if isinstance(data, dict):
            return data, ""
    logger.warning("supabase alpaca_accounts insert status=%s body=%s", r.status_code, r.text[:500])
    return None, "Failed to create account"


def update_alpaca_account(
    user_id: str,
    account_id: str,
    *,
    label: str | None = None,
    alpaca_api_key: str | None = None,
    alpaca_secret_key: str | None = None,
) -> tuple[bool, str]:
    uid = (user_id or "").strip()
    aid = (account_id or "").strip()
    if not uid or not aid or not service_role_configured():
        return False, "Trading settings are not configured on the server"
    if label is None and alpaca_api_key is None and alpaca_secret_key is None:
        return False, "No fields to update"
    body: dict[str, Any] = {"updated_at": datetime.now(timezone.utc).isoformat()}
    if label is not None:
        body["label"] = label.strip()
    if alpaca_api_key is not None:
        body["alpaca_api_key"] = alpaca_api_key.strip()
    if alpaca_secret_key is not None:
        body["alpaca_secret_key"] = alpaca_secret_key.strip()
    if alpaca_api_key is not None or alpaca_secret_key is not None:
        current = fetch_alpaca_account_for_user(uid, aid, include_credentials=True)
        if current is None:
            return False, "Alpaca account not found"
        merged_api_key = (
            body["alpaca_api_key"]
            if alpaca_api_key is not None
            else str(current.get("alpaca_api_key") or "").strip()
        )
        merged_secret_key = (
            body["alpaca_secret_key"]
            if alpaca_secret_key is not None
            else str(current.get("alpaca_secret_key") or "").strip()
        )
        detected_is_live, detect_err = detect_alpaca_account_is_live(
            alpaca_api_key=merged_api_key,
            alpaca_secret_key=merged_secret_key,
        )
        if detect_err:
            return False, detect_err
        body["is_live"] = bool(detected_is_live)
    r = _patch(
        f"alpaca_accounts?id=eq.{urllib.parse.quote(aid, safe='')}&user_id=eq.{urllib.parse.quote(uid, safe='')}",
        body,
    )
    if r.status_code in (200, 204):
        return True, ""
    logger.warning("supabase alpaca_accounts patch status=%s body=%s", r.status_code, r.text[:500])
    return False, "Failed to update account"


def delete_alpaca_account(user_id: str, account_id: str) -> tuple[bool, str]:
    uid = (user_id or "").strip()
    aid = (account_id or "").strip()
    if not uid or not aid or not service_role_configured():
        return False, "Trading settings are not configured on the server"
    r = _delete(
        f"alpaca_accounts?id=eq.{urllib.parse.quote(aid, safe='')}&user_id=eq.{urllib.parse.quote(uid, safe='')}"
    )
    if r.status_code in (200, 204):
        return True, ""
    logger.warning("supabase alpaca_accounts delete status=%s body=%s", r.status_code, r.text[:500])
    return False, "Failed to delete account"
