from __future__ import annotations

import dotenv
dotenv.load_dotenv()

import argparse
import os
import sys

import time
import uuid

from flask import Flask, g, request
from flask_cors import CORS

from api.routes import strategy_blueprint
from db.models import Base
from db.session import (
    engine,
    ensure_strategy_columns,
    ensure_strategy_created_by_column,
    ensure_strategy_created_by_email_column,
    ensure_strategy_langsmith_trace_column,
    ensure_strategy_strategy_name_column,
    ensure_strategy_algorithm_column,
    ensure_strategy_messages_count_column,
)
import logging
from logging.handlers import RotatingFileHandler
import json
from flask.signals import got_request_exception


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["JSON_SORT_KEYS"] = False
    app.config["OPENROUTER_MODEL"] = os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini")
    app.config["REQUEST_ID_HEADER"] = os.getenv("REQUEST_ID_HEADER", "X-Request-Id")

    CORS(
        app,
        resources={
            r"/*": {
                "origins": "*",
                "allow_headers": ["Authorization", "Content-Type", "X-Request-Id"],
            }
        },
    )

    Base.metadata.create_all(bind=engine)
    ensure_strategy_columns(engine)
    ensure_strategy_created_by_column(engine)
    ensure_strategy_created_by_email_column(engine)
    ensure_strategy_langsmith_trace_column(engine)
    ensure_strategy_strategy_name_column(engine)
    ensure_strategy_algorithm_column(engine)
    ensure_strategy_messages_count_column(engine)

    app.register_blueprint(strategy_blueprint)

    log_level = os.getenv("LOG_LEVEL", "INFO")
    gcp = (os.getenv("GCP_LOGGING", "1").strip() not in ("0", "false", "False"))
    if "--debug" in sys.argv:
        gcp = False
    configure_logging(log_level, gcp=gcp)
    logger = logging.getLogger("backend.request")

    @app.before_request
    def _start_request_timer():
        hdr = app.config["REQUEST_ID_HEADER"]
        rid = (request.headers.get(hdr) or request.headers.get("X-Request-Id") or "").strip()
        if not rid:
            rid = uuid.uuid4().hex
        g.request_id = rid
        g.request_start = time.perf_counter()

    @app.after_request
    def _log_request(response):
        start = getattr(g, "request_start", None)
        dur_ms = None
        if isinstance(start, (int, float)):
            dur_ms = int((time.perf_counter() - start) * 1000)
        rid = getattr(g, "request_id", "")
        response.headers[app.config["REQUEST_ID_HEADER"]] = rid
        try:
            qs = (request.query_string or b"").decode("utf-8", errors="replace")
            full_path = f"{request.path}?{qs}" if qs else request.path
            logger.info(
                f"{request.method} {full_path} -> {response.status_code} ({dur_ms}ms)",
                extra={
                    "request_id": rid,
                    "method": request.method,
                    "path": request.path,
                    "query_string": qs,
                    "status_code": response.status_code,
                    "duration_ms": dur_ms,
                    "remote_addr": request.headers.get("X-Forwarded-For", request.remote_addr),
                    "user_agent": request.headers.get("User-Agent", ""),
                },
            )
        except Exception:
            logger.exception("failed to log request")
        return response

    @app.teardown_request
    def _log_teardown(exc):
        if exc is None:
            return
        rid = getattr(g, "request_id", "")
        logger.exception(
            "teardown_request exception",
            extra={
                "request_id": rid,
                "method": getattr(request, "method", None),
                "path": getattr(request, "path", None),
            },
        )

    def _log_got_request_exception(sender, exception, **extra):
        rid = getattr(g, "request_id", "")
        logger.exception(
            "got_request_exception",
            extra={
                "request_id": rid,
                "method": getattr(request, "method", None),
                "path": getattr(request, "path", None),
            },
        )

    got_request_exception.connect(_log_got_request_exception, app)
    return app


class GCPLoggingFormatter(logging.Formatter):
    LEVEL_TO_SEVERITY = {
        logging.DEBUG: "DEBUG",
        logging.INFO: "INFO",
        logging.WARNING: "WARNING",
        logging.ERROR: "ERROR",
        logging.CRITICAL: "CRITICAL",
    }

    def format(self, record):
        log_entry = {
            "severity": self.LEVEL_TO_SEVERITY.get(record.levelno, "DEFAULT"),
            "message": super().format(record),
        }
        return json.dumps(log_entry)

def configure_logging(log_level: str, gcp=True):
    """Configure logging with the specified level."""
    import sys
    numeric_level = getattr(logging, log_level.upper(), logging.DEBUG)
    
    if gcp:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(GCPLoggingFormatter('%(name)s:%(lineno)d - %(message)s'))
        
        logging.basicConfig(
            level=numeric_level,
            handlers=[handler],
            force=True
        )

        for name in ("gunicorn", "gunicorn.error", "gunicorn.access"):
            glog = logging.getLogger(name)
            glog.handlers = [handler]
            glog.propagate = True
    else:
        logging.basicConfig(
            level=numeric_level,
            format='%(levelname)s %(name)s:%(lineno)d - %(message)s'
        )
    
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("hpack").setLevel(logging.WARNING)
    logging.getLogger("voyage").setLevel(logging.WARNING)
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    logging.getLogger("gunicorn").setLevel(numeric_level)
    logging.getLogger("gunicorn.error").setLevel(numeric_level)
    logging.getLogger("gunicorn.access").setLevel(numeric_level)


app = create_app()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--debug", action="store_true", help="enable Flask debug mode")

    args = parser.parse_args()
    if args.debug:
        configure_logging("INFO", gcp=False)
    else:
        configure_logging("INFO", gcp=True)
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8080")),
        debug=args.debug,
    )  
