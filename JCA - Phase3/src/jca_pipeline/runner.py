"""Public entry points for running the pipeline against one or many PICO sets."""

import time
from typing import Any, Dict, List, Optional

from . import metrics as metrics_mod
from .graph import build_graph
from .nodes import PipelineState

_app = None


def _get_app():
    global _app
    if _app is None:
        _app = build_graph()
    return _app


def run_phase3(
    pico_set: Dict[str, Any],
    prompt_overrides: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Run a single PICO set through the full Phase 3 handoff pipeline.

    Returns only the final ValidationResult dict. Use run_phase3_detailed() if
    you also want per-agent latency/token metrics and every intermediate output.

    prompt_overrides: optional dict with any of "agent2_prompt", "agent3_prompt"
    keys to use edited prompt text instead of the bundled defaults in prompts.py
    for this run only (e.g. from a Streamlit prompt editor). Agent 1 has no
    prompt to override — it is pure deterministic code, not an LLM call.
    """
    return run_phase3_detailed(pico_set, prompt_overrides)["final_result"]


def run_phase3_detailed(
    pico_set: Dict[str, Any],
    prompt_overrides: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Same PICO set, full pipeline state back: agent1_output, agent2_output,
    deterministic_checks, semantic_checks, final_result, the raw per-call
    "metrics" list, and a "metrics_summary" (per-agent + overall latency/token
    totals, plus total_pipeline_latency_ms measured around the whole graph run)."""
    app = _get_app()
    initial_state: PipelineState = {
        "pico_set_id": pico_set.get("pico_set_id", "unknown"),
        "input_pico": pico_set,
    }
    if prompt_overrides:
        initial_state.update(prompt_overrides)  # type: ignore[typeddict-item]

    start = time.perf_counter()
    final_state = dict(app.invoke(initial_state))
    total_latency_ms = (time.perf_counter() - start) * 1000

    call_metrics = final_state.get("metrics", [])
    summary = metrics_mod.summarize(call_metrics)
    summary["total_pipeline_latency_ms"] = round(total_latency_ms, 1)
    final_state["metrics_summary"] = summary

    return final_state


def run_phase3_batch(
    pico_sets: List[Dict[str, Any]],
    prompt_overrides: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Run each PICO set through its own independent graph invocation.

    Matches Agent 3's own results[] schema — one set's failure never blocks
    or delays another's, per the "block only the specific failing project" rule.
    Each result dict carries its own "metrics_summary"; "aggregate_metrics" rolls
    per-call metrics up across every set in the batch.
    """
    results: List[Dict[str, Any]] = []
    all_call_metrics: List[Dict[str, Any]] = []
    total_latency_ms = 0.0

    for pico_set in pico_sets:
        state = run_phase3_detailed(pico_set, prompt_overrides)
        result = dict(state["final_result"])
        result["metrics_summary"] = state["metrics_summary"]
        result["agent2_output"] = state.get("agent2_output")
        result["semantic_checks"] = state.get("semantic_checks")
        results.append(result)

        all_call_metrics.extend(state.get("metrics", []))
        total_latency_ms += state["metrics_summary"]["total_pipeline_latency_ms"]

    aggregate = metrics_mod.summarize(all_call_metrics)
    aggregate["total_pipeline_latency_ms"] = round(total_latency_ms, 1)

    return {"results": results, "aggregate_metrics": aggregate}
