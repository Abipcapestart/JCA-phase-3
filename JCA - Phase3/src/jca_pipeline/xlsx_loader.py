"""Reads Phase 2 PICO sets directly from the real Phase 2 module's .xlsx export.

The Phase 2 workbook has several sheets (sheet names carry a leading "N. "
prefix — matched here by keyword, not by fixed position, so a re-numbering
doesn't break this):

  "... PICO Sets"        -- one row per PICO set (required). Columns used:
                            Population, PICO Set ID, Comparator (C),
                            Intervention (I), Outcomes (O), Member States
  "... Validation"       -- one row per PICO set (required). Columns used:
                            Population, Set ID, Validation Result,
                            Failure Reason. A trailing summary row such as
                            "MEMBER STATE COUNT CHECK" (blank Population) is
                            ignored — it's a workbook-level check, not a set.
  "... Evidence Sources"  -- optional. Columns used: Population, PICO Set ID,
                            Source Reference. Used to populate
                            metadata.source_articles.

"Member States" and "Outcomes (O)" are semicolon-separated within their cell
(e.g. "Denmark; Italy").

Category gap: Phase 2's "Outcomes (O)" is one flat list — it is not already
split into the four categories (Clinical Effectiveness / Safety / Quality of
Life / Clinician-Patient-Reported) that the Phase 3 agents expect. This loader
assigns each outcome to a category with a keyword heuristic
(OUTCOME_CATEGORY_KEYWORDS below); anything not matched falls back to Clinical
Effectiveness. This is best-effort — review the categorization for anything
ambiguous, and extend the keyword lists if your data consistently
miscategorizes a recurring term.
"""

import io
from typing import Any, Dict, List, Optional, Tuple, Union

import pandas as pd

LIST_DELIMITER = ";"

REQUIRED_PICO_SHEET_COLUMNS = [
    "Population",
    "PICO Set ID",
    "Comparator (C)",
    "Intervention (I)",
    "Outcomes (O)",
    "Member States",
]
REQUIRED_VALIDATION_SHEET_COLUMNS = ["Population", "Set ID", "Validation Result"]

# Checked in this order — safety/QoL/clinician-reported keywords are checked
# before falling back to Clinical Effectiveness, since that's the largest,
# least-specific bucket.
OUTCOME_CATEGORY_KEYWORDS: Dict[str, List[str]] = {
    "safety": [
        "adverse event",
        "adverse effect",
        "safety",
        "tolerability",
        "toxicity",
        "discontinuation",
        "hematology",
        "chemistry parameter",
        "ecg",
        "electrocardiogram",
        "heart rate",
        "qtcf",
        "qtc",
        "pr interval",
        "qrs",
        "concentration",
        "auc",
        "cmin",
        "pharmacokinetic",
        "growth velocity",
        "bone age",
        "growth hormone",
        "gh deficiency",
    ],
    "quality_of_life": [
        "quality of life",
        "qualité de vie",
        "qol",
        "handicap",
        "symptom",
        "visual function",
        "neurological function",
        "fonctionnement",
    ],
    "clinician_patient_reported": [
        "patient-reported",
        "patient reported",
        "clinician-reported",
        "clinician reported",
        "caregiver-reported",
        "pro score",
    ],
}
DEFAULT_OUTCOME_CATEGORY = "clinical_effectiveness"
OUTCOME_CATEGORY_KEYS = [
    "clinical_effectiveness",
    "safety",
    "quality_of_life",
    "clinician_patient_reported",
]


def _cell_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and pd.isna(value):
        return ""
    return str(value).strip()


def _split_list_cell(value: Any) -> List[str]:
    text = _cell_str(value)
    if not text:
        return []
    return [item.strip() for item in text.split(LIST_DELIMITER) if item.strip()]


def _categorize_outcome(outcome_text: str) -> str:
    text = outcome_text.lower()
    for category in ("safety", "quality_of_life", "clinician_patient_reported"):
        for keyword in OUTCOME_CATEGORY_KEYWORDS[category]:
            if keyword in text:
                return category
    return DEFAULT_OUTCOME_CATEGORY


def _find_sheet_name(sheet_names, keyword: str, required: bool = True) -> Optional[str]:
    keyword_lower = keyword.lower()
    for name in sheet_names:
        if keyword_lower in str(name).lower():
            return name
    if required:
        raise ValueError(
            f"Could not find a sheet matching '{keyword}' in the uploaded workbook. "
            f"Sheets found: {list(sheet_names)}"
        )
    return None


def load_pico_sets_from_xlsx(
    file_or_path: Union[str, "io.BytesIO"],
) -> List[Dict[str, Any]]:
    """Parse a Phase 2 .xlsx export (path or file-like/BytesIO, e.g. a Streamlit
    upload) into a list of PICO set dicts matching the pipeline's expected
    input schema. Raises ValueError if a required sheet or column is missing.
    """
    sheets = pd.read_excel(file_or_path, sheet_name=None, dtype=object, engine="openpyxl")

    pico_sheet_name = _find_sheet_name(sheets.keys(), "PICO Sets")
    validation_sheet_name = _find_sheet_name(sheets.keys(), "Validation")
    evidence_sheet_name = _find_sheet_name(sheets.keys(), "Evidence Sources", required=False)

    pico_df = sheets[pico_sheet_name]
    pico_df.columns = [str(c).strip() for c in pico_df.columns]
    missing = [c for c in REQUIRED_PICO_SHEET_COLUMNS if c not in pico_df.columns]
    if missing:
        raise ValueError(f"Missing required column(s) in the '{pico_sheet_name}' sheet: {missing}")

    validation_df = sheets[validation_sheet_name]
    validation_df.columns = [str(c).strip() for c in validation_df.columns]
    missing_v = [c for c in REQUIRED_VALIDATION_SHEET_COLUMNS if c not in validation_df.columns]
    if missing_v:
        raise ValueError(
            f"Missing required column(s) in the '{validation_sheet_name}' sheet: {missing_v}"
        )

    # (population, set_id) -> normalized validation status ("PASS" or the raw
    # upper-cased Validation Result text if it isn't "passed")
    validation_lookup: Dict[Tuple[str, str], str] = {}
    for _, row in validation_df.iterrows():
        set_id = _cell_str(row.get("Set ID"))
        population = _cell_str(row.get("Population"))
        if not set_id or not population:
            continue  # skips workbook-level summary rows like MEMBER STATE COUNT CHECK
        raw_status = _cell_str(row.get("Validation Result")).upper()
        validation_lookup[(population, set_id)] = "PASS" if raw_status == "PASSED" else raw_status

    # (population, pico_set_id) -> ordered, de-duplicated list of source references
    source_lookup: Dict[Tuple[str, str], List[str]] = {}
    if evidence_sheet_name:
        evidence_df = sheets[evidence_sheet_name]
        evidence_df.columns = [str(c).strip() for c in evidence_df.columns]
        if {"Population", "PICO Set ID", "Source Reference"}.issubset(evidence_df.columns):
            for _, row in evidence_df.iterrows():
                key = (_cell_str(row.get("Population")), _cell_str(row.get("PICO Set ID")))
                ref = _cell_str(row.get("Source Reference"))
                if ref:
                    bucket = source_lookup.setdefault(key, [])
                    if ref not in bucket:
                        bucket.append(ref)

    pico_sets: List[Dict[str, Any]] = []
    for idx, row in pico_df.iterrows():
        population = _cell_str(row["Population"])
        pico_set_id = _cell_str(row["PICO Set ID"]) or f"row-{idx + 2}"
        raw_outcomes = _split_list_cell(row["Outcomes (O)"])

        categorized_outcomes: Dict[str, List[str]] = {key: [] for key in OUTCOME_CATEGORY_KEYS}
        for outcome in raw_outcomes:
            categorized_outcomes[_categorize_outcome(outcome)].append(outcome)

        pico_sets.append(
            {
                "pico_set_id": pico_set_id,
                "population": population,
                "intervention": _cell_str(row["Intervention (I)"]),
                "comparator": {
                    "display_value": _cell_str(row["Comparator (C)"]),
                    "member_states": _split_list_cell(row["Member States"]),
                },
                "outcomes": categorized_outcomes,
                "metadata": {
                    "source_articles": source_lookup.get((population, pico_set_id), []),
                    "phase2_validation_status": validation_lookup.get((population, pico_set_id)),
                },
            }
        )

    return pico_sets


def generate_template_bytes() -> bytes:
    """Builds a minimal 3-sheet example workbook matching the real Phase 2
    export shape, for a 'download template' button in the UI or `main.py --template`."""
    pico_df = pd.DataFrame(
        [
            {
                "Population": "Locally advanced or metastatic NSCLC, EGFR exon 19 deletion",
                "PICO Set ID": "osimertinib-gefitinib-nsclc",
                "Comparator (C)": "Gefitinib",
                "Intervention (I)": "Osimertinib",
                "Outcomes (O)": (
                    "Overall survival; Progression-free survival; "
                    "Objective response rate; Grade >=3 adverse events"
                ),
                "Member States": "Denmark; Italy",
            }
        ]
    )
    validation_df = pd.DataFrame(
        [
            {
                "Population": "Locally advanced or metastatic NSCLC, EGFR exon 19 deletion",
                "Set ID": "osimertinib-gefitinib-nsclc",
                "Validation Result": "passed",
                "Failure Reason": None,
            }
        ]
    )
    evidence_df = pd.DataFrame(
        [
            {
                "Population": "Locally advanced or metastatic NSCLC, EGFR exon 19 deletion",
                "PICO Set ID": "osimertinib-gefitinib-nsclc",
                "Comparator": "Gefitinib",
                "Member State": "Denmark",
                "Tier": "1",
                "Source Reference": "PMID:12345678",
                "Evidence Quote": "Example supporting evidence quote.",
            }
        ]
    )
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        pico_df.to_excel(writer, index=False, sheet_name="5. Agent3 PICO Sets")
        validation_df.to_excel(writer, index=False, sheet_name="6. Agent4 Validation")
        evidence_df.to_excel(writer, index=False, sheet_name="7. Evidence Sources")
    return buffer.getvalue()
