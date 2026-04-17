# VibeTrader

Chat-based trading strategy builder.

VibeTrader is a web app where you iterate on a trading strategy in a chat UI. The backend stores “threads” (conversations) and returns the updated message list plus data needed to render results/charts in the frontend.

## Setting up dev machine & user environment

If no gcloud, install gcloud: https://docs.cloud.google.com/sdk/docs/install-sdk
if no docker, install docker: https://docs.docker.com/engine/install/ubuntu/

If no kubctl, in kubectl and connect to the cluster
```
sudo apt-get install kubectl
sudo apt-get install google-cloud-cli-gke-gcloud-auth-plugin
gcloud container clusters get-credentials autopilot-cluster-1 --zone us-central1 --project traderchat
```

Log in to Google Cloud Platform, GCP Artifacts Registry
```
gcloud auth login 
gcloud auth configure-docker \
    us-central1-docker.pkg.dev
```
 
Update your .env from GCP configmap:
```
kubectl get configmap vibetrader-config -o json --namespace vibetrader |  jq -r '.data | to_entries[] | "\(.key)=\(.value)"' > .env
```

Install backend packages (better into a conda env!):
```
pip install -r backend/requirements.txt
```

On Linux, Codex `exec --full-auto` needs [bubblewrap](https://developers.openai.com/codex/concepts/sandboxing#prerequisites) on `PATH` (e.g. `sudo apt install bubblewrap`). If `bwrap` still cannot create user namespaces, apply the sysctl notes in `deploy/gke/README.md` (Codex section) on the host or cluster node. In Docker or Kubernetes where you cannot change those sysctls, set `CODEX_BYPASS_SANDBOX=1` for the backend process so Codex skips its sandbox (see the same deploy doc).

Install frontend packages:
```
cd frontend
npm install
```

## Run

Frontend:
```
npm run dev
```

Backend:
```
python backend/app.py
```

## Deploy

Build/deploy with:
```
./scripts/gke_build_push.sh && ./scripts/gke_deploy.sh
```

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

