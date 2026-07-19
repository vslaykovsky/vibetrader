FROM python:3.12

ARG RUST_TOOLCHAIN=1.85.1

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CARGO_HOME=/opt/cargo \
    RUSTUP_HOME=/opt/rustup \
    RUST_TOOLCHAIN=${RUST_TOOLCHAIN} \
    PATH=/opt/cargo/bin:$PATH

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends nodejs npm bubblewrap curl ca-certificates build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \
    | sh -s -- -y --no-modify-path --profile minimal --default-toolchain "${RUST_TOOLCHAIN}" \
    && cargo --version \
    && rustc --version \
    && rustup show active-toolchain

RUN npm install -g @openai/codex \
    && npm cache clean --force

RUN pip install --no-cache-dir --upgrade pip

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt gunicorn

COPY . /app

RUN chmod 755 /app/scripts/backend_entrypoint.sh

# Warm the shared Cargo target so the first generated Rust strategy only needs
# to compile its small strategy crate.
RUN mkdir -p /tmp/vibetrader-rust-template \
    && cp strategies_v2/Cargo.toml strategies_v2/Cargo.lock strategies_v2/strategy.rs strategies_v2/utils.rs strategies_v2/worker.rs strategies_v2/simulator.rs strategies_v2/optimizer.rs strategies_v2/optimizer_runtime.rs strategies_v2/portfolio.rs strategies_v2/params.json /tmp/vibetrader-rust-template/ \
    && sed -i 's/{{CRATE_NAME}}/vibetrader_strategy_template/g' /tmp/vibetrader-rust-template/Cargo.toml /tmp/vibetrader-rust-template/Cargo.lock \
    && CARGO_TARGET_DIR=/app/.rust-target cargo build --release --locked --bins --manifest-path /tmp/vibetrader-rust-template/Cargo.toml \
    && rm -rf /tmp/vibetrader-rust-template

ARG CODEX_AUTH_JSON_B64=""
RUN if [ -n "$CODEX_AUTH_JSON_B64" ]; then \
        mkdir -p /root/.codex \
        && printf '%s' "$CODEX_AUTH_JSON_B64" | base64 -d > /root/.codex/auth.json \
        && chmod 600 /root/.codex/auth.json; \
    fi

EXPOSE 8080

ENTRYPOINT ["/app/scripts/backend_entrypoint.sh"]
CMD ["gunicorn", "-c", "gunicorn_conf.py", "app:app"]
