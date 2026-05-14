"""
pr-watcher/main.py
FastAPI service that receives GitHub webhook events, spins up / tears down
shadow containers, and tracks state in Supabase.
"""

import hashlib
import hmac
import logging
import os
from datetime import datetime, timezone
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Request, status
from supabase import Client, create_client

from manifest_parser import parse_manifest
from shadow_manager import (
    clear_traffic_splitter,
    patch_traffic_splitter,
    spin_up_shadow,
    tear_down_shadow,
)

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
GITHUB_WEBHOOK_SECRET: str = os.environ["GITHUB_WEBHOOK_SECRET"]
GITHUB_TOKEN: str = os.environ["GITHUB_TOKEN"]
SUPABASE_URL: str = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY: str = os.environ["SUPABASE_SERVICE_KEY"]

supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

app = FastAPI(title="Shadow-Infra PR Watcher", version="1.0.0")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _verify_signature(payload: bytes, sig_header: str) -> None:
    """Raise 403 if the GitHub HMAC-SHA256 signature is invalid."""
    if not sig_header or not sig_header.startswith("sha256="):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing or malformed X-Hub-Signature-256 header",
        )
    expected = "sha256=" + hmac.new(
        GITHUB_WEBHOOK_SECRET.encode(),
        payload,
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, sig_header):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid webhook signature",
        )


def _upsert_deployment(
    pr_number: int,
    pr_title: str,
    repo: str,
    branch: str,
    shadow_url: str,
    deployment_status: str,
) -> str:
    """Insert or update a shadow_deployments row. Returns the deployment ID."""
    now = datetime.now(timezone.utc).isoformat()

    # Check if a row already exists for this PR.
    existing = (
        supabase.table("shadow_deployments")
        .select("id")
        .eq("pr_number", pr_number)
        .eq("repo", repo)
        .execute()
    )

    if existing.data:
        dep_id: str = existing.data[0]["id"]
        supabase.table("shadow_deployments").update(
            {
                "shadow_url": shadow_url,
                "status": deployment_status,
                "updated_at": now,
            }
        ).eq("id", dep_id).execute()
        return dep_id

    result = (
        supabase.table("shadow_deployments")
        .insert(
            {
                "pr_number": pr_number,
                "pr_title": pr_title,
                "repo": repo,
                "branch": branch,
                "shadow_url": shadow_url,
                "status": deployment_status,
                "created_at": now,
                "updated_at": now,
            }
        )
        .execute()
    )
    return result.data[0]["id"]


def _set_deployment_status(pr_number: int, repo: str, new_status: str) -> None:
    """Update the status column for an existing deployment."""
    now = datetime.now(timezone.utc).isoformat()
    supabase.table("shadow_deployments").update(
        {"status": new_status, "updated_at": now}
    ).eq("pr_number", pr_number).eq("repo", repo).execute()


# ---------------------------------------------------------------------------
# Webhook endpoint
# ---------------------------------------------------------------------------

@app.post("/webhook", status_code=status.HTTP_200_OK)
async def github_webhook(
    request: Request,
    x_hub_signature_256: str = Header(default=""),
    x_github_event: str = Header(default=""),
) -> dict[str, Any]:
    """
    Receive GitHub webhook events.
    Handles pull_request events: opened, closed, reopened, synchronize.
    """
    payload_bytes = await request.body()
    _verify_signature(payload_bytes, x_hub_signature_256)

    if x_github_event != "pull_request":
        logger.info("Ignoring event type: %s", x_github_event)
        return {"status": "ignored", "event": x_github_event}

    body: dict[str, Any] = await request.json()
    action: str = body.get("action", "")
    pr: dict[str, Any] = body.get("pull_request", {})
    pr_number: int = pr.get("number", 0)
    pr_title: str = pr.get("title", "")
    repo: str = body.get("repository", {}).get("full_name", "")
    branch: str = pr.get("head", {}).get("ref", "")

    logger.info("PR #%d action=%s repo=%s branch=%s", pr_number, action, repo, branch)

    if action in ("opened", "reopened", "synchronize"):
        return await _handle_pr_open(pr_number, pr_title, repo, branch)

    if action in ("closed",):
        return await _handle_pr_close(pr_number, repo)

    return {"status": "ignored", "action": action}


async def _handle_pr_open(
    pr_number: int, pr_title: str, repo: str, branch: str
) -> dict[str, Any]:
    """Spin up a shadow container and register it in Supabase."""
    try:
        service_info = parse_manifest(repo, branch, GITHUB_TOKEN)
    except ValueError as exc:
        logger.warning("Could not parse manifest for PR #%d: %s", pr_number, exc)
        return {"status": "skipped", "reason": str(exc)}

    image: str = service_info["image"]
    port: str = service_info["port"]

    try:
        shadow_url = spin_up_shadow(pr_number, image, port)
    except RuntimeError as exc:
        logger.error("Failed to spin up shadow for PR #%d: %s", pr_number, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Shadow container failed to start: {exc}",
        ) from exc

    dep_id = _upsert_deployment(
        pr_number=pr_number,
        pr_title=pr_title,
        repo=repo,
        branch=branch,
        shadow_url=shadow_url,
        deployment_status="active",
    )

    try:
        patch_traffic_splitter(shadow_url=shadow_url, deployment_id=dep_id)
    except Exception as exc:
        logger.warning("Could not patch traffic-splitter (non-fatal): %s", exc)

    logger.info("Shadow deployment %s created for PR #%d at %s", dep_id, pr_number, shadow_url)
    return {
        "status": "shadow_started",
        "deployment_id": dep_id,
        "shadow_url": shadow_url,
    }


async def _handle_pr_close(pr_number: int, repo: str) -> dict[str, Any]:
    """Tear down the shadow container and mark the deployment as closed."""
    try:
        tear_down_shadow(pr_number)
    except RuntimeError as exc:
        logger.error("Failed to tear down shadow for PR #%d: %s", pr_number, exc)
        # Don't surface a 500 — the PR is closed regardless.

    try:
        clear_traffic_splitter()
    except Exception as exc:
        logger.warning("Could not clear traffic-splitter (non-fatal): %s", exc)

    _set_deployment_status(pr_number, repo, "closed")
    logger.info("Shadow for PR #%d torn down and marked closed", pr_number)
    return {"status": "shadow_stopped", "pr_number": pr_number}


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
