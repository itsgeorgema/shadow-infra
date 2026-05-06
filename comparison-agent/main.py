"""
comparison-agent/main.py
FastAPI service that receives response pairs and returns LLM-classified verdicts.
"""

import logging
import os
from datetime import datetime, timezone
from typing import Any

import anthropic
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel
from supabase import Client, create_client

from agent import compare_responses

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

SUPABASE_URL: str = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY: str = os.environ["SUPABASE_SERVICE_KEY"]

supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

app = FastAPI(title="Shadow-Infra Comparison Agent", version="1.0.0")


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class HttpResponse(BaseModel):
    status: int
    headers: dict[str, str] = {}
    body: str = ""


class CompareRequest(BaseModel):
    deployment_id: str
    pair_id: str = ""
    prod_response: HttpResponse
    shadow_response: HttpResponse


class CompareResult(BaseModel):
    verdict_id: str
    pair_id: str
    verdict: str
    reasoning: str
    diff_summary: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.post("/compare", response_model=CompareResult, status_code=status.HTTP_200_OK)
async def compare(req: CompareRequest) -> CompareResult:
    """
    Classify the difference between a production and shadow HTTP response pair.

    1. Calls the Claude-backed agent to determine verdict.
    2. If pair_id is missing, creates a response_pairs row first.
    3. Stores the verdict in the verdicts table.
    4. Returns the structured verdict.
    """
    pair_id = req.pair_id

    # If the caller didn't provide a pair_id, insert a response_pairs row now.
    if not pair_id:
        now = datetime.now(timezone.utc).isoformat()
        try:
            pair_result = (
                supabase.table("response_pairs")
                .insert(
                    {
                        "deployment_id": req.deployment_id,
                        "request_path": "/unknown",
                        "request_method": "UNKNOWN",
                        "prod_status": req.prod_response.status,
                        "prod_headers": req.prod_response.headers,
                        "prod_body": req.prod_response.body,
                        "shadow_status": req.shadow_response.status,
                        "shadow_headers": req.shadow_response.headers,
                        "shadow_body": req.shadow_response.body,
                        "captured_at": now,
                    }
                )
                .execute()
            )
            pair_id = pair_result.data[0]["id"]
        except Exception as exc:
            logger.error("Failed to create response pair row: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Database error creating response pair: {exc}",
            ) from exc

    # Call the LLM agent.
    try:
        verdict_data = compare_responses(
            prod_response=req.prod_response.model_dump(),
            shadow_response=req.shadow_response.model_dump(),
        )
    except anthropic.APIError as exc:
        logger.error("Anthropic API error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"LLM API error: {exc}",
        ) from exc
    except ValueError as exc:
        logger.error("Agent returned invalid response: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Agent response error: {exc}",
        ) from exc

    # Persist the verdict.
    now = datetime.now(timezone.utc).isoformat()
    try:
        verdict_result = (
            supabase.table("verdicts")
            .insert(
                {
                    "pair_id": pair_id,
                    "verdict": verdict_data["verdict"],
                    "reasoning": verdict_data["reasoning"],
                    "diff_summary": verdict_data["diff_summary"],
                    "created_at": now,
                }
            )
            .execute()
        )
        verdict_id: str = verdict_result.data[0]["id"]
    except Exception as exc:
        logger.error("Failed to store verdict: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error storing verdict: {exc}",
        ) from exc

    logger.info(
        "Verdict %s for pair %s: %s", verdict_id, pair_id, verdict_data["verdict"]
    )

    return CompareResult(
        verdict_id=verdict_id,
        pair_id=pair_id,
        verdict=verdict_data["verdict"],
        reasoning=verdict_data["reasoning"],
        diff_summary=verdict_data["diff_summary"],
    )


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
