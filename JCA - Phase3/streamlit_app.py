"""
Streamlit UI for the JCA Phase 3 LR Handoff Pipeline.

Run with:
    streamlit run streamlit_app.py

Lets you:
  - Upload a Phase 2 .xlsx output file (one PICO set per row) — or paste a single
    PICO set as JSON for quick testing
  - View and edit each agent's system prompt for this session
  - Run one PICO set in full detail, or run every row in an uploaded file as a batch
  - Reset any edited prompt back to the bundled default
"""

import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from jca_pipeline.prompts import (  # noqa: E402
    AGENT1_SYSTEM_PROMPT as RETIRED_AGENT1_PROMPT,
    AGENT2_SYSTEM_PROMPT as DEFAULT_AGENT2_PROMPT,
    AGENT3_SYSTEM_PROMPT as DEFAULT_AGENT3_PROMPT,
)
from jca_pipeline.metrics import summarize as summarize_metrics  # noqa: E402
from jca_pipeline.output_writer import build_json_bytes, build_xlsx_bytes  # noqa: E402
from jca_pipeline.runner import run_phase3_detailed  # noqa: E402
from jca_pipeline.xlsx_loader import generate_template_bytes, load_pico_sets_from_xlsx  # noqa: E402

st.set_page_config(page_title="JCA Phase 3 — LR Handoff Pipeline", layout="wide")

EXAMPLE_PICO = {
    "pico_set_id": "osimertinib-gefitinib-nsclc-denmark-italy",
    "population": "Locally advanced or metastatic NSCLC, EGFR exon 19 deletion",
    "intervention": "Osimertinib",
    "comparator": {
        "display_value": "Gefitinib",
        "member_states": ["Denmark", "Italy"],
    },
    "outcomes": {
        "clinical_effectiveness": [
            "Overall survival",
            "Progression-free survival",
            "Objective response rate",
        ],
        "safety": ["Grade \u2265 3 adverse events"],
        "quality_of_life": [],
        "clinician_patient_reported": [],
    },
    "metadata": {
        "source_articles": ["PMID:12345678"],
        "phase2_validation_status": "PASS",
    },
}

# --- session state defaults ---
defaults = {
    "agent2_prompt": DEFAULT_AGENT2_PROMPT,
    "agent3_prompt": DEFAULT_AGENT3_PROMPT,
    "pico_json_text": json.dumps(EXAMPLE_PICO, indent=2, ensure_ascii=False),
    "parsed_pico_sets": None,
    "xlsx_parse_error": None,
    "last_result": None,
    "last_error": None,
    "batch_results": None,
    "batch_aggregate_metrics": None,
}
for key, value in defaults.items():
    if key not in st.session_state:
        st.session_state[key] = value

st.title("JCA Phase 3 — LR Handoff Pipeline")
st.caption(
    "LR Handoff Assembly → Study Title & Objectives Generation → Validation, "
    "run through LangGraph with Claude on Bedrock."
)

tab_run, tab_prompts = st.tabs(["▶ Run Pipeline", "✎ Edit Agent Prompts"])

# ============================================================================
# Tab: Edit Agent Prompts
# ============================================================================
with tab_prompts:
    st.info(
        "Edits here apply only to this browser session and are sent with each run — "
        "they do not modify the `prompts.py` file on disk."
    )

    p1_col, p2_col, p3_col = st.tabs(
        ["Agent 1 — LR Handoff Assembly", "Agent 2 — Title & Objectives", "Agent 3 — Validation"]
    )

    with p1_col:
        st.info(
            "Agent 1 no longer makes an LLM call. Its job — copy Population / "
            "Intervention / Comparator / Outcomes through unchanged and append the "
            "fixed Study Design list — is a mechanical business rule with no "
            "judgment involved, so it now runs as deterministic code instead of an "
            "LLM prompt. There is nothing to edit here; the retired prompt is shown "
            "below for reference only."
        )
        st.text_area(
            "Retired Agent 1 system prompt (read-only)",
            value=RETIRED_AGENT1_PROMPT,
            height=500,
            key="agent1_prompt_area",
            disabled=True,
        )

    with p2_col:
        st.session_state.agent2_prompt = st.text_area(
            "Agent 2 system prompt",
            value=st.session_state.agent2_prompt,
            height=500,
            key="agent2_prompt_area",
        )
        if st.button("Reset Agent 2 prompt to default", key="reset_a2"):
            st.session_state.agent2_prompt = DEFAULT_AGENT2_PROMPT
            st.rerun()

    with p3_col:
        st.session_state.agent3_prompt = st.text_area(
            "Agent 3 system prompt (semantic checks only — see note at top of prompt)",
            value=st.session_state.agent3_prompt,
            height=500,
            key="agent3_prompt_area",
        )
        if st.button("Reset Agent 3 prompt to default", key="reset_a3"):
            st.session_state.agent3_prompt = DEFAULT_AGENT3_PROMPT
            st.rerun()

# ============================================================================
# Tab: Run Pipeline
# ============================================================================
with tab_run:
    left, right = st.columns([1, 1])

    with left:
        st.subheader("Input: Phase 2 output")

        input_mode = st.radio(
            "Input method",
            ["Upload .xlsx (Phase 2 output)", "Paste JSON (single set)"],
            horizontal=True,
        )

        prompt_overrides = {
            "agent2_prompt": st.session_state.agent2_prompt,
            "agent3_prompt": st.session_state.agent3_prompt,
        }

        run_single_clicked = False
        run_batch_clicked = False
        run_json_clicked = False
        selected_pico = None
        pico_sets = None

        # ------------------------------------------------------------------
        # Excel upload path
        # ------------------------------------------------------------------
        if input_mode == "Upload .xlsx (Phase 2 output)":
            st.download_button(
                "\u2b07 Download template .xlsx",
                data=generate_template_bytes(),
                file_name="phase2_template.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
            st.caption(
                "Upload the Phase 2 module's own .xlsx export as-is — this reads its "
                "'... PICO Sets' sheet (Population, PICO Set ID, Comparator (C), "
                "Intervention (I), Outcomes (O), Member States), joins in pass/fail "
                "status from the '... Validation' sheet, and pulls source references "
                "from '... Evidence Sources' if present. Outcomes are a single flat "
                "list in Phase 2's export; they're auto-sorted into the four "
                "categories the agents expect using a keyword heuristic — worth a "
                "quick glance in the Agent 1 output expander after running."
            )

            uploaded = st.file_uploader("Upload Phase 2 .xlsx", type=["xlsx"])
            if uploaded is not None:
                try:
                    st.session_state.parsed_pico_sets = load_pico_sets_from_xlsx(uploaded)
                    st.session_state.xlsx_parse_error = None
                except Exception as e:  # noqa: BLE001
                    st.session_state.parsed_pico_sets = None
                    st.session_state.xlsx_parse_error = str(e)

            if st.session_state.xlsx_parse_error:
                st.error(st.session_state.xlsx_parse_error)

            pico_sets = st.session_state.parsed_pico_sets

            if pico_sets:
                st.success(f"Parsed {len(pico_sets)} PICO set(s) from the uploaded file.")
                preview_df = pd.DataFrame(
                    [
                        {
                            "pico_set_id": p["pico_set_id"],
                            "population": p["population"][:60],
                            "intervention": p["intervention"],
                            "comparator": p["comparator"]["display_value"],
                            "phase2_validation_status": p["metadata"]["phase2_validation_status"],
                        }
                        for p in pico_sets
                    ]
                )
                st.dataframe(preview_df, use_container_width=True, hide_index=True)

                id_options = [p["pico_set_id"] for p in pico_sets]
                selected_id = st.selectbox("Select one PICO set to run in detail", id_options)
                selected_pico = next(p for p in pico_sets if p["pico_set_id"] == selected_id)

                col_a, col_b = st.columns(2)
                with col_a:
                    run_single_clicked = st.button(
                        "\u25b6 Run selected set", type="primary", use_container_width=True
                    )
                with col_b:
                    run_batch_clicked = st.button(
                        f"\u25b6\u25b6 Run all {len(pico_sets)} sets (batch)",
                        use_container_width=True,
                    )

            if run_single_clicked and selected_pico is not None:
                st.session_state.batch_results = None
                st.session_state.batch_aggregate_metrics = None
                st.session_state.last_error = None
                with st.spinner(f"Running {selected_pico['pico_set_id']} ..."):
                    try:
                        st.session_state.last_result = run_phase3_detailed(
                            selected_pico, prompt_overrides=prompt_overrides
                        )
                    except Exception as e:  # noqa: BLE001
                        st.session_state.last_result = None
                        st.session_state.last_error = f"Pipeline error: {e}"

            if run_batch_clicked and pico_sets:
                st.session_state.last_result = None
                st.session_state.last_error = None
                results = []
                all_call_metrics = []
                total_latency_ms = 0.0
                progress = st.progress(0.0)
                for i, pico in enumerate(pico_sets):
                    try:
                        state = run_phase3_detailed(pico, prompt_overrides=prompt_overrides)
                        result = dict(state["final_result"])
                        result["metrics_summary"] = state["metrics_summary"]
                        result["agent2_output"] = state.get("agent2_output")
                        result["semantic_checks"] = state.get("semantic_checks")
                        results.append(result)
                        all_call_metrics.extend(state.get("metrics", []))
                        total_latency_ms += state["metrics_summary"]["total_pipeline_latency_ms"]
                    except Exception as e:  # noqa: BLE001
                        results.append(
                            {
                                "pico_set_id": pico["pico_set_id"],
                                "validation_result": "failed_blocked",
                                "failure_reason": f"Pipeline error: {e}",
                                "metrics_summary": None,
                            }
                        )
                    progress.progress((i + 1) / len(pico_sets))

                aggregate_metrics = summarize_metrics(all_call_metrics)
                aggregate_metrics["total_pipeline_latency_ms"] = round(total_latency_ms, 1)
                st.session_state.batch_results = results
                st.session_state.batch_aggregate_metrics = aggregate_metrics

        # ------------------------------------------------------------------
        # JSON paste path
        # ------------------------------------------------------------------
        else:
            st.session_state.pico_json_text = st.text_area(
                "PICO set JSON",
                value=st.session_state.pico_json_text,
                height=440,
                key="pico_json_area",
            )

            col_a, col_b = st.columns(2)
            with col_a:
                run_json_clicked = st.button(
                    "\u25b6 Run Phase 3 Pipeline", type="primary", use_container_width=True
                )
            with col_b:
                if st.button("Load example PICO set", use_container_width=True):
                    st.session_state.pico_json_text = json.dumps(
                        EXAMPLE_PICO, indent=2, ensure_ascii=False
                    )
                    st.rerun()

            if run_json_clicked:
                st.session_state.last_error = None
                st.session_state.last_result = None
                st.session_state.batch_results = None
                st.session_state.batch_aggregate_metrics = None
                try:
                    pico_set = json.loads(st.session_state.pico_json_text)
                except json.JSONDecodeError as e:
                    st.session_state.last_error = f"Invalid JSON: {e}"
                else:
                    with st.spinner("Running Agent 1 -> Agent 2 -> Agent 3 ..."):
                        try:
                            st.session_state.last_result = run_phase3_detailed(
                                pico_set, prompt_overrides=prompt_overrides
                            )
                        except Exception as e:  # noqa: BLE001
                            st.session_state.last_error = f"Pipeline error: {e}"

    with right:
        st.subheader("Results")

        if st.session_state.last_error:
            st.error(st.session_state.last_error)

        # --- Batch results table ---
        if st.session_state.batch_results:
            results = st.session_state.batch_results
            passed_count = sum(1 for r in results if r.get("validation_result") == "passed")
            st.markdown(f"**{passed_count} / {len(results)} passed**")
            summary_df = pd.DataFrame(
                [
                    {
                        "pico_set_id": r.get("pico_set_id"),
                        "result": "\u2705 passed"
                        if r.get("validation_result") == "passed"
                        else "\u26d4 blocked",
                        "latency_ms": (r.get("metrics_summary") or {}).get(
                            "total_pipeline_latency_ms"
                        ),
                        "total_tokens": ((r.get("metrics_summary") or {}).get("overall") or {}).get(
                            "total_tokens"
                        ),
                        "cost_usd": ((r.get("metrics_summary") or {}).get("overall") or {}).get(
                            "cost_usd"
                        ),
                        "failure_reason": r.get("failure_reason") or "",
                    }
                    for r in results
                ]
            )
            st.dataframe(summary_df, use_container_width=True, hide_index=True)

            agg = st.session_state.batch_aggregate_metrics
            if agg:
                st.markdown("**Aggregate token usage, cost & latency (whole batch)**")
                st.caption(
                    f"Total pipeline latency across all sets: "
                    f"{agg['total_pipeline_latency_ms']:,.0f} ms "
                    f"({agg['overall']['calls']} LLM call(s), "
                    f"{agg['overall']['total_tokens']:,} total tokens, "
                    f"${agg['overall']['cost_usd']:,.4f} estimated cost)"
                )
                agg_df = pd.DataFrame(
                    [
                        {
                            "agent": agent,
                            "calls": bucket["calls"],
                            "latency_ms": bucket["latency_ms"],
                            "input_tokens": bucket["input_tokens"],
                            "output_tokens": bucket["output_tokens"],
                            "total_tokens": bucket["total_tokens"],
                            "cost_usd": bucket["cost_usd"],
                        }
                        for agent, bucket in agg["per_agent"].items()
                    ]
                )
                st.dataframe(agg_df, use_container_width=True, hide_index=True)

            batch_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            dl_col_a, dl_col_b = st.columns(2)
            with dl_col_a:
                st.download_button(
                    "⬇ Download final response (.json)",
                    data=build_json_bytes({"results": results, "aggregate_metrics": agg}),
                    file_name=f"phase3_results_{batch_timestamp}.json",
                    mime="application/json",
                    use_container_width=True,
                    key="download_batch_json",
                )
            with dl_col_b:
                st.download_button(
                    "⬇ Download final response (.xlsx)",
                    data=build_xlsx_bytes(results),
                    file_name=f"phase3_results_{batch_timestamp}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True,
                    key="download_batch_xlsx",
                )

            with st.expander("Full batch results (raw JSON)"):
                st.json(results)

        # --- Single detailed result ---
        result_state = st.session_state.last_result
        if result_state:
            final = result_state.get("final_result", {})
            verdict = final.get("validation_result")

            if verdict == "passed":
                st.success(f"\u2705 PASSED \u2014 {final.get('pico_set_id')}")
            else:
                st.error(f"\u26d4 BLOCKED \u2014 {final.get('pico_set_id')}")
                if final.get("failure_reason"):
                    st.warning(final["failure_reason"])

            if final.get("checks"):
                st.markdown("**Validation checks**")
                for check_name, passed in final["checks"].items():
                    icon = "\u2705" if passed else "\u274c"
                    st.markdown(f"{icon} `{check_name}`")

            with st.expander("Agent 1 output \u2014 assembled PICO + Study Design", expanded=False):
                if result_state.get("agent1_output"):
                    st.json(result_state["agent1_output"])
                if result_state.get("agent1_fidelity_errors"):
                    st.warning(f"Fidelity errors: {result_state['agent1_fidelity_errors']}")

            with st.expander("Agent 2 output \u2014 Study Title & Objectives", expanded=True):
                if result_state.get("agent2_output"):
                    a2 = result_state["agent2_output"]
                    st.markdown(f"**Study Title:** {a2.get('study_title', '')}")
                    st.markdown(f"**Primary objective:** {a2['objectives'].get('primary', '')}")
                    for sec in a2["objectives"].get("secondary", []):
                        st.markdown(f"- *{sec['category']}*: {sec['objective']}")

            with st.expander("Agent 3 semantic checks (raw)", expanded=False):
                if result_state.get("semantic_checks"):
                    st.json(result_state["semantic_checks"])

            metrics_summary = result_state.get("metrics_summary")
            if metrics_summary:
                st.markdown("**Token usage, cost & latency**")
                st.caption(
                    f"Total pipeline latency: {metrics_summary['total_pipeline_latency_ms']:,.0f} ms "
                    f"({metrics_summary['overall']['calls']} LLM call(s), "
                    f"{metrics_summary['overall']['total_tokens']:,} total tokens, "
                    f"${metrics_summary['overall']['cost_usd']:,.4f} estimated cost)"
                )
                per_agent_df = pd.DataFrame(
                    [
                        {
                            "agent": agent,
                            "calls": bucket["calls"],
                            "latency_ms": bucket["latency_ms"],
                            "input_tokens": bucket["input_tokens"],
                            "output_tokens": bucket["output_tokens"],
                            "total_tokens": bucket["total_tokens"],
                            "cost_usd": bucket["cost_usd"],
                        }
                        for agent, bucket in metrics_summary["per_agent"].items()
                    ]
                )
                st.dataframe(per_agent_df, use_container_width=True, hide_index=True)

            single_result = dict(final)
            single_result["metrics_summary"] = metrics_summary
            single_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            dl_col_a, dl_col_b = st.columns(2)
            with dl_col_a:
                st.download_button(
                    "⬇ Download final response (.json)",
                    data=build_json_bytes({"results": [single_result]}),
                    file_name=f"phase3_result_{single_result.get('pico_set_id', 'unknown')}_{single_timestamp}.json",
                    mime="application/json",
                    use_container_width=True,
                    key="download_single_json",
                )
            with dl_col_b:
                st.download_button(
                    "⬇ Download final response (.xlsx)",
                    data=build_xlsx_bytes([single_result]),
                    file_name=f"phase3_result_{single_result.get('pico_set_id', 'unknown')}_{single_timestamp}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True,
                    key="download_single_xlsx",
                )

            with st.expander("Full pipeline state (debug)", expanded=False):
                st.json(result_state)

        if not st.session_state.last_result and not st.session_state.batch_results:
            st.caption("Run the pipeline to see results here.")
