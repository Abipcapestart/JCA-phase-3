"""Writes Phase 3 run output to a JSON file (full detail, for debugging) and
an .xlsx file (flat, for review in Excel) so a CLI run leaves files behind
instead of only printing to the console. Also exposes in-memory (bytes)
builders so any caller — the CLI or the Streamlit UI's download buttons —
produces byte-for-byte the same JSON/xlsx shape from the same result data."""

import io
import json
from pathlib import Path
from typing import Any, Dict, List, Tuple, Union

import pandas as pd

CHECK_COLUMNS = [
    "core_fields_unaltered",
    "comparator_phrasing_preserved",
    "outcome_categories_preserved",
    "study_design_complete",
    "title_specific",
    "objectives_structure_correct",
    "title_objectives_consistent",
]


def build_json_bytes(payload: Union[Dict[str, Any], List[Dict[str, Any]]]) -> bytes:
    return json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")


def write_json_output(payload: Union[Dict[str, Any], List[Dict[str, Any]]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(build_json_bytes(payload))


def _build_result_dataframes(results: List[Dict[str, Any]]) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """results: a list of final_result dicts, each carrying its own
    "metrics_summary" (as returned by run_phase3_batch()["results"], or a
    single-set [result] list built the same way)."""
    result_rows = []
    per_agent_rows = []
    for r in results:
        checks = r.get("checks") or {}
        summary = r.get("metrics_summary") or {}
        overall = summary.get("overall") or {}

        result_rows.append(
            {
                "pico_set_id": r.get("pico_set_id"),
                "validation_result": r.get("validation_result"),
                "failure_reason": r.get("failure_reason") or "",
                **{c: checks.get(c) for c in CHECK_COLUMNS},
                "total_pipeline_latency_ms": summary.get("total_pipeline_latency_ms"),
                "llm_calls": overall.get("calls"),
                "input_tokens": overall.get("input_tokens"),
                "output_tokens": overall.get("output_tokens"),
                "total_tokens": overall.get("total_tokens"),
                "total_cost_usd": overall.get("cost_usd"),
            }
        )

        for agent, bucket in (summary.get("per_agent") or {}).items():
            per_agent_rows.append({"pico_set_id": r.get("pico_set_id"), "agent": agent, **bucket})

    return pd.DataFrame(result_rows), pd.DataFrame(per_agent_rows)


def _build_agent2_dataframe(results: List[Dict[str, Any]]) -> pd.DataFrame:
    """One row per PICO set with Agent 2's raw output (Study Title & Objectives
    Generation Agent) — the study title and objectives it generated, independent
    of whether the deterministic/semantic checks over that output passed."""
    rows = []
    for r in results:
        a2 = r.get("agent2_output")
        if not a2:
            continue
        objectives = a2.get("objectives") or {}
        secondary = objectives.get("secondary") or []
        secondary_text = "\n".join(
            f"{s.get('category')}: {s.get('objective')}" for s in secondary
        )
        rows.append(
            {
                "pico_set_id": r.get("pico_set_id"),
                "study_title": a2.get("study_title"),
                "primary_objective": objectives.get("primary"),
                "secondary_objectives": secondary_text,
            }
        )
    return pd.DataFrame(rows)


def _build_agent3_dataframe(results: List[Dict[str, Any]]) -> pd.DataFrame:
    """One row per PICO set with Agent 3's raw semantic-check output (Validation
    Agent) — its own booleans and failure_reason, before merging with the
    deterministic checks into the Results sheet's combined verdict."""
    rows = []
    for r in results:
        sem = r.get("semantic_checks")
        if not sem:
            continue
        failure_reason = sem.get("failure_reason")
        rows.append(
            {
                "pico_set_id": r.get("pico_set_id"),
                "comparator_phrasing_preserved": sem.get("comparator_phrasing_preserved"),
                "title_specific": sem.get("title_specific"),
                "title_objectives_consistent": sem.get("title_objectives_consistent"),
                # The LLM sometimes emits the literal string "null" instead of a
                # real null when there's no failure — treat both as "no reason".
                "agent3_failure_reason": "" if not failure_reason or failure_reason.strip().lower() == "null" else failure_reason,
            }
        )
    return pd.DataFrame(rows)


def build_xlsx_bytes(results: List[Dict[str, Any]]) -> bytes:
    results_df, per_agent_df = _build_result_dataframes(results)
    agent2_df = _build_agent2_dataframe(results)
    agent3_df = _build_agent3_dataframe(results)

    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        results_df.to_excel(writer, index=False, sheet_name="Results")
        if not agent2_df.empty:
            agent2_df.to_excel(writer, index=False, sheet_name="Agent 2 - Title & Objectives")
        if not agent3_df.empty:
            agent3_df.to_excel(writer, index=False, sheet_name="Agent 3 - Validation")
        if not per_agent_df.empty:
            per_agent_df.to_excel(writer, index=False, sheet_name="Per-Agent Metrics")
    return buffer.getvalue()


def write_xlsx_output(results: List[Dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(build_xlsx_bytes(results))
