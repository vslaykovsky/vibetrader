# Backend

Flask API for the strategy-building chat application.

## What it does

- Persists thread state (messages + metadata) in SQLite
- Exposes endpoints used by the frontend chat UI
- Manages per-thread strategy workspaces under `backend/strategies/<THREAD_UUID>/`

## Run

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
python backend/app.py
```

The SQLite database is stored at `backend/db/db.sqlite`.

## API

`GET /strategy?thread_id=<uuid>` returns the current thread state and creates an empty row if it does not exist.

`GET /threads` returns a time-ordered list of threads, ordered by latest run.

`POST /strategy` accepts:

```json
{
  "thread_id": "uuid",
  "message": "user message"
}
```
