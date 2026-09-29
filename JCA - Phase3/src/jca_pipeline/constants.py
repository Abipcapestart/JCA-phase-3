"""Fixed business rules for the Phase 3 handoff pipeline."""

FIXED_STUDY_DESIGN = [
    "Randomised controlled trials (RCTs)",
    "Cluster-randomised or pragmatic RCTs",
    "Non-randomised comparative studies",
    "Observational studies",
    "Real World Evidence studies",
]

OUTCOME_CATEGORY_KEYS = [
    "clinical_effectiveness",
    "safety",
    "quality_of_life",
    "clinician_patient_reported",
]

CATEGORY_LABELS = {
    "clinical_effectiveness": "Clinical Effectiveness",
    "safety": "Safety",
    "quality_of_life": "Quality of Life",
    "clinician_patient_reported": "Clinician / Patient-Reported Outcomes (COA)",
}
