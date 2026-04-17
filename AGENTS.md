# Vibetrader — agent context

Chat UI + Flask API. An agent edits per-thread Python under `backend/strategies/<thread_id>/`, runs `strategy.py`, and the client renders chart JSON from the saved run.

## Architecture

- **Frontend** (`frontend/`): React/Vite, Supabase session, calls the API with a Bearer JWT.
- **Backend** (`backend/`): Flask app in `app.py`; HTTP in `api/routes.py`; auth in `auth.py`; DB models/session in `db/`.
- **Agent** (`services/agent.py`): LLM + tools; invokes Codex on the thread workspace and subprocess runs with cwd set to that folder. Thread state is persisted as `Strategy` rows (messages, canvas, code, status, etc.); strategy source on disk is the workspace for that `thread_id`.
- **Strategy contract** (schemas, run contract, params/output JSON): **`backend/strategies/AGENTS.md`** — templates (`strategy.py`, `utils.py`, `hyperopt.py`, same `AGENTS.md`) are copied into each new workspace; change the template under `backend/strategies/`, not only one thread directory.

## `strategy` table and DB access

Each row is one persisted snapshot for a chat thread: `id` (UUID primary key), `thread_id` (conversation key), `created_at` (ordering). Payload columns are JSON `messages` and `canvas`, and text `code` (copy of workspace `strategy.py` after runs). `status` / `status_text` record whether a run is in progress or finished and any error string. `created_by` / `created_by_email` tie rows to the authenticated user; `langsmith_trace` stores an optional trace URL.

The app uses SQLAlchemy 2.x (see `backend/db/session.py`): a module-level `engine` and `SessionLocal` factory. Route and job code typically opens `session = SessionLocal()`, runs `session.query(Strategy)` / `session.get(Strategy, id)`, mutates the row, `session.commit()`, and closes the session in a `finally` block. `create_app` calls `Base.metadata.create_all` plus small `ensure_*` helpers for legacy sqlite columns.

Try the already fully configured `@modelcontextprotocol/server-postgres` MCP tool when the user asks to access, query, or inspect the database at runtime (real data, ad-hoc SQL, or schema verification against a live instance). Do not check configuration, do not explore files, database url, credentials. MCP tool is ready for you to use immediately. 

When analysing chat threads use database as a source of truth, not local backend/strategies/<thread_id> folder! 
Check `strategy` table, every record is a single run in the chat thread. It contains all messages as well as code and canvas outputs. Additionally it links to langsmith_trace that contains execution logs accessible with langsmith MCP tool. 

## Where to look first

- DB model / engine URL / session factory → `backend/db/models.py`, `backend/db/session.py`
- API shape / new routes → `backend/api/routes.py`
- Agent, tools, Codex, runs → `backend/services/agent.py`
- LLM client → `langchain_openrouter.ChatOpenRouter` (see `backend/services/agent.py`)
- UI thread + canvas → `frontend/src/pages/StrategyPage.jsx`, `frontend/src/strategyChartRenderer.js`
- Chart/output JSON spec → `backend/strategies/AGENTS.md`

Details (exact paths, env names, observability) live in code and deployment config; read them when the task touches that layer.
