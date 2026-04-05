from __future__ import annotations

import functools
import logging
import os
from functools import lru_cache

import jwt
from jwt import PyJWKClient
from flask import g, jsonify, request

logger = logging.getLogger(__name__)


def _jwt_secret() -> str:
    return os.getenv("SUPABASE_JWT_SECRET", "")


def _supabase_url() -> str:
    return os.getenv("SUPABASE_URL", "").strip().rstrip("/")


@lru_cache(maxsize=8)
def _jwks_client(jwks_url: str) -> PyJWKClient:
    return PyJWKClient(jwks_url)


def _token_diagnostics(token: str) -> dict:
    out: dict = {"token_length": len(token)}
    try:
        h = jwt.get_unverified_header(token)
        out["jwt_alg"] = h.get("alg")
        out["jwt_typ"] = h.get("typ")
        out["jwt_kid"] = h.get("kid")
    except Exception as exc:
        out["jwt_header_error"] = f"{type(exc).__name__}: {exc}"
    try:
        claims = jwt.decode(
            token,
            options={"verify_signature": False, "verify_aud": False, "verify_exp": False},
        )
        out["claims_iss"] = claims.get("iss")
        out["claims_aud"] = claims.get("aud")
        out["claims_role"] = claims.get("role")
        out["claims_has_sub"] = bool(claims.get("sub"))
        exp = claims.get("exp")
        out["claims_has_exp"] = exp is not None
    except Exception as exc:
        out["claims_unverified_error"] = f"{type(exc).__name__}: {exc}"
    return out


def _extract_token() -> str | None:
    auth_header = (request.headers.get("Authorization") or "").strip()
    if auth_header.startswith("Bearer "):
        t = auth_header[7:].strip()
        return t or None
    q = (request.args.get("access_token") or "").strip()
    return q or None


def _audience_is_authenticated(aud: str | list | None) -> bool:
    if aud == "authenticated":
        return True
    if isinstance(aud, (list, tuple)):
        return "authenticated" in aud
    return False


def _decode_supabase_jwt(token: str) -> dict:
    header = jwt.get_unverified_header(token)
    alg = header.get("alg")
    opts = {"verify_aud": False}
    if alg == "HS256":
        secret = _jwt_secret()
        if not secret:
            raise jwt.InvalidTokenError("SUPABASE_JWT_SECRET not configured for HS256")
        return jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            options=opts,
        )
    if alg in ("ES256", "RS256"):
        base = _supabase_url()
        if not base:
            raise jwt.InvalidTokenError("SUPABASE_URL not configured for asymmetric JWT")
        unverified = jwt.decode(
            token,
            options={
                **opts,
                "verify_signature": False,
                "verify_exp": False,
            },
        )
        iss = unverified.get("iss")
        if not isinstance(iss, str) or not iss.strip():
            raise jwt.InvalidTokenError("missing iss claim")
        expected_iss = f"{base}/auth/v1"
        if iss.rstrip("/") != expected_iss.rstrip("/"):
            raise jwt.InvalidTokenError("issuer does not match SUPABASE_URL")
        jwks_url = iss.rstrip("/") + "/.well-known/jwks.json"
        signing_key = _jwks_client(jwks_url).get_signing_key_from_jwt(token)
        return jwt.decode(
            token,
            signing_key.key,
            algorithms=[alg],
            options=opts,
        )
    raise jwt.InvalidTokenError(f"unsupported JWT algorithm {alg!r}")


def require_auth(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        token = _extract_token()
        if not token:
            logger.warning(
                "auth 401: missing_token method=%s path=%s has_auth_header=%s",
                request.method,
                request.path,
                bool((request.headers.get("Authorization") or "").strip()),
            )
            return jsonify({"error": "Authentication required"}), 401

        try:
            alg = jwt.get_unverified_header(token).get("alg")
        except Exception as exc:
            logger.warning(
                "auth 401: bad_jwt_header method=%s path=%s exc=%s",
                request.method,
                request.path,
                exc,
            )
            return jsonify({"error": "Invalid token"}), 401

        if alg == "HS256" and not _jwt_secret():
            logger.error("SUPABASE_JWT_SECRET is not configured (HS256 token)")
            return jsonify({"error": "Server auth misconfigured"}), 500
        if alg in ("ES256", "RS256") and not _supabase_url():
            logger.error("SUPABASE_URL is not configured (asymmetric JWT)")
            return jsonify({"error": "Server auth misconfigured"}), 500

        diag = _token_diagnostics(token)
        if alg == "HS256":
            diag["jwt_secret_len"] = len(_jwt_secret())
        else:
            diag["supabase_url_configured"] = bool(_supabase_url())

        try:
            payload = _decode_supabase_jwt(token)
        except jwt.ExpiredSignatureError as exc:
            logger.warning(
                "auth 401: token_expired method=%s path=%s diag=%s exc=%s",
                request.method,
                request.path,
                diag,
                exc,
            )
            return jsonify({"error": "Token expired"}), 401
        except jwt.PyJWKClientError as exc:
            logger.exception(
                "auth 503: jwks_error method=%s path=%s diag=%s",
                request.method,
                request.path,
                diag,
            )
            return jsonify({"error": "Authentication temporarily unavailable"}), 503
        except jwt.InvalidTokenError as exc:
            logger.warning(
                "auth 401: invalid_jwt method=%s path=%s exc_type=%s exc=%s diag=%s",
                request.method,
                request.path,
                type(exc).__name__,
                exc,
                diag,
            )
            return jsonify({"error": "Invalid token"}), 401

        if payload.get("role") != "authenticated":
            logger.warning(
                "auth 401: wrong_role method=%s path=%s role=%r aud=%r sub_present=%s diag=%s",
                request.method,
                request.path,
                payload.get("role"),
                payload.get("aud"),
                bool(payload.get("sub")),
                diag,
            )
            return jsonify({"error": "Invalid token"}), 401
        if not _audience_is_authenticated(payload.get("aud")):
            logger.warning(
                "auth 401: wrong_audience method=%s path=%s role=%r aud=%r diag=%s",
                request.method,
                request.path,
                payload.get("role"),
                payload.get("aud"),
                diag,
            )
            return jsonify({"error": "Invalid token"}), 401

        g.user_id = payload.get("sub")
        if not g.user_id:
            logger.warning(
                "auth 401: missing_sub method=%s path=%s role=%r aud=%r diag=%s",
                request.method,
                request.path,
                payload.get("role"),
                payload.get("aud"),
                diag,
            )
            return jsonify({"error": "Invalid token: missing subject"}), 401

        return fn(*args, **kwargs)

    return wrapper
