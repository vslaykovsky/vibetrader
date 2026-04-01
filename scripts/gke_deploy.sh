#!/usr/bin/env bash
set -euo pipefail

CLUSTER=autopilot-cluster-1
gcloud container clusters get-credentials "${CLUSTER}" --region "us-central1" --project "traderchat"

kubectl apply -f deploy/gke/namespace.yaml

kubectl apply -f deploy/gke/frontend-backendconfig.yaml

kubectl apply -f deploy/gke/frontend-deployment.yaml
kubectl apply -f deploy/gke/frontend-service.yaml

kubectl apply -f deploy/gke/backend-deployment.yaml
kubectl apply -f deploy/gke/backend-service.yaml

kubectl -n vibetrader rollout restart deploy/vibetrader-frontend
kubectl -n vibetrader rollout restart deploy/vibetrader-backend

kubectl -n vibetrader rollout status deploy/vibetrader-backend
kubectl -n vibetrader rollout status deploy/vibetrader-frontend

kubectl -n vibetrader get svc vibetrader-frontend

