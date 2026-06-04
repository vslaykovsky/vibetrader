FROM python:3.12

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends nodejs npm bubblewrap \
    && rm -rf /var/lib/apt/lists/*

RUN npm install -g @openai/codex \
    && npm cache clean --force

RUN pip install --no-cache-dir --upgrade pip

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt gunicorn

COPY . /app

ARG CODEX_AUTH_JSON_B64=""
RUN if [ -n "$CODEX_AUTH_JSON_B64" ]; then \
        mkdir -p /root/.codex \
        && printf '%s' "$CODEX_AUTH_JSON_B64" | base64 -d > /root/.codex/auth.json \
        && chmod 600 /root/.codex/auth.json; \
    fi

EXPOSE 8080

CMD ["gunicorn", "-c", "gunicorn_conf.py", "app:app"]

