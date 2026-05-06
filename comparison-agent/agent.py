"""
comparison-agent/agent.py
Calls the Claude API to classify differences between production and shadow HTTP responses.
"""

import json
import logging
import os
from typing import Any

import anthropic

logger = logging.getLogger(__name__)

ANTHROPIC_API_KEY: str = os.environ["ANTHROPIC_API_KEY"]

# Singleton client — created once at import time.
_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

MODEL = "claude-sonnet-4-6"

SYSTEM_PROMPT = (
    "You are a traffic comparison agent. Analyze HTTP response pairs from production "
    "and shadow deployments. Classify differences as:\n"
    "- Safe: expected or cosmetic differences (e.g. timestamps, request IDs, whitespace)\n"
    "- Warning: potential issue that needs human review (e.g. different field values, "
    "extra/missing fields, status code differences that may be acceptable)\n"
    "- Critical: regression, data corruption, broken API contract, or functional breakage "
    "(e.g. 500 vs 200, missing required fields, changed data types, authentication failures)\n\n"
    "Return ONLY valid JSON with this exact structure:\n"
    '{"verdict": "Safe|Warning|Critical", "reasoning": "...", "diff_summary": "..."}\n\n'
    "reasoning: 1-3 sentences explaining why you chose this verdict.\n"
    "diff_summary: a concise bullet-point list of the specific differences observed."
)


def _format_headers(headers: dict[str, str]) -> str:
    """Format a headers dict as a readable string, omitting sensitive values."""
    sensitive = {"authorization", "cookie", "set-cookie", "x-api-key"}
    lines = []
    for k, v in sorted(headers.items()):
        if k.lower() in sensitive:
            lines.append(f"  {k}: [REDACTED]")
        else:
            lines.append(f"  {k}: {v}")
    return "\n".join(lines) if lines else "  (none)"


def _build_user_message(
    prod: dict[str, Any],
    shadow: dict[str, Any],
) -> str:
    """Build the user-turn message describing the response pair."""
    return (
        "## Production Response\n"
        f"Status: {prod['status']}\n"
        f"Headers:\n{_format_headers(prod.get('headers', {}))}\n"
        f"Body:\n{prod.get('body', '')[:4000]}\n\n"
        "## Shadow Response\n"
        f"Status: {shadow['status']}\n"
        f"Headers:\n{_format_headers(shadow.get('headers', {}))}\n"
        f"Body:\n{shadow.get('body', '')[:4000]}\n\n"
        "Analyze the differences and return your verdict as JSON."
    )


def compare_responses(
    prod_response: dict[str, Any],
    shadow_response: dict[str, Any],
) -> dict[str, str]:
    """
    Send a prod/shadow response pair to Claude for classification.

    Args:
        prod_response:   Dict with keys: status (int), headers (dict), body (str).
        shadow_response: Dict with keys: status (int), headers (dict), body (str).

    Returns:
        Dict with keys: verdict, reasoning, diff_summary.

    Raises:
        ValueError: If Claude returns malformed JSON or an invalid verdict.
        anthropic.APIError: On API-level failures.
    """
    user_message = _build_user_message(prod_response, shadow_response)

    response = _client.messages.create(
        model=MODEL,
        max_tokens=1024,
        system=[
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                # Enable prompt caching for the (static) system prompt.
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[
            {"role": "user", "content": user_message},
        ],
    )

    raw_text: str = response.content[0].text.strip()

    # Strip markdown code fences if Claude wrapped the JSON.
    if raw_text.startswith("```"):
        lines = raw_text.splitlines()
        # Remove opening fence (```json or ```) and closing fence.
        raw_text = "\n".join(lines[1:-1]) if lines[-1].startswith("```") else "\n".join(lines[1:])

    try:
        result = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Claude returned non-JSON response: {raw_text!r}") from exc

    verdict = result.get("verdict", "")
    if verdict not in ("Safe", "Warning", "Critical"):
        raise ValueError(f"Invalid verdict value: {verdict!r}")

    return {
        "verdict": verdict,
        "reasoning": result.get("reasoning", ""),
        "diff_summary": result.get("diff_summary", ""),
    }
