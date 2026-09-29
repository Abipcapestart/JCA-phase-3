"""Token usage, latency, and cost tracking for every call in the pipeline
(LLM calls via llm.call_structured, plus Agent 1's own code-only metric).

Each call produces one metric dict:
    {
        "agent": "agent1_lr_handoff_assembly",
        "model_id": "...",
        "latency_ms": 842.3,
        "input_tokens": 1204,
        "output_tokens": 187,
        "total_tokens": 1391,
        "cost_usd": 0.008865,
        "started_at": "2026-09-17T10:02:11.123456+00:00",
    }

These accumulate in PipelineState["metrics"] across a single run. `summarize()`
rolls a list of them up into per-agent and overall totals for display.
"""

from typing import Any, Dict, List


def summarize(metrics: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate a list of per-call metric dicts into per-agent + overall totals."""
    per_agent: Dict[str, Dict[str, Any]] = {}

    for m in metrics:
        agent = m.get("agent", "unknown")
        bucket = per_agent.setdefault(
            agent,
            {
                "calls": 0,
                "latency_ms": 0.0,
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "cost_usd": 0.0,
            },
        )
        bucket["calls"] += 1
        bucket["latency_ms"] += m.get("latency_ms") or 0.0
        bucket["input_tokens"] += m.get("input_tokens") or 0
        bucket["output_tokens"] += m.get("output_tokens") or 0
        bucket["total_tokens"] += m.get("total_tokens") or 0
        bucket["cost_usd"] += m.get("cost_usd") or 0.0

    for bucket in per_agent.values():
        bucket["latency_ms"] = round(bucket["latency_ms"], 1)
        bucket["cost_usd"] = round(bucket["cost_usd"], 6)

    overall = {
        "calls": sum(b["calls"] for b in per_agent.values()),
        "latency_ms": round(sum(b["latency_ms"] for b in per_agent.values()), 1),
        "input_tokens": sum(b["input_tokens"] for b in per_agent.values()),
        "output_tokens": sum(b["output_tokens"] for b in per_agent.values()),
        "total_tokens": sum(b["total_tokens"] for b in per_agent.values()),
        "cost_usd": round(sum(b["cost_usd"] for b in per_agent.values()), 6),
    }

    return {"per_agent": per_agent, "overall": overall}
