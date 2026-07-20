## GKE deploy

### Build, push, and deploy (Artifact Registry + GKE)

Images and cluster are hardcoded in `scripts/gke_*.sh` to `us-central1-docker.pkg.dev/traderchat/traderchat/vibetrader-frontend:latest`, `…/vibetrader-backend:latest`, `…/vibetrader-live-runner:latest`, `…/vibetrader-alpaca-listener:latest`, cluster `cluster-standard` (`us-central1`), project `traderchat`.

From the repo root:

```bash
./scripts/gke_frontend.sh
./scripts/gke_backend.sh
./scripts/gke_live_runner.sh
./scripts/gke_alpaca_listener.sh
```

The backend ConfigMap must include `REDIS_URL` for user message quotas and strategy event streaming. Apply the Supabase migrations through `20260719203000_add_profile_eula_acceptance.sql` before deploying this backend version; protected APIs fail closed until the EULA fields are available.

The backend defaults `LIVE_RUNNER_IMAGE` to `us-central1-docker.pkg.dev/traderchat/traderchat/vibetrader-live-runner:latest`; set it in `vibetrader-config` only to override (see `deploy/gke/live-runner-deployment.example.yaml`).

### Rust strategy engine

Enable the Rust engine in the existing backend ConfigMap:

```bash
kubectl -n vibetrader patch configmap vibetrader-config \
  --type merge \
  -p '{"data":{"STRATEGY_ENGINE":"rust"}}'
./scripts/gke_backend.sh
```

ConfigMap changes do not update environment variables in existing Pods, so restart or redeploy the backend after changing the value. The backend image contains pinned Rust/Cargo, prebuilds the strategy binaries during the image build, validates the toolchain and writable Cargo target at startup, and `gke_backend.sh` verifies the same toolchain inside the rolled-out Pod. Dynamically created live-runner Pods receive the selected `STRATEGY_ENGINE` explicitly; rebuild the runner image with `./scripts/gke_live_runner.sh` before starting Rust live runs.

### Codex `exec --full-auto` (bubblewrap)

The backend invokes Codex with `--full-auto` (workspace-write sandbox), which runs shell commands through [bubblewrap](https://github.com/containers/bubblewrap). The backend image installs the `bubblewrap` package so `bwrap` is on `PATH` (Codex prefers the distro binary over the vendored helper).

If logs still show `bwrap: No permissions to create a new namespace`, the **kernel** on the machine running the container must allow unprivileged user namespaces (and on some Ubuntu systems AppArmor must not block them). Typical fixes on the **node or host** (not inside the app container):

```bash
sudo sysctl -w kernel.unprivileged_userns_clone=1
```

```bash
sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0
```

Persist with drop-ins under `/etc/sysctl.d/` on that host and `sysctl --system` (or reboot). See [Codex sandboxing prerequisites](https://developers.openai.com/codex/concepts/sandboxing#prerequisites).

#### Applying these on Google Cloud

These settings are **node OS / VM kernel** parameters. They are **not** set through Kubernetes `ConfigMap` env vars, Pod `securityContext.sysctls`, or your backend container image alone (except by choosing a bypass; see below).

**GKE Autopilot:** Node OS is not customizable for arbitrary `kernel.*` tuning. Use `CODEX_BYPASS_SANDBOX=1` in `vibetrader-config` rather than these sysctls.

**GKE Standard:** [Node system configuration](https://cloud.google.com/kubernetes-engine/docs/how-to/node-system-config) (`gcloud container clusters create … --system-config-from-file=…` or the same on node pool create/update) only permits **Google’s documented sysctl allowlist**. `kernel.unprivileged_userns_clone` and `kernel.apparmor_restrict_unprivileged_userns` are **not** on that list, so you **cannot** enable them through `--system-config-from-file` today.

If you must change them anyway on Standard (strong security and ops review required), typical approaches are: **custom node image**, **Sole Tenant** / self-managed nodes where you control the OS, or a **privileged DaemonSet** that applies `sysctl` on the host (Google’s pattern: [Automatically bootstrap GKE nodes with DaemonSets](https://cloud.google.com/kubernetes-engine/docs/tutorials/automatically-bootstrapping-gke-nodes-with-daemonsets)). You are responsible for persistence across reboots, upgrades, and blast radius.

**Compute Engine without GKE:** If the API runs on a VM, use a [startup script](https://cloud.google.com/compute/docs/instances/startup-scripts) to run the same `sysctl -w` lines and append stable values under `/etc/sysctl.d/`.

**Practical default for this backend on GKE:** If you do not own node hardening, set `CODEX_BYPASS_SANDBOX=1` in `vibetrader-config` so Codex uses `--dangerously-bypass-approvals-and-sandbox` instead of `--full-auto`. Treat the Pod and cluster policy as the isolation boundary; Codex then runs without bubblewrap.
