#!/usr/bin/env bash
set -euo pipefail

FRONTEND_IMAGE="us-central1-docker.pkg.dev/traderchat/traderchat/vibetrader-frontend:latest"
BACKEND_IMAGE="us-central1-docker.pkg.dev/traderchat/traderchat/vibetrader-backend:latest"

gcloud auth configure-docker "us-central1-docker.pkg.dev"

docker buildx build --platform linux/amd64 -t "${FRONTEND_IMAGE}" ./frontend --push

docker buildx build --platform linux/amd64 -t "${BACKEND_IMAGE}" ./backend --push

echo "FRONTEND_IMAGE=${FRONTEND_IMAGE}"
echo "BACKEND_IMAGE=${BACKEND_IMAGE}"

