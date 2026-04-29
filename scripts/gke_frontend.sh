#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

gcloud auth configure-docker us-central1-docker.pkg.dev

docker buildx build --platform linux/amd64 -t us-central1-docker.pkg.dev/traderchat/traderchat/vibetrader-frontend:latest ./frontend --push

gcloud container clusters get-credentials autopilot-cluster-1 --region us-central1 --project traderchat

kubectl apply -f deploy/gke/namespace.yaml
kubectl apply -f deploy/gke/frontend-backendconfig.yaml
kubectl apply -f deploy/gke/frontend-deployment.yaml
kubectl apply -f deploy/gke/frontend-service.yaml

kubectl -n vibetrader rollout restart deploy/vibetrader-frontend
kubectl -n vibetrader rollout status deploy/vibetrader-frontend

kubectl -n vibetrader get svc vibetrader-frontend
