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

EXPOSE 8080

CMD ["sh", "-lc", "if [ -n \"${OPENAI_API_KEY:-}\" ]; then printenv OPENAI_API_KEY | codex login --with-api-key; fi; exec gunicorn -c gunicorn_conf.py app:app"]

