## GKE deploy

### Build & push (Artifact Registry)

```bash
export PROJECT_ID="your-gcp-project"
export REGION="us-central1"
export REPOSITORY="your-artifact-registry-repo"
export TAG="v1"

./scripts/gke_build_push.sh
```

### Deploy to a GKE cluster

```bash
export PROJECT_ID="your-gcp-project"
export REGION="us-central1"
export CLUSTER="your-gke-cluster-name"

export FRONTEND_IMAGE="us-central1-docker.pkg.dev/your-gcp-project/your-artifact-registry-repo/vibetrader-frontend:v1"
export BACKEND_IMAGE="us-central1-docker.pkg.dev/your-gcp-project/your-artifact-registry-repo/vibetrader-backend:v1"

./scripts/gke_deploy.sh
```

