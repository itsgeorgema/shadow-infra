"""
comparison-agent/agent.py
Multi-step LangGraph analysis pipeline for classifying prod/shadow HTTP response differences.

Graph flow:
  structural_check → (fast path) → format_verdict
                   → (ambiguous) → extract_diffs → semantic_analysis → format_verdict
"""

import json
import logging
import os
from typing import Any, Literal

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field
from typing_extensions import TypedDict

logger = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-6"

class VerdictModel(BaseModel):
    verdict: Literal["Safe", "Warning", "Critical"] = Field(
        description="Classification of the response difference"
    )
    reasoning: str = Field(description="1-3 sentences explaining the verdict")
    diff_summary: str = Field(description="Concise bullet-point list of observed differences")


_llm = ChatAnthropic(
    model=MODEL,
    api_key=os.environ["ANTHROPIC_API_KEY"],
    max_tokens=1024,
)
_llm_structured = _llm.with_structured_output(VerdictModel)

SYSTEM_PROMPT = (
    "You are a traffic comparison agent. You will be given pre-computed structural flags, "
    "field-level diffs, and the raw HTTP responses from production and shadow deployments.\n\n"
    "Classify differences as:\n"
    "- Safe: expected or cosmetic differences (timestamps, request IDs, whitespace, ordering)\n"
    "- Warning: potential issues needing human review (field value changes, extra/missing optional "
    "fields, acceptable status code differences)\n"
    "- Critical: regressions, data corruption, or broken API contracts (5xx vs 2xx, missing "
    "required fields, changed data types, auth failures)\n\n"
    "Focus on functional impact. Use the structured diff data to ground your analysis."
)


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

class AnalysisState(TypedDict):
    prod_response: dict[str, Any]
    shadow_response: dict[str, Any]
    # Set by structural_check
    structural_flags: list[str]
    fast_path_verdict: str | None
    fast_path_reasoning: str
    # Set by extract_diffs
    field_diffs: dict[str, Any]
    # Final output
    verdict: str
    reasoning: str
    diff_summary: str


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------

def structural_check(state: AnalysisState) -> dict:
    """Rule-based fast path — catches obvious Critical/Warning cases without an LLM call."""
    prod = state["prod_response"]
    shadow = state["shadow_response"]
    flags: list[str] = []
    fast_verdict: str | None = None
    fast_reasoning = ""

    prod_status = prod.get("status", 0)
    shadow_status = shadow.get("status", 0)

    if shadow_status >= 500 and prod_status < 500:
        flags.append(f"status_mismatch: prod={prod_status} shadow={shadow_status} (shadow server error)")
        fast_verdict = "Critical"
        fast_reasoning = (
            f"Shadow returned {shadow_status} while production returned {prod_status}. "
            "Server errors on the shadow indicate a likely regression."
        )
    elif shadow_status in (401, 403) and prod_status not in (401, 403):
        flags.append(f"auth_failure: prod={prod_status} shadow={shadow_status}")
        fast_verdict = "Critical"
        fast_reasoning = (
            f"Shadow returned an auth failure ({shadow_status}) while production succeeded "
            f"({prod_status}). This indicates a broken authentication contract."
        )
    elif shadow_status != prod_status:
        flags.append(f"status_mismatch: prod={prod_status} shadow={shadow_status}")

    # Latency regression check
    prod_latency = prod.get("latency_ms") or 0
    shadow_latency = shadow.get("latency_ms") or 0
    if prod_latency > 0 and shadow_latency > 0:
        ratio = shadow_latency / prod_latency
        if ratio >= 10:
            flags.append(
                f"latency_critical: prod={prod_latency}ms shadow={shadow_latency}ms ({ratio:.1f}x slower)"
            )
            if fast_verdict is None:
                fast_verdict = "Critical"
                fast_reasoning = (
                    f"Shadow is {ratio:.1f}x slower than production "
                    f"({shadow_latency}ms vs {prod_latency}ms). "
                    "This level of latency regression indicates a serious performance problem."
                )
        elif ratio >= 3:
            flags.append(
                f"latency_warning: prod={prod_latency}ms shadow={shadow_latency}ms ({ratio:.1f}x slower)"
            )
            if fast_verdict is None:
                fast_verdict = "Warning"
                fast_reasoning = (
                    f"Shadow is {ratio:.1f}x slower than production "
                    f"({shadow_latency}ms vs {prod_latency}ms). "
                    "Performance degradation of this magnitude may impact user experience."
                )

    # Empty shadow body where production has content
    prod_body = prod.get("body", "")
    shadow_body = shadow.get("body", "")
    if prod_body.strip() and not shadow_body.strip():
        flags.append("empty_shadow_body: production has body, shadow returned empty")
        if fast_verdict is None:
            fast_verdict = "Critical"
            fast_reasoning = (
                "Shadow returned an empty body while production returned content. "
                "This indicates a missing or suppressed response."
            )

    return {
        "structural_flags": flags,
        "fast_path_verdict": fast_verdict,
        "fast_path_reasoning": fast_reasoning,
    }


def extract_diffs(state: AnalysisState) -> dict:
    """Compute structured field-level diffs to give the LLM better grounding."""
    prod_body = state["prod_response"].get("body", "")
    shadow_body = state["shadow_response"].get("body", "")
    diffs: dict[str, Any] = {}

    try:
        prod_json = json.loads(prod_body) if prod_body.strip() else None
        shadow_json = json.loads(shadow_body) if shadow_body.strip() else None
    except (json.JSONDecodeError, ValueError):
        prod_json = None
        shadow_json = None

    if isinstance(prod_json, dict) and isinstance(shadow_json, dict):
        prod_keys = set(prod_json.keys())
        shadow_keys = set(shadow_json.keys())
        changed: dict[str, Any] = {}
        for k in prod_keys & shadow_keys:
            if prod_json[k] != shadow_json[k]:
                pv, sv = prod_json[k], shadow_json[k]
                if type(pv) is not type(sv):
                    changed[k] = {"prod_type": type(pv).__name__, "shadow_type": type(sv).__name__}
                else:
                    changed[k] = {"prod": str(pv)[:200], "shadow": str(sv)[:200]}
        diffs = {
            "format": "json",
            "added_keys": sorted(shadow_keys - prod_keys),
            "removed_keys": sorted(prod_keys - shadow_keys),
            "changed_keys": changed,
        }
    else:
        prod_ct = state["prod_response"].get("headers", {}).get("Content-Type", "")
        shadow_ct = state["shadow_response"].get("headers", {}).get("Content-Type", "")
        diffs = {
            "format": "text",
            "prod_size": len(prod_body),
            "shadow_size": len(shadow_body),
            "size_delta": len(shadow_body) - len(prod_body),
        }
        if prod_ct != shadow_ct:
            diffs["content_type_change"] = {"prod": prod_ct, "shadow": shadow_ct}

    return {"field_diffs": diffs}


def semantic_analysis(state: AnalysisState) -> dict:
    """LLM node: classify using structured diff data + raw responses."""
    prod = state["prod_response"]
    shadow = state["shadow_response"]
    flags = state["structural_flags"]
    diffs = state["field_diffs"]

    sensitive = {"authorization", "cookie", "set-cookie", "x-api-key"}

    def _fmt_headers(h: dict) -> str:
        lines = [
            f"  {k}: {'[REDACTED]' if k.lower() in sensitive else v}"
            for k, v in sorted(h.items())
        ]
        return "\n".join(lines) or "  (none)"

    user_msg = (
        f"## Structural Flags\n"
        f"{chr(10).join(f'- {f}' for f in flags) or '(none)'}\n\n"
        f"## Field-Level Diffs\n{json.dumps(diffs, indent=2)}\n\n"
        f"## Production Response\n"
        f"Status: {prod['status']}\n"
        f"Latency: {prod.get('latency_ms', 'unknown')}ms\n"
        f"Headers:\n{_fmt_headers(prod.get('headers', {}))}\n"
        f"Body (truncated):\n{prod.get('body', '')[:2000]}\n\n"
        f"## Shadow Response\n"
        f"Status: {shadow['status']}\n"
        f"Latency: {shadow.get('latency_ms', 'unknown')}ms\n"
        f"Headers:\n{_fmt_headers(shadow.get('headers', {}))}\n"
        f"Body (truncated):\n{shadow.get('body', '')[:2000]}\n\n"
        "Analyze the differences and return your verdict."
    )

    messages = [
        SystemMessage(content=[{
            "type": "text",
            "text": SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }]),
        HumanMessage(content=user_msg),
    ]

    result: VerdictModel = _llm_structured.invoke(messages)
    return {
        "verdict": result.verdict,
        "reasoning": result.reasoning,
        "diff_summary": result.diff_summary,
    }


def format_verdict(state: AnalysisState) -> dict:
    """Assemble the final verdict from the fast path (if taken)."""
    if state.get("fast_path_verdict"):
        flags = state.get("structural_flags", [])
        return {
            "verdict": state["fast_path_verdict"],
            "reasoning": state.get("fast_path_reasoning", ""),
            "diff_summary": "\n".join(f"- {f}" for f in flags),
        }
    # Verdict already written to state by semantic_analysis.
    return {}


# ---------------------------------------------------------------------------
# Graph assembly
# ---------------------------------------------------------------------------

def _route_after_structural(state: AnalysisState) -> str:
    return "format_verdict" if state.get("fast_path_verdict") else "extract_diffs"


def _build_graph():
    graph = StateGraph(AnalysisState)
    graph.add_node("structural_check", structural_check)
    graph.add_node("extract_diffs", extract_diffs)
    graph.add_node("semantic_analysis", semantic_analysis)
    graph.add_node("format_verdict", format_verdict)

    graph.add_edge(START, "structural_check")
    graph.add_conditional_edges(
        "structural_check",
        _route_after_structural,
        {"format_verdict": "format_verdict", "extract_diffs": "extract_diffs"},
    )
    graph.add_edge("extract_diffs", "semantic_analysis")
    graph.add_edge("semantic_analysis", "format_verdict")
    graph.add_edge("format_verdict", END)
    return graph.compile()


_graph = _build_graph()


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def compare_responses(
    prod_response: dict[str, Any],
    shadow_response: dict[str, Any],
) -> dict[str, str]:
    """
    Run the multi-step LangGraph analysis on a prod/shadow response pair.

    Args:
        prod_response:   Dict with keys: status, headers, body, latency_ms (optional).
        shadow_response: Same shape.

    Returns:
        Dict with keys: verdict, reasoning, diff_summary.

    Raises:
        ValueError: If the graph produces an invalid or missing verdict.
    """
    initial_state: AnalysisState = {
        "prod_response": prod_response,
        "shadow_response": shadow_response,
        "structural_flags": [],
        "fast_path_verdict": None,
        "fast_path_reasoning": "",
        "field_diffs": {},
        "verdict": "",
        "reasoning": "",
        "diff_summary": "",
    }

    final_state = _graph.invoke(initial_state)

    verdict = final_state.get("verdict", "")
    if verdict not in ("Safe", "Warning", "Critical"):
        raise ValueError(f"Graph produced invalid verdict: {verdict!r}")

    return {
        "verdict": verdict,
        "reasoning": final_state.get("reasoning", ""),
        "diff_summary": final_state.get("diff_summary", ""),
    }
