#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

gcloud auth configure-docker us-central1-docker.pkg.dev

CODEX_AUTH_JSON_B64=""
if [ -f "${HOME}/.codex/auth.json" ]; then
  CODEX_AUTH_JSON_B64="$(base64 < "${HOME}/.codex/auth.json" | tr -d '\n')"
else
  echo "WARNING: ${HOME}/.codex/auth.json not found; building without baked-in Codex OAuth credentials" >&2
fi

docker buildx build --platform linux/amd64 \
  --build-arg CODEX_AUTH_JSON_B64="${CODEX_AUTH_JSON_B64}" \
  -t us-central1-docker.pkg.dev/traderchat/traderchat/vibetrader-backend:latest ./backend --push

gcloud container clusters get-credentials autopilot-cluster-1 --region us-central1 --project traderchat

kubectl apply -f deploy/gke/namespace.yaml
kubectl apply -f deploy/gke/live-runner-rbac.yaml
kubectl apply -f deploy/gke/backend-deployment.yaml
kubectl apply -f deploy/gke/backend-service.yaml

kubectl -n vibetrader rollout restart deploy/vibetrader-backend
kubectl -n vibetrader rollout status deploy/vibetrader-backend
