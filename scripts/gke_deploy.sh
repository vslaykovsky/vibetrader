#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="traderchat"
REGION="us-central1"
REPOSITORY="traderchat"
TAG="${TAG:-}"

if [[ -z "${TAG}" ]]; then
GIT_SHA="$(git rev-parse --short HEAD)"
DIRTY_SUFFIX=""
git diff --quiet || DIRTY_SUFFIX="-dirty"
git diff --cached --quiet || DIRTY_SUFFIX="-dirty"
TAG="${GIT_SHA}${DIRTY_SUFFIX}"
fi
CLUSTER=autopilot-cluster-1
FRONTEND_IMAGE="us-central1-docker.pkg.dev/${PROJECT_ID}/${REPOSITORY}/vibetrader-frontend:${TAG}"
BACKEND_IMAGE="us-central1-docker.pkg.dev/${PROJECT_ID}/${REPOSITORY}/vibetrader-backend:${TAG}"


: "${CLUSTER:?set CLUSTER}"
: "${REGION:?set REGION (or zone, if you change get-credentials)}"
: "${PROJECT_ID:?set PROJECT_ID}"
: "${FRONTEND_IMAGE:?set FRONTEND_IMAGE}"
: "${BACKEND_IMAGE:?set BACKEND_IMAGE}"

gcloud container clusters get-credentials "${CLUSTER}" --region "${REGION}" --project "${PROJECT_ID}"

kubectl apply -f deploy/gke/namespace.yaml

tmpdir="$(mktemp -d)"
trap 'rm -rf "${tmpdir}"' EXIT

sed "s|FRONTEND_IMAGE|${FRONTEND_IMAGE}|g" deploy/gke/frontend-deployment.yaml > "${tmpdir}/frontend-deployment.yaml"
sed "s|BACKEND_IMAGE|${BACKEND_IMAGE}|g" deploy/gke/backend-deployment.yaml > "${tmpdir}/backend-deployment.yaml"

kubectl apply -f "${tmpdir}/frontend-deployment.yaml"
kubectl apply -f deploy/gke/frontend-service.yaml

kubectl apply -f "${tmpdir}/backend-deployment.yaml"
kubectl apply -f deploy/gke/backend-service.yaml

kubectl -n vibetrader rollout restart deploy/vibetrader-frontend
kubectl -n vibetrader rollout restart deploy/vibetrader-backend

kubectl -n vibetrader rollout status deploy/vibetrader-backend
kubectl -n vibetrader rollout status deploy/vibetrader-frontend

kubectl -n vibetrader get svc vibetrader-frontend

