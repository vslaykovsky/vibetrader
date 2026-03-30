# Folders

frontend - React.js based frontend code. This is a chat based UI that communicates with the backend server.
backend - Flask based backend server. This returns an updated list of user and agent messages along with data for the canvas. 
backend/strategies - Per-thread strategy workspaces at `backend/strategies/<THREAD_UUID>/` (seeded from `backend/strategies/AGENTS.md`). The backend agent runs Codex (`codex exec` with bypass flags) on each strategy update and backtests in that folder using `python src/strategy.py` with cwd set to the thread directory.

