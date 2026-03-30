from __future__ import annotations

import argparse
import os

from flask import Flask
from flask_cors import CORS

from api.routes import strategy_blueprint
from db.models import Base
from db.session import engine, ensure_strategy_columns


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["JSON_SORT_KEYS"] = False
    app.config["OPENROUTER_MODEL"] = os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini")

    CORS(app, resources={r"/*": {"origins": "*"}})

    Base.metadata.create_all(bind=engine)
    ensure_strategy_columns(engine)

    app.register_blueprint(strategy_blueprint)
    return app


app = create_app()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--debug", action="store_true", help="enable Flask debug mode")
    args = parser.parse_args()
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8080")),
        debug=args.debug,
    )  
