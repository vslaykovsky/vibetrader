from __future__ import annotations

import os
import functools
import logging

import jwt
from flask import g, jsonify, request

logger = logging.getLogger(__name__)

SUPABASE_JWT_SECRET = os.getenv("SUPABASE_JWT_SECRET", "")


def _extract_token() -> str | None:
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:]
    return request.args.get("access_token")


def require_auth(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        token = _extract_token()
        if not token:
            return jsonify({"error": "Authentication required"}), 401

        if not SUPABASE_JWT_SECRET:
            logger.error("SUPABASE_JWT_SECRET is not configured")
            return jsonify({"error": "Server auth misconfigured"}), 500

        try:
            payload = jwt.decode(
                token,
                SUPABASE_JWT_SECRET,
                algorithms=["HS256"],
                audience="authenticated",
            )
        except jwt.ExpiredSignatureError:
            return jsonify({"error": "Token expired"}), 401
        except jwt.InvalidTokenError:
            return jsonify({"error": "Invalid token"}), 401

        g.user_id = payload.get("sub")
        if not g.user_id:
            return jsonify({"error": "Invalid token: missing subject"}), 401

        return fn(*args, **kwargs)

    return wrapper
