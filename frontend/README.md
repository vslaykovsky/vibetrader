# Frontend

React + Vite frontend for the strategy-building chat UI.

## What it does

- Chat UI for building/running strategies
- Calls the Flask API to fetch threads and post user messages
- Renders the returned thread state and any accompanying canvas/chart data

## Run

```bash
npm install
npm run dev
```

## Configuration

Set `VITE_API_BASE_URL` if the Flask API is not running on `http://localhost:5000`.

Example:

```bash
export VITE_API_BASE_URL="http://localhost:5000"
```
