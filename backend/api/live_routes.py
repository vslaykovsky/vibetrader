from __future__ import annotations

import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any

from flask import Blueprint, Response, g, jsonify, request

from application.services.alpaca_live_db import delete_runner_subscriptions
from auth import require_auth
from db.models import LiveRun, LiveRunEvent, Strategy
from db.session import SessionLocal
from services.agent import STRATEGIES_DIR, thread_id_allowed
from services.supabase_trading_settings import (
    fetch_alpaca_account_for_user,
    fetch_profile_alpaca_keys,
    service_role_configured,
)

import requests

logger = logging.getLogger(__name__)

LIVE_RUNNER_IMAGE_DEFAULT = (
    "us-central1-docker.pkg.dev/traderchat/traderchat/vibetrader-live-runner:latest"
)

live_blueprint = Blueprint("live", __name__)


def _user_sub_is_uuid(user_id: str | None) -> bool:
    if not user_id or not str(user_id).strip():
        return False
    try:
        uuid.UUID(str(user_id).strip())
        return True
    except ValueError:
        return False


def _live_use_kubernetes() -> bool:
    if (os.environ.get("LIVE_RUNNER_BACKEND") or "").strip().lower() == "local":
        return False
    host = (os.environ.get("KUBERNETES_SERVICE_HOST") or "").strip()
    return bool(host)


def _bad(message: str, code: int = 400) -> tuple:
    return jsonify({"error": message}), code


_SA_TOKEN_PATH = Path("/var/run/secrets/kubernetes.io/serviceaccount/token")
_SA_CA_PATH = Path("/var/run/secrets/kubernetes.io/serviceaccount/ca.crt")


def _k8s_api_base() -> str:
    host = (os.environ.get("KUBERNETES_SERVICE_HOST") or "").strip()
    port = (os.environ.get("KUBERNETES_SERVICE_PORT") or "").strip() or "443"
    if not host:
        raise RuntimeError("KUBERNETES_SERVICE_HOST not set (not running in-cluster?)")
    return f"https://{host}:{port}"


def _k8s_namespace() -> str:
    return (
        (os.environ.get("LIVE_NAMESPACE") or "").strip()
        or (os.environ.get("POD_NAMESPACE") or "").strip()
        or "default"
    )


def _k8s_headers() -> dict[str, str]:
    token = _SA_TOKEN_PATH.read_text(encoding="utf-8").strip()
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _k8s_verify():
    return str(_SA_CA_PATH) if _SA_CA_PATH.exists() else True


def _k8s_request(method: str, path: str, *, json_body: dict | None = None) -> requests.Response:
    base = _k8s_api_base()
    url = base.rstrip("/") + path
    resp = requests.request(
        method=method.upper(),
        url=url,
        headers=_k8s_headers(),
        json=json_body,
        verify=_k8s_verify(),
        timeout=15,
    )
    return resp


def _runner_image() -> str:
    img = (os.environ.get("LIVE_RUNNER_IMAGE") or "").strip()
    return img if img else LIVE_RUNNER_IMAGE_DEFAULT


def _runner_env_from() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    sec = (os.environ.get("LIVE_RUNNER_ENV_SECRET") or "").strip()
    if sec:
        out.append({"secretRef": {"name": sec}})
    cm = (os.environ.get("LIVE_RUNNER_ENV_CONFIGMAP") or "").strip()
    if cm:
        out.append({"configMapRef": {"name": cm}})
    return out


def _runner_resources() -> dict[str, Any] | None:
    cpu = (os.environ.get("LIVE_RUNNER_CPU") or "").strip()
    mem = (os.environ.get("LIVE_RUNNER_MEMORY") or "").strip()
    if not cpu and not mem:
        return None
    reqs: dict[str, str] = {}
    lims: dict[str, str] = {}
    if cpu:
        reqs["cpu"] = cpu
        lims["cpu"] = cpu
    if mem:
        reqs["memory"] = mem
        lims["memory"] = mem
    return {"requests": reqs, "limits": lims}


def _deployment_name(run_id: str) -> str:
    safe = "".join(c for c in run_id.lower() if c.isalnum() or c == "-")
    short = safe.replace("-", "")[:16]
    return f"live-run-{short}"


def _strategy_entry_path(thread_id: str) -> Path:
    return Path(STRATEGIES_DIR) / thread_id / "strategy.py"


def _deployment_manifest(
    *,
    run_id: str,
    thread_id: str,
    paper: bool,
    enable_trading: bool,
    user_id: str,
    user_email: str | None,
    alpaca_api_key: str | None = None,
    alpaca_secret_key: str | None = None,
) -> dict[str, Any]:
    name = _deployment_name(run_id)
    namespace = _k8s_namespace()
    entry = _strategy_entry_path(thread_id)
    env_from = _runner_env_from()
    resources = _runner_resources()
    sa = (os.environ.get("LIVE_RUNNER_SERVICE_ACCOUNT") or "").strip()

    args = [
        "python",
        "scripts/run_alpaca_strategy.py",
        "--entry",
        str(entry),
        "--run-id",
        run_id,
        "--runner-id",
        run_id,
        "--created-by",
        user_id,
    ]
    if user_email:
        args += ["--created-by-email", user_email]
    if paper:
        args.append("--paper")
    if enable_trading:
        args.append("--enable-trading")

    container: dict[str, Any] = {
        "name": "runner",
        "image": _runner_image(),
        "imagePullPolicy": "Always",
        "args": args,
        "envFrom": env_from or None,
    }
    ak = (alpaca_api_key or "").strip()
    sk = (alpaca_secret_key or "").strip()
    if ak and sk:
        container["env"] = [
            {"name": "ALPACA_API_KEY", "value": ak},
            {"name": "ALPACA_SECRET_KEY", "value": sk},
        ]
    pod_spec: dict[str, Any] = {
        "containers": [container],
        "restartPolicy": "Always",
    }
    if sa:
        pod_spec["serviceAccountName"] = sa
    if resources:
        pod_spec["containers"][0]["resources"] = resources
    pod_spec["containers"][0] = {k: v for k, v in pod_spec["containers"][0].items() if v is not None}

    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "labels": {
                "app": "live-runner",
                "run_id": run_id,
                "thread_id": thread_id,
            },
        },
        "spec": {
            "replicas": 1,
            "strategy": {"type": "Recreate"},
            "selector": {"matchLabels": {"app": "live-runner", "run_id": run_id}},
            "template": {
                "metadata": {"labels": {"app": "live-runner", "run_id": run_id, "thread_id": thread_id}},
                "spec": pod_spec,
            },
        },
    }


@live_blueprint.post("/live/start")
@require_auth
def live_start() -> tuple:
    payload = request.get_json(silent=True) or {}
    thread_id = str(payload.get("thread_id", "")).strip()
    if not thread_id or not thread_id_allowed(thread_id):
        return _bad("invalid or missing thread_id")
    deployed_from_run_id = str(payload.get("deployed_from_run_id") or "").strip()
    entry = _strategy_entry_path(thread_id)
    if not entry.is_file():
        return _bad(f"strategy entry not found: {entry}", 404)

    run_id = str(payload.get("run_id") or "").strip() or str(uuid.uuid4())
    paper = bool(payload.get("paper", True))
    enable_trading = bool(payload.get("enable_trading", False))
    alpaca_account_id = str(payload.get("alpaca_account_id") or "").strip()

    alpaca_pair: tuple[str, str] | None = None
    alpaca_account_row: dict[str, Any] | None = None
    use_supabase_trading = service_role_configured() and _user_sub_is_uuid(getattr(g, "user_id", None))
    if use_supabase_trading:
        alpaca_pair = fetch_profile_alpaca_keys(str(g.user_id))
        if not alpaca_pair:
            return _bad("Add Alpaca API credentials in Dashboard → Settings", 400)
        if not alpaca_account_id:
            return _bad("alpaca_account_id is required", 400)
        try:
            uuid.UUID(alpaca_account_id)
        except ValueError:
            return _bad("invalid alpaca_account_id")
        alpaca_account_row = fetch_alpaca_account_for_user(str(g.user_id), alpaca_account_id)
        if not alpaca_account_row:
            return _bad("Alpaca account not found", 404)
        paper = not bool(alpaca_account_row.get("is_live"))

    use_k8s = _live_use_kubernetes()
    runner_backend = "kubernetes" if use_k8s else "local"
    status_text = "creating deployment" if use_k8s else "waiting for local worker"
    session = SessionLocal()
    try:
        run = LiveRun(
            id=run_id,
            thread_id=thread_id,
            created_by=str(getattr(g, "user_id", "") or ""),
            created_by_email=getattr(g, "user_email", None),
            mode="paper" if paper else "live",
            status="starting",
            status_text=status_text,
            entry_path=str(entry),
            deployed_from_run_id=deployed_from_run_id,
            alpaca_account_id=alpaca_account_id if use_supabase_trading else "",
            runner_backend=runner_backend,
            runner_id=run_id[:64],
            last_input_event_id=0,
        )
        session.merge(run)
        session.add(
            LiveRunEvent(
                run_id=run_id,
                kind="status",
                unixtime=int(time.time()),
                payload={"status": "starting"},
            )
        )
        session.commit()
    finally:
        session.close()

    if not use_k8s:
        return (
            jsonify(
                {
                    "ok": True,
                    "run_id": run_id,
                    "runner_backend": "local",
                    "deployment": None,
                    "hint": "Start workers: python scripts/local_live_orchestrator.py (watches DB) or python scripts/local_live_orchestrator.py <run_id>",
                }
            ),
            200,
        )

    manifest = _deployment_manifest(
        run_id=run_id,
        thread_id=thread_id,
        paper=paper,
        enable_trading=enable_trading,
        user_id=str(g.user_id),
        user_email=getattr(g, "user_email", None),
        alpaca_api_key=alpaca_pair[0] if alpaca_pair else None,
        alpaca_secret_key=alpaca_pair[1] if alpaca_pair else None,
    )
    ns = _k8s_namespace()
    resp = _k8s_request("POST", f"/apis/apps/v1/namespaces/{ns}/deployments", json_body=manifest)
    if resp.status_code not in (200, 201, 409):
        logger.error("live_start k8s error status=%s body=%s", resp.status_code, resp.text[:2000])
        return _bad(f"failed to create deployment (status={resp.status_code})", 502)
    return (
        jsonify(
            {
                "ok": True,
                "run_id": run_id,
                "runner_backend": "kubernetes",
                "deployment": _deployment_name(run_id),
            }
        ),
        200,
    )


@live_blueprint.get("/live/runs")
@require_auth
def live_runs() -> tuple:
    uid = str(g.user_id)
    thread_id = (request.args.get("thread_id") or "").strip()
    run_id_filter = (request.args.get("run_id") or "").strip()
    limit_raw = (request.args.get("limit") or "").strip()
    limit = 50
    if limit_raw:
        try:
            limit = max(1, min(200, int(limit_raw)))
        except ValueError:
            return _bad("limit must be an integer")

    if run_id_filter:
        try:
            uuid.UUID(run_id_filter)
        except ValueError:
            return _bad("invalid run_id", 400)

    session = SessionLocal()
    try:
        q = session.query(LiveRun).filter(LiveRun.created_by == uid)
        if thread_id:
            q = q.filter(LiveRun.thread_id == thread_id)
        if run_id_filter:
            q = q.filter(LiveRun.id == run_id_filter)
        rows = q.order_by(LiveRun.created_at.desc()).limit(limit).all()
        strat_ids = [
            x
            for x in (str(getattr(row, "deployed_from_run_id", "") or "").strip() for row in rows)
            if x
        ]
        strat_by_id: dict[str, Strategy] = {}
        if strat_ids:
            for s in session.query(Strategy).filter(Strategy.id.in_(strat_ids)).all():
                strat_by_id[s.id] = s

        aid_seen: set[str] = set()
        label_by_aid: dict[str, str] = {}
        for row in rows:
            aid = str(getattr(row, "alpaca_account_id", "") or "").strip()
            if not aid or aid in aid_seen:
                continue
            aid_seen.add(aid)
            acc_row = fetch_alpaca_account_for_user(uid, aid)
            if acc_row:
                lab = str(acc_row.get("label") or "").strip()
                acct = str(acc_row.get("account") or "").strip()
                label_by_aid[aid] = lab or acct

        def _serialize_live_run(r: LiveRun) -> dict[str, Any]:
            sid = str(getattr(r, "deployed_from_run_id", "") or "").strip()
            strat = strat_by_id.get(sid) if sid else None
            name = (strat.strategy_name or "").strip() if strat else ""
            bt_at = strat.created_at.isoformat() if strat and strat.created_at else None
            aid = str(getattr(r, "alpaca_account_id", "") or "").strip()
            acct_label = label_by_aid.get(aid, "") if aid else ""
            return {
                "run_id": r.id,
                "thread_id": r.thread_id,
                "mode": r.mode,
                "status": r.status,
                "status_text": r.status_text,
                "entry_path": r.entry_path,
                "deployed_from_run_id": sid,
                "strategy_name": name,
                "backtest_at": bt_at,
                "alpaca_account_id": aid,
                "alpaca_account_label": acct_label,
                "runner_backend": getattr(r, "runner_backend", "") or "kubernetes",
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            }

        return (
            jsonify({"runs": [_serialize_live_run(r) for r in rows]}),
            200,
        )
    finally:
        session.close()


@live_blueprint.post("/live/stop")
@require_auth
def live_stop() -> tuple:
    payload = request.get_json(silent=True) or {}
    run_id = str(payload.get("run_id", "")).strip()
    if not run_id:
        return _bad("missing run_id")
    name = _deployment_name(run_id)
    deleted = False
    use_k8s = _live_use_kubernetes()
    if use_k8s:
        ns = _k8s_namespace()
        resp = _k8s_request("DELETE", f"/apis/apps/v1/namespaces/{ns}/deployments/{name}")
        if resp.status_code not in (200, 202, 404):
            logger.error("live_stop k8s error status=%s body=%s", resp.status_code, resp.text[:2000])
            return _bad(f"failed to delete deployment (status={resp.status_code})", 502)
        deleted = resp.status_code != 404
    else:
        deleted = False

    session = SessionLocal()
    try:
        row = session.get(LiveRun, run_id)
        if row is not None:
            if use_k8s:
                row.status = "stopping"
                ev_status = "stopping"
            else:
                row.status = "stopped"
                ev_status = "stopped"
                rid = ((row.runner_id or "").strip() or run_id)[:64]
                delete_runner_subscriptions(session, runner_id=rid)
            row.status_text = ""
            row.updated_at = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
            session.add(row)
        else:
            ev_status = "stopping" if use_k8s else "stopped"
        session.add(
            LiveRunEvent(
                run_id=run_id,
                kind="status",
                unixtime=int(time.time()),
                payload={"status": ev_status},
            )
        )
        session.commit()
    finally:
        session.close()

    return jsonify({"ok": True, "run_id": run_id, "deployment": name, "deleted": deleted}), 200


@live_blueprint.get("/live/status")
@require_auth
def live_status() -> tuple:
    run_id = (request.args.get("run_id") or "").strip()
    if not run_id:
        return _bad("missing run_id")
    name = _deployment_name(run_id)
    dep_resp_code = 0
    dep_json: dict | None = None
    if _live_use_kubernetes():
        ns = _k8s_namespace()
        dep_resp = _k8s_request("GET", f"/apis/apps/v1/namespaces/{ns}/deployments/{name}")
        dep_resp_code = int(dep_resp.status_code)
        if dep_resp.status_code == 200:
            try:
                dep_json = dep_resp.json()
            except Exception:
                dep_json = None
    else:
        ns = ""

    session = SessionLocal()
    try:
        row = session.get(LiveRun, run_id)
        db_status = None
        if row is not None:
            db_status = {
                "run_id": row.id,
                "thread_id": row.thread_id,
                "mode": row.mode,
                "status": row.status,
                "status_text": row.status_text,
                "entry_path": row.entry_path,
                "deployed_from_run_id": getattr(row, "deployed_from_run_id", "") or "",
                "alpaca_account_id": getattr(row, "alpaca_account_id", "") or "",
                "runner_backend": getattr(row, "runner_backend", "") or "kubernetes",
                "runner_id": row.runner_id,
                "last_input_event_id": int(row.last_input_event_id or 0),
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            }
    finally:
        session.close()

    if _live_use_kubernetes():
        k8s = {
            "namespace": ns,
            "deployment": name,
            "deployment_exists": dep_resp_code == 200,
            "deployment_status_code": dep_resp_code,
            "deployment_status": (dep_json or {}).get("status") if isinstance(dep_json, dict) else None,
        }
    else:
        k8s = {
            "namespace": None,
            "deployment": name,
            "deployment_exists": False,
            "deployment_status_code": None,
            "deployment_status": None,
            "skipped": True,
        }
    return jsonify({"ok": True, "run_id": run_id, "db": db_status, "k8s": k8s}), 200


@live_blueprint.get("/live/stream")
@require_auth
def live_stream() -> tuple | Response:
    run_id = (request.args.get("run_id") or "").strip()
    if not run_id:
        return _bad("missing run_id")
    raw_after = (request.args.get("after_id") or "").strip()
    after_id = 0
    if raw_after:
        try:
            after_id = int(raw_after)
        except ValueError:
            return _bad("after_id must be an integer")

    def generate():
        last_keepalive = time.monotonic()
        cur_after = int(after_id)
        while True:
            session = SessionLocal()
            try:
                rows = (
                    session.query(LiveRunEvent)
                    .filter(LiveRunEvent.run_id == run_id, LiveRunEvent.id > cur_after)
                    .order_by(LiveRunEvent.id.asc())
                    .limit(500)
                    .all()
                )
            finally:
                session.close()

            if not rows:
                if (time.monotonic() - last_keepalive) >= 15:
                    yield ": keepalive\n\n"
                    last_keepalive = time.monotonic()
                time.sleep(0.25)
                continue

            for ev in rows:
                cur_after = int(ev.id)
                payload = {
                    "id": int(ev.id),
                    "run_id": ev.run_id,
                    "kind": ev.kind,
                    "unixtime": ev.unixtime,
                    "payload": dict(ev.payload or {}),
                }
                yield f"id: {ev.id}\n"
                yield f"event: {ev.kind}\n"
                yield "data: " + json.dumps(payload, ensure_ascii=False, default=str) + "\n\n"
                last_keepalive = time.monotonic()

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )

