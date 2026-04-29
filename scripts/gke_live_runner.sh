#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

gcloud auth configure-docker us-central1-docker.pkg.dev

docker buildx build --platform linux/amd64 -f backend/Dockerfile.runner -t us-central1-docker.pkg.dev/traderchat/traderchat/vibetrader-live-runner:latest ./backend --push

gcloud container clusters get-credentials autopilot-cluster-1 --region us-central1 --project traderchat

kubectl apply -f backend/k8s/live-runner-rbac.yaml
