#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="traderchat"
REGION="us-central1"
REPOSITORY="traderchat"
TAG="v1"

: "${PROJECT_ID:?set PROJECT_ID}"
: "${REGION:?set REGION (e.g. us-central1)}"
: "${REPOSITORY:?set REPOSITORY (Artifact Registry repo name)}"
: "${TAG:?set TAG (e.g. v1 or git SHA)}"

FRONTEND_IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPOSITORY}/vibetrader-frontend:${TAG}"
BACKEND_IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPOSITORY}/vibetrader-backend:${TAG}"

gcloud auth configure-docker "${REGION}-docker.pkg.dev"

docker buildx build --platform linux/amd64 -t "${FRONTEND_IMAGE}" ./frontend --push

docker buildx build --platform linux/amd64 -t "${BACKEND_IMAGE}" ./backend --push

echo "FRONTEND_IMAGE=${FRONTEND_IMAGE}"
echo "BACKEND_IMAGE=${BACKEND_IMAGE}"

