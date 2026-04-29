# VibeTrader

Chat-based trading strategy builder.

VibeTrader is a web app where you iterate on a trading strategy in a chat UI. The backend stores “threads” (conversations) and returns the updated message list plus data needed to render results/charts in the frontend.

## Prereqs

- **Python**: 3.12 recommended
- **Node.js**: for the frontend (`frontend/`)
- **Docker**: required for building runner images (and for GKE deploys)
- **kubectl + gcloud**: only required for GKE setup

Install if needed:

- **gcloud**: `https://docs.cloud.google.com/sdk/docs/install-sdk`
- **Docker**: `https://docs.docker.com/engine/install/`
- **kubectl** (Linux example):

```
sudo apt-get install kubectl
sudo apt-get install google-cloud-cli-gke-gcloud-auth-plugin
gcloud container clusters get-credentials autopilot-cluster-1 --zone us-central1 --project traderchat
```

Authenticate docker to Artifact Registry (only for GKE builds/push):

```
gcloud auth login 
gcloud auth configure-docker \
    us-central1-docker.pkg.dev
```

Optionally, bootstrap `.env` from an existing cluster configmap:

```
kubectl get configmap vibetrader-config -o json --namespace vibetrader |  jq -r '.data | to_entries[] | "\(.key)=\(.value)"' > .env
```

## Local dev (frontend + backend)

Create a virtualenv (recommended):

```
python -m venv .venv
source .venv/bin/activate
```

Install backend packages:

```
pip install -r backend/requirements.txt
```

On Linux, Codex `exec --full-auto` needs [bubblewrap](https://developers.openai.com/codex/concepts/sandboxing#prerequisites) on `PATH` (e.g. `sudo apt install bubblewrap`). If `bwrap` still cannot create user namespaces, apply the sysctl notes in `deploy/gke/README.md` (Codex section) on the host or cluster node. In Docker or Kubernetes where you cannot change those sysctls, set `CODEX_BYPASS_SANDBOX=1` for the backend process so Codex skips its sandbox (see the same deploy doc).

Install frontend packages:

```
cd frontend
npm install
```

Run the backend:

```
python backend/app.py
```

Run the frontend (separate terminal):

```
cd frontend
npm run dev
```

## Live runs: runner + orchestrator (local vs GKE)

“Live runs” are controlled via the Flask API (`/live/start`, `/live/stop`, `/live/status`) and executed by a **runner** process (`backend/scripts/run_alpaca_strategy.py`) that reads Alpaca market events from the DB and writes run events back to the DB for streaming to the UI.

- **Alpaca market data → DB**: `backend/scripts/alpaca_live_listener.py` runs as a separate long-lived process. It uses Alpaca’s market data WebSocket (`StockDataStream`) to subscribe to **1-minute bars** for symbols that active runs register in `alpaca_live_subscriptions`, and appends each bar to `alpaca_live_events`. Set `ALPACA_API_KEY` and `ALPACA_SECRET_KEY` in the environment (same as the runner). This path is bar-based OHLC, not sub-minute tick/trade streams.
- **Positions**: Portfolio snapshots for the strategy come from the runner calling Alpaca’s **REST** API (`get_all_positions()`), not from a realtime WebSocket listener writing to the DB.

- **One DB, one bar listener**: `alpaca_live_listener.py` appends global rows to `alpaca_live_events`. Run **at most one** listener process per database; two listeners against the same DB will insert duplicate bars for the same symbols and confuse runners. Point local dev and production at **different databases** when you can so live-run tables and a single in-cluster listener stay isolated from laptop experiments.

- **Local runner backend** (`LIVE_RUNNER_BACKEND=local`): the API only creates a `live_runs` row with `status=starting`. You must run a local **orchestrator** that polls the DB and spawns runner processes.
- **Kubernetes runner backend** (`LIVE_RUNNER_BACKEND` != `local`): the backend creates a Kubernetes `Deployment` per run (control-plane pattern), which starts the runner container in-cluster. (If `LIVE_RUNNER_BACKEND` is unset, it is treated as Kubernetes mode.)

### Local live runs

1) Start backend + frontend as usual.

2) Start the local orchestrator (separate terminal). It watches the DB for `live_runs.runner_backend=local` and `status=starting`:

```
python backend/scripts/local_live_orchestrator.py
```

You can also start a specific run once by passing `run_id`:

```
python backend/scripts/local_live_orchestrator.py <run_id>
```

3) Start the Alpaca bar listener (another terminal). The runner consumes `alpaca_live_events` written by this process; without it, live bars will not arrive.

```
python backend/scripts/alpaca_live_listener.py
```

4) In the UI, start a Live run (paper/live). The API will return a hint if the orchestrator isn’t running yet.

Trading credentials:

- **Alpaca keys**: the runner requires `ALPACA_API_KEY` and `ALPACA_SECRET_KEY` in its environment. In local mode the orchestrator will also try to load them from Supabase profile settings (when service-role trading settings are configured); otherwise set them in your `.env`.
- **Placing orders**: the worker submits to Alpaca only when started with `--enable-trading` (the UI/API set this on the live run; the local orchestrator forwards it when you pass `--enable-trading` there).

### GKE live runs (runner deployments)

In Kubernetes mode the backend creates a per-run `Deployment` using these environment variables:

- **`LIVE_RUNNER_BACKEND`**: set to any value other than `local` to enable Kubernetes control-plane behavior
- **`LIVE_NAMESPACE`**: namespace to create runner deployments in (often injected via Downward API; see the example manifest)
- **`LIVE_RUNNER_IMAGE`**: runner container image (must include `backend/scripts/run_alpaca_strategy.py`). If unset, the backend uses `us-central1-docker.pkg.dev/traderchat/traderchat/vibetrader-live-runner:latest`.
- **`LIVE_RUNNER_SERVICE_ACCOUNT`** (optional): service account for runner pods
- **`LIVE_RUNNER_ENV_SECRET`** / **`LIVE_RUNNER_ENV_CONFIGMAP`** (optional): injected into the runner container via `envFrom` (use this to provide DB + Alpaca creds/config)
- **`LIVE_RUNNER_CPU`**, **`LIVE_RUNNER_MEMORY`** (optional): requests/limits for runner pods

RBAC: the backend needs permission to create/delete deployments in its namespace. Apply:

- `backend/k8s/live-runner-rbac.yaml`

Backend manifest snippet: see `backend/k8s/live-runner-deployment.example.yaml` for the env vars expected by the backend.

Runner image:

- A minimal runner image can be built from `backend/Dockerfile.runner` (it installs `backend/requirements.txt` and includes the `backend/` code). On GKE, build and push it with `./scripts/gke_live_runner.sh`.
- Your runner pods must have environment configured for DB access (and Alpaca keys if you want to trade).

Operational tips:

- The runner reads bars from `alpaca_live_events`; something must run `alpaca_live_listener.py` with DB and Alpaca credentials (that script is not started by the per-run Deployment—deploy it separately if cluster live runs should ingest bars).
- Each live run gets a deployment named like `live-run-<run_id_prefix>`.
- Use `/live/status?run_id=...` to see DB status plus whether the Kubernetes deployment exists.
- Use `kubectl logs deploy/<deployment>` (and `kubectl describe deploy/<deployment>`) to debug runner startup in GKE.

## Deploy (GKE)

Images are fixed to `us-central1-docker.pkg.dev/traderchat/traderchat/vibetrader-frontend:latest`, `…/vibetrader-backend:latest`, and `…/vibetrader-live-runner:latest`. Cluster: `autopilot-cluster-1` in `us-central1`, project `traderchat`, namespace `vibetrader`.

- **Frontend**: `./scripts/gke_frontend.sh`
- **Backend**: `./scripts/gke_backend.sh`
- **Live runner** (`backend/Dockerfile.runner` + `backend/k8s/live-runner-rbac.yaml`): `./scripts/gke_live_runner.sh`

You may set `LIVE_RUNNER_IMAGE` in `vibetrader-config` to override the same default URI (see `backend/k8s/live-runner-deployment.example.yaml`). Rebuild and push the live-runner image when runner code or dependencies change.

If you have docker related issues: 
```
sudo usermod -aG docker $USER && newgrp docker
```


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


## Precaching

PYTHONPATH=$(pwd)  python scripts/precache_alpaca_daily.py --timeframe 1d --workers 1 --years 10 --symbols-file ../snp500.txt