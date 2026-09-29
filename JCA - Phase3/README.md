# JCA Phase 3 — LR Handoff Pipeline

LangGraph implementation of the Phase 3 LR handoff flow: takes a validated Phase 2
PICO set and, through three agents, produces a Study Design list, a Study Title,
and Study Objectives — gated by a Validation Agent before publication.

## Structure

```
jca_phase3_pipeline/
├── main.py                    # CLI entry point
├── streamlit_app.py            # Streamlit UI — run pipeline + edit prompts live
├── requirements.txt
├── .env.example                # copy to .env and fill in
└── src/jca_pipeline/
    ├── __init__.py              # exposes run_phase3 / run_phase3_batch
    ├── config.py                # env loading, model id
    ├── constants.py              # fixed Study Design list, outcome category keys
    ├── prompts.py                 # full agent system prompts, as Python string constants
    ├── schemas.py                  # pydantic models for structured LLM output
    ├── llm.py                       # Bedrock ChatBedrockConverse client + call_structured()
    ├── metrics.py                    # per-agent / overall latency & token-usage aggregation
    ├── nodes.py                      # PipelineState + all graph node functions
    ├── graph.py                       # builds/compiles the LangGraph StateGraph
    ├── runner.py                       # run_phase3(), run_phase3_batch(), run_phase3_detailed()
    └── xlsx_loader.py                   # parses a Phase 2 .xlsx export into PICO set dicts
```

Each LLM node (`agent1_node`, `agent2_node`, `agent3_node`) reads its system prompt
from `state["agentN_prompt"]` if present, falling back to the bundled `DEFAULT_*`
constant from `prompts.py` otherwise — this is what lets the Streamlit UI (or any
caller) supply edited prompt text per-run without touching the module constants.

Agent prompts live in `prompts.py` as plain Python string constants
(`AGENT1_SYSTEM_PROMPT`, `AGENT2_SYSTEM_PROMPT`, `AGENT3_SYSTEM_PROMPT`) rather than
external `.md` files — no filesystem read at runtime, and the whole codebase ships
as pure `.py`.

`xlsx_loader.py` reads a Phase 2 `.xlsx` export (one PICO set per row) into the
pipeline's expected input dict shape — see "Phase 2 .xlsx input format" below.

`metrics.py` aggregates the latency/token-usage dict each LLM call produces
(`llm.call_structured`) into per-agent and overall totals — see "Token usage &
latency tracking" below.

## Setup

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then fill in your AWS credentials + region + Bedrock model id
```

Confirm `BEDROCK_MODEL_ID` against your AWS Bedrock console's Model catalog for your
account/region before running — the exact model id (or inference-profile ARN) can
differ by region.

## Phase 2 .xlsx input format

`load_pico_sets_from_xlsx()` reads the **Phase 2 module's own .xlsx export
directly** — no reformatting needed. It looks for sheets by keyword (so a
"5. Agent3 PICO Sets" or "PICO Sets" naming both work) and uses:

| Sheet (matched by keyword) | Columns used | Required? |
|---|---|---|
| `... PICO Sets` | `Population`, `PICO Set ID`, `Comparator (C)`, `Intervention (I)`, `Outcomes (O)`, `Member States` | Yes |
| `... Validation` | `Population`, `Set ID`, `Validation Result`, `Failure Reason` | Yes |
| `... Evidence Sources` | `Population`, `PICO Set ID`, `Source Reference` | Optional — populates `metadata.source_articles` |

- `Member States` and `Outcomes (O)` are semicolon-separated within a single
  cell (e.g. `Denmark; Italy`).
- `Validation Result` of `passed` (any case) becomes
  `phase2_validation_status: "PASS"`; anything else passes through as-is (so
  the pipeline's own precheck correctly blocks it). A trailing workbook-level
  summary row like `MEMBER STATE COUNT CHECK` (blank `Population`) is ignored.

**Outcome categorization gap:** Phase 2's `Outcomes (O)` is one flat
semicolon-separated list — it isn't pre-split into the four categories
(Clinical Effectiveness / Safety / Quality of Life / Clinician-Patient-Reported)
that Agent 2 and Agent 3's prompts expect. The loader assigns each outcome to a
category with a keyword heuristic (`OUTCOME_CATEGORY_KEYWORDS` in
`xlsx_loader.py`) — anything unmatched falls back to Clinical Effectiveness.
This is best-effort: check the categorization in the "Agent 1 output" expander
after a run, and extend the keyword lists if your data consistently
miscategorizes a recurring term.

Run `python main.py --template` to generate `phase2_template.xlsx` with one
filled example PICO set across all three sheets, or use the "Download template
.xlsx" button in the Streamlit UI.

## Token usage & latency tracking

Every LLM call (Agent 1, Agent 2, Agent 3) is timed and its token usage extracted
in `llm.call_structured()`, which returns `(parsed_result, metric)` where `metric`
is:

```json
{
  "agent": "agent1_lr_handoff_assembly",
  "model_id": "anthropic.claude-sonnet-5-...",
  "latency_ms": 842.3,
  "input_tokens": 1204,
  "output_tokens": 187,
  "total_tokens": 1391,
  "started_at": "2026-09-17T10:02:11.123456+00:00"
}
```

Each LLM node (`agent1_node`, `agent2_node`, `agent3_node`) appends its call's
metric to `state["metrics"]`. `run_phase3_detailed()` rolls that list up via
`metrics.summarize()` into a `metrics_summary`:

```json
{
  "per_agent": {
    "agent1_lr_handoff_assembly": {"calls": 1, "latency_ms": 842.3, "input_tokens": 1204, "output_tokens": 187, "total_tokens": 1391},
    "agent2_title_objectives":    {"calls": 1, "latency_ms": 1050.1, "...": "..."},
    "agent3_validation":          {"calls": 1, "latency_ms": 610.7, "...": "..."}
  },
  "overall": {"calls": 3, "latency_ms": 2503.1, "input_tokens": 3600, "output_tokens": 520, "total_tokens": 4120},
  "total_pipeline_latency_ms": 2610.4
}
```

`total_pipeline_latency_ms` is measured around the whole graph invocation, so it
also captures the (usually negligible) time spent in the code-only nodes between
LLM calls.

`run_phase3_batch()` additionally returns `aggregate_metrics` — the same shape,
rolled up across every PICO set in the batch — alongside each individual result's
own `metrics_summary`.

- **CLI** (`main.py`) prints `metrics_summary` (single run) or `aggregate_metrics`
  (batch/.xlsx run) after the results JSON.
- **Streamlit UI** shows a per-agent table (calls, latency, input/output/total
  tokens) under each single-run result, and both a per-set latency/token column
  and an aggregate per-agent table for batch runs.

## Run

### CLI

```bash
python main.py                          # runs the built-in example PICO set
python main.py phase2_pico_output.xlsx   # runs every PICO set in the given Phase 2 .xlsx export as a batch
python main.py --template                # writes phase2_template.xlsx to the current directory
```

### Streamlit UI

```bash
streamlit run streamlit_app.py
```

Opens a browser UI with two tabs:

- **▶ Run Pipeline** — choose "Upload .xlsx (Phase 2 output)" to upload a file (a
  preview table of every parsed row appears; select one row to run in detail, or
  run every row as a batch with a pass/blocked summary table), or "Paste JSON
  (single set)" for quick manual testing. Results show Agent 1's assembled output,
  Agent 2's Study Title & Objectives, the 7-item validation checklist, and the
  final passed/blocked verdict — plus the full raw pipeline state for debugging.
- **✎ Edit Agent Prompts** — each agent's full system prompt is shown in an editable
  text box, pre-filled from `prompts.py`. Edits apply only to your current browser
  session and are sent with each run via `run_phase3_detailed(..., prompt_overrides=...)`
  — they never modify `prompts.py` on disk. Use "Reset ... to default" to revert.

Or from your own code:

```python
from jca_pipeline import run_phase3, run_phase3_batch

result = run_phase3(my_pico_set_dict)
results = run_phase3_batch([pico_set_1, pico_set_2, ...])
```

## Pipeline flow

```
precheck (code) --not_ready--> not_ready_end
    |
    v (precheck_passed)
agent1 (LLM) --> code fidelity diff-check
    |
    v (fidelity_ok)                --fail--> blocked_end
compute_categories (code)
    |
    v
agent2 (LLM: title + objectives)
    |
    v
deterministic_checks (code: field fidelity, study design, objective counts)
    |
    v
agent3 (LLM: comparator phrasing / title specificity / title-objectives consistency only)
    |
    v
merge (code) --> final ValidationResult (passed | failed_blocked)
```

Only the three checks that require real reading comprehension go to the LLM in
Agent 3 — field-for-field fidelity, study design completeness, and objective-count
matching are computed deterministically in code and merged into the final 7-check
result in `nodes.merge_node`.

## Editing a prompt

Edit the relevant constant directly in `src/jca_pipeline/prompts.py`
(`AGENT1_SYSTEM_PROMPT`, `AGENT2_SYSTEM_PROMPT`, or `AGENT3_SYSTEM_PROMPT`) — each is
a full Python triple-quoted string containing that agent's Role, Purpose, Generation
Process, Worked Examples, Self-Validation Checklist, Edge Cases, and JSON Schema.

## Batch behaviour

`run_phase3_batch` runs each PICO set through its own independent graph invocation,
so one set's failure never blocks or delays another's — matching the "block only
the specific failing project" rule in the Validation Agent's own prompt.

## Troubleshooting

**`ValidationException: ... on-demand throughput isn't supported ... Retry your
request with the ID or ARN of an inference profile that contains this model.`**

Claude Sonnet 5 (and most current Claude models) on Bedrock can't be invoked by
a bare on-demand model ID like `anthropic.claude-sonnet-5` — Bedrock requires an
**inference profile ID or ARN** instead. `BEDROCK_MODEL_ID` in `.env` defaults to
`us.anthropic.claude-sonnet-5`, which is the value this project's own Phase 2
module used successfully (visible in its Run Summary sheet's `model` field). If
Bedrock still rejects that exact string for your account/region, open the AWS
Bedrock console → **Cross-region inference profiles**, copy the profile ID or
ARN listed there for Claude Sonnet 5, and set `BEDROCK_MODEL_ID` to that value.

**`UserWarning: Model ... does not support temperature; ignoring the provided
value.`** — harmless; some Bedrock model/profile combinations ignore the
`temperature=0` the client sets. No action needed.
