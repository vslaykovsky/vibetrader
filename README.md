# VibeTrader

Chat-based trading strategy builder.

VibeTrader is a web app where you iterate on a trading strategy in a chat UI. The backend stores “threads” (conversations) and returns the updated message list plus data needed to render results/charts in the frontend.

## Project structure

- **`frontend/`**: React + Vite chat UI.
- **`backend/`**: Flask API that powers the chat workflow and persists thread state.
- **`backend/strategies/`**: Per-thread strategy workspaces at `backend/strategies/<THREAD_UUID>/` (seeded from `backend/strategies/AGENTS.md`).

## Docs

- **Frontend**: see `frontend/README.md`
- **Backend**: see `backend/README.md`

## Notes / backlog

- supabase auth
- sqlite -> pg
- E2B for codegen and runs
- global cache of market data. 
- Names ideas (available names):
    - vibestrategy.ai
    - traderchat.ai

