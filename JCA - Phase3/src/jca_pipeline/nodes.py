"""State schema and node functions for the Phase 3 LangGraph pipeline.

Node responsibilities:
  precheck               -> code, no LLM
  agent1                  -> LLM (LR Handoff Assembly Agent) + code fidelity post-check
  compute_categories       -> code
  agent2                  -> LLM (Study Title & Objectives Generation Agent)
  deterministic_checks     -> code
  agent3                   -> LLM (Validation Agent — semantic checks only)
  merge                    -> code, produces final ValidationResult
  not_ready_end / blocked_end -> terminal short-circuits
"""

import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, TypedDict

from .constants import CATEGORY_LABELS, FIXED_STUDY_DESIGN, OUTCOME_CATEGORY_KEYS
from .llm import call_structured
from .prompts import (
    AGENT2_SYSTEM_PROMPT as DEFAULT_AGENT2_SYSTEM_PROMPT,
    AGENT3_SYSTEM_PROMPT as DEFAULT_AGENT3_SYSTEM_PROMPT,
)
from .schemas import (
    Agent1Output,
    Agent2Output,
    SemanticChecks,
    ValidationChecks,
    ValidationResult,
)


class PipelineState(TypedDict, total=False):
    pico_set_id: str
    input_pico: Dict[str, Any]
    handoff_status: str
    blocked_reason: Optional[str]
    agent1_output: Dict[str, Any]
    agent1_fidelity_ok: bool
    agent1_fidelity_errors: List[str]
    outcome_categories_present: List[str]
    agent2_output: Dict[str, Any]
    deterministic_checks: Dict[str, bool]
    semantic_checks: Dict[str, Any]
    final_result: Dict[str, Any]
    # Optional per-run prompt overrides — if absent, each node falls back to the
    # bundled DEFAULT_* prompt from prompts.py. Lets a caller (e.g. a Streamlit
    # editor UI) supply edited prompt text without touching the module constants.
    # (Agent 1 has no prompt: it is pure deterministic code, see agent1_node.)
    agent2_prompt: Optional[str]
    agent3_prompt: Optional[str]
    # Per-call latency/token metrics, one dict per LLM call, appended by each
    # LLM node — summarize with metrics.summarize(state["metrics"]).
    metrics: List[Dict[str, Any]]


# --------------------------------------------------------------------------
# Node 1: Pre-check (code, no LLM call)
# --------------------------------------------------------------------------


def precheck_node(state: PipelineState) -> PipelineState:
    pico = state["input_pico"]
    required = ["population", "intervention", "comparator", "outcomes"]
    missing = [f for f in required if not pico.get(f)]
    status_ok = pico.get("metadata", {}).get("phase2_validation_status") == "PASS"

    if missing or not status_ok:
        reason = (
            f"Missing required field(s): {missing}"
            if missing
            else "Incoming PICO set does not carry phase2_validation_status == 'PASS'"
        )
        return {**state, "handoff_status": "not_ready", "blocked_reason": reason}

    return {**state, "handoff_status": "precheck_passed"}


def route_after_precheck(state: PipelineState) -> str:
    return "agent1" if state["handoff_status"] == "precheck_passed" else "not_ready_end"


# --------------------------------------------------------------------------
# Node 2: Agent 1 — pure code, no LLM call
# --------------------------------------------------------------------------
#
# Agent 1's own role definition (see the retired AGENT1_SYSTEM_PROMPT in
# prompts.py, kept for reference) is explicitly mechanical: copy Population /
# Intervention / Comparator / Outcomes through completely unchanged and append
# the fixed Study Design list — "a business rule, not a judgment call." By the
# time this node runs, precheck_node has already confirmed the required fields
# are present and phase2_validation_status == "PASS", so handoff_status is
# always "ready" here. There is no reading comprehension or generation task to
# delegate to an LLM, so this is computed directly — Agent1Output still
# validates/normalizes the shape exactly as the LLM's structured output did.


def agent1_node(state: PipelineState) -> PipelineState:
    started_at = datetime.now(timezone.utc).isoformat()
    start = time.perf_counter()

    src = state["input_pico"]
    output = Agent1Output(
        handoff_status="ready",
        population=src["population"],
        intervention=src["intervention"],
        comparator=src["comparator"],
        outcomes=src.get("outcomes", {}),
        study_design=list(FIXED_STUDY_DESIGN),
    ).model_dump()

    latency_ms = (time.perf_counter() - start) * 1000
    # Same metric shape llm.call_structured produces, so this agent still shows
    # up in metrics_summary/aggregate_metrics — with 0 tokens and its own real
    # (sub-millisecond) latency, since it made no LLM call rather than an
    # unusually fast one.
    call_metric = {
        "agent": "agent1_lr_handoff_assembly",
        "model_id": "deterministic-code (no LLM call)",
        "latency_ms": round(latency_ms, 3),
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "cost_usd": 0.0,
        "started_at": started_at,
    }

    return {
        **state,
        "agent1_output": output,
        "agent1_fidelity_ok": True,
        "agent1_fidelity_errors": [],
        "metrics": state.get("metrics", []) + [call_metric],
    }


def route_after_agent1(state: PipelineState) -> str:
    return "compute_categories" if state["agent1_fidelity_ok"] else "blocked_end"


# --------------------------------------------------------------------------
# Node 3: compute outcome categories actually present (code)
# --------------------------------------------------------------------------


def compute_categories_node(state: PipelineState) -> PipelineState:
    outcomes = state["agent1_output"]["outcomes"]
    present = [c for c in OUTCOME_CATEGORY_KEYS if len(outcomes.get(c, [])) > 0]
    return {**state, "outcome_categories_present": present}


# --------------------------------------------------------------------------
# Node 4: Agent 2 (LLM)
# --------------------------------------------------------------------------


def agent2_node(state: PipelineState) -> PipelineState:
    system_prompt = state.get("agent2_prompt") or DEFAULT_AGENT2_SYSTEM_PROMPT
    payload = {
        **state["agent1_output"],
        "outcome_categories_present": [
            CATEGORY_LABELS[c] for c in state["outcome_categories_present"]
        ],
    }
    result, call_metric = call_structured(
        system_prompt, payload, Agent2Output, agent_name="agent2_title_objectives"
    )
    return {
        **state,
        "agent2_output": result.model_dump(),
        "metrics": state.get("metrics", []) + [call_metric],
    }


# --------------------------------------------------------------------------
# Node 5: deterministic checks (code — no LLM needed for these)
# --------------------------------------------------------------------------


def deterministic_checks_node(state: PipelineState) -> PipelineState:
    a1 = state["agent1_output"]
    a2 = state["agent2_output"]

    present_labels = {CATEGORY_LABELS[c] for c in state["outcome_categories_present"]}
    secondary_labels = {s["category"] for s in a2["objectives"]["secondary"]}

    checks = {
        "core_fields_unaltered": not state["agent1_fidelity_errors"],
        "outcome_categories_preserved": True,  # enforced verbatim already in agent1 fidelity check
        "study_design_complete": a1["study_design"] == FIXED_STUDY_DESIGN,
        "objectives_structure_correct": (
            secondary_labels == present_labels and bool(a2["objectives"]["primary"].strip())
        ),
    }
    return {**state, "deterministic_checks": checks}


# --------------------------------------------------------------------------
# Node 6: Agent 3 — semantic checks only (LLM)
# --------------------------------------------------------------------------


def agent3_node(state: PipelineState) -> PipelineState:
    system_prompt = state.get("agent3_prompt") or DEFAULT_AGENT3_SYSTEM_PROMPT
    payload = {
        "population": state["agent1_output"]["population"],
        "intervention": state["agent1_output"]["intervention"],
        "comparator": state["agent1_output"]["comparator"],
        "study_title": state["agent2_output"]["study_title"],
        "objectives": state["agent2_output"]["objectives"],
    }
    result, call_metric = call_structured(
        system_prompt, payload, SemanticChecks, agent_name="agent3_validation"
    )
    return {
        **state,
        "semantic_checks": result.model_dump(),
        "metrics": state.get("metrics", []) + [call_metric],
    }


# --------------------------------------------------------------------------
# Node 7: merge deterministic + semantic checks into final verdict (code)
# --------------------------------------------------------------------------


def _explain_deterministic_failure(check_name: str, state: "PipelineState") -> str:
    """Build a specific, human-readable sentence for a failed deterministic check,
    grounded in the actual data that made it fail — never just the check's name."""
    a1 = state["agent1_output"]
    a2 = state["agent2_output"]

    if check_name == "core_fields_unaltered":
        errors = state["agent1_fidelity_errors"]
        return "Agent 1 altered protected field(s) during handoff assembly: " + "; ".join(errors) + "."

    if check_name == "outcome_categories_preserved":
        return "Agent 1's outcome category groupings do not match the input PICO set's groupings."

    if check_name == "study_design_complete":
        return (
            "The assembled Study Design list does not exactly match the required "
            f"fixed five-item list, in order. Got: {a1['study_design']}."
        )

    if check_name == "objectives_structure_correct":
        present = {CATEGORY_LABELS[c] for c in state["outcome_categories_present"]}
        secondary = {s["category"] for s in a2["objectives"]["secondary"]}
        missing = sorted(present - secondary)
        extra = sorted(secondary - present)
        details = []
        if not a2["objectives"]["primary"].strip():
            details.append("the primary objective is empty")
        if missing:
            details.append(f"missing a secondary objective for: {', '.join(missing)}")
        if extra:
            details.append(f"has an unexpected secondary objective for a category with no outcomes: {', '.join(extra)}")
        return "Objectives structure is incorrect — " + "; ".join(details) + "."

    return f"Deterministic check '{check_name}' failed."


def merge_node(state: PipelineState) -> PipelineState:
    det = state["deterministic_checks"]
    sem = state["semantic_checks"]

    checks = ValidationChecks(
        core_fields_unaltered=det["core_fields_unaltered"],
        comparator_phrasing_preserved=sem["comparator_phrasing_preserved"],
        outcome_categories_preserved=det["outcome_categories_preserved"],
        study_design_complete=det["study_design_complete"],
        title_specific=sem["title_specific"],
        objectives_structure_correct=det["objectives_structure_correct"],
        title_objectives_consistent=sem["title_objectives_consistent"],
    )

    all_pass = all(checks.model_dump().values())
    failure_reason = None
    if not all_pass:
        reasons: List[str] = []
        if sem.get("failure_reason"):
            reasons.append(sem["failure_reason"])
        for check_name, det_value in det.items():
            if not det_value:
                reasons.append(_explain_deterministic_failure(check_name, state))
        failure_reason = " ".join(reasons)

    result = ValidationResult(
        pico_set_id=state["pico_set_id"],
        validation_result="passed" if all_pass else "failed_blocked",
        checks=checks,
        failure_reason=failure_reason,
    )
    return {**state, "final_result": result.model_dump()}


# --------------------------------------------------------------------------
# Terminal short-circuit nodes
# --------------------------------------------------------------------------


def not_ready_end_node(state: PipelineState) -> PipelineState:
    # Precheck fails before Agent 1 ever runs, so none of the 7 checks have a
    # real answer yet — the object is still present, every field explicitly
    # null, rather than collapsing the whole "checks" key to null.
    return {
        **state,
        "final_result": ValidationResult(
            pico_set_id=state["pico_set_id"],
            validation_result="failed_blocked",
            checks=ValidationChecks(),
            failure_reason=state.get("blocked_reason", "PICO set not ready for handoff"),
        ).model_dump(),
    }


def blocked_end_node(state: PipelineState) -> PipelineState:
    errors = state["agent1_fidelity_errors"]
    a1 = state["agent1_output"]

    # core_fields_unaltered is the one check this stage can actually answer —
    # Agent 2/Agent 3 never ran, so the remaining 6 checks stay null rather
    # than being guessed at or defaulted to a value.
    checks = ValidationChecks(core_fields_unaltered=not errors)

    if errors:
        failure_reason = (
            "Agent 1 fidelity check failed — it altered protected field(s) during "
            "handoff assembly: " + "; ".join(errors) + "."
        )
    else:
        failure_reason = (
            "Agent 1 fidelity check failed — it returned "
            f"handoff_status='{a1.get('handoff_status')}' (not 'ready') with no altered "
            "fields detected, meaning it judged the incoming PICO set itself unfit for "
            "handoff (e.g. missing an expected field or not carrying validated status)."
        )

    return {
        **state,
        "final_result": ValidationResult(
            pico_set_id=state["pico_set_id"],
            validation_result="failed_blocked",
            checks=checks,
            failure_reason=failure_reason,
        ).model_dump(),
    }
