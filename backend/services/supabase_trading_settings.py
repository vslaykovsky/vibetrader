from __future__ import annotations

import json
import logging
import os
import urllib.parse
from datetime import datetime, timezone
from typing import Any

import requests

logger = logging.getLogger(__name__)


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


def fetch_profile_alpaca_keys(user_id: str) -> tuple[str, str] | None:
    uid = (user_id or "").strip()
    if not uid or not service_role_configured():
        return None
    r = _get(
        "profiles",
        {"id": f"eq.{uid}", "select": "alpaca_api_key,alpaca_secret_key"},
    )
    if r.status_code != 200:
        logger.warning("supabase profiles fetch status=%s body=%s", r.status_code, r.text[:500])
        return None
    rows = r.json()
    if not isinstance(rows, list) or not rows:
        return None
    row = rows[0]
    k = str(row.get("alpaca_api_key") or "").strip()
    s = str(row.get("alpaca_secret_key") or "").strip()
    if not k or not s:
        return None
    return k, s


def fetch_alpaca_account_for_user(user_id: str, account_id: str) -> dict[str, Any] | None:
    uid = (user_id or "").strip()
    aid = (account_id or "").strip()
    if not uid or not aid or not service_role_configured():
        return None
    r = _get(
        "alpaca_accounts",
        {
            "id": f"eq.{urllib.parse.quote(aid, safe='')}",
            "user_id": f"eq.{urllib.parse.quote(uid, safe='')}",
            "select": "id,account,label,is_live",
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


def fetch_trading_settings_payload(user_id: str) -> dict[str, Any] | None:
    uid = (user_id or "").strip()
    if not uid or not service_role_configured():
        return None
    pr = _get("profiles", {"id": f"eq.{uid}", "select": "alpaca_api_key,alpaca_secret_key,updated_at"})
    if pr.status_code != 200:
        logger.warning("supabase profiles read status=%s", pr.status_code)
        return None
    prows = pr.json()
    prof: dict[str, Any] = {}
    if isinstance(prows, list) and prows:
        p0 = prows[0]
        ak = str(p0.get("alpaca_api_key") or "").strip()
        sk = str(p0.get("alpaca_secret_key") or "").strip()
        prof = {
            "has_alpaca_api_key": bool(ak),
            "has_alpaca_secret_key": bool(sk),
            "alpaca_api_key_hint": mask_secret_tail(ak) if ak else "",
            "alpaca_secret_key_hint": mask_secret_tail(sk) if sk else "",
            "updated_at": p0.get("updated_at"),
        }
    ar = _get(
        "alpaca_accounts",
        {"user_id": f"eq.{uid}", "select": "id,account,label,is_live,created_at,updated_at", "order": "created_at.asc"},
    )
    accounts: list[dict[str, Any]] = []
    if ar.status_code == 200 and isinstance(ar.json(), list):
        for a in ar.json():
            if not isinstance(a, dict):
                continue
            accounts.append(
                {
                    "id": str(a.get("id") or ""),
                    "account": str(a.get("account") or ""),
                    "label": str(a.get("label") or ""),
                    "is_live": bool(a.get("is_live")),
                    "created_at": a.get("created_at"),
                    "updated_at": a.get("updated_at"),
                }
            )
    return {"profile": prof, "alpaca_accounts": accounts}


def upsert_profile_alpaca_keys(
    user_id: str,
    *,
    alpaca_api_key: str | None = None,
    alpaca_secret_key: str | None = None,
) -> tuple[bool, str]:
    uid = (user_id or "").strip()
    if not uid or not service_role_configured():
        return False, "Trading settings are not configured on the server"
    if alpaca_api_key is None and alpaca_secret_key is None:
        return False, "No fields to update"
    now_iso = datetime.now(timezone.utc).isoformat()
    rget = _get("profiles", {"id": f"eq.{urllib.parse.quote(uid, safe='')}", "select": "alpaca_api_key,alpaca_secret_key"})
    ak = ""
    sk = ""
    has_row = False
    if rget.status_code == 200 and isinstance(rget.json(), list) and rget.json():
        has_row = True
        row0 = rget.json()[0]
        if isinstance(row0, dict):
            ak = str(row0.get("alpaca_api_key") or "")
            sk = str(row0.get("alpaca_secret_key") or "")
    if alpaca_api_key is not None:
        ak = alpaca_api_key
    if alpaca_secret_key is not None:
        sk = alpaca_secret_key
    body = {"alpaca_api_key": ak, "alpaca_secret_key": sk, "updated_at": now_iso}
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
    account: str,
    label: str,
    is_live: bool,
) -> tuple[dict[str, Any] | None, str]:
    uid = (user_id or "").strip()
    if not uid or not service_role_configured():
        return None, "Trading settings are not configured on the server"
    now_iso = datetime.now(timezone.utc).isoformat()
    row = {
        "user_id": uid,
        "account": (account or "").strip(),
        "label": (label or "").strip(),
        "is_live": bool(is_live),
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
    account: str | None = None,
    label: str | None = None,
    is_live: bool | None = None,
) -> tuple[bool, str]:
    uid = (user_id or "").strip()
    aid = (account_id or "").strip()
    if not uid or not aid or not service_role_configured():
        return False, "Trading settings are not configured on the server"
    if account is None and label is None and is_live is None:
        return False, "No fields to update"
    body: dict[str, Any] = {"updated_at": datetime.now(timezone.utc).isoformat()}
    if account is not None:
        body["account"] = account.strip()
    if label is not None:
        body["label"] = label.strip()
    if is_live is not None:
        body["is_live"] = bool(is_live)
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
