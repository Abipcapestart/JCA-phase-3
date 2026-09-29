"""Pydantic schemas mirroring each agent prompt's declared JSON Schema."""

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class ComparatorModel(BaseModel):
    display_value: str
    member_states: List[str]


class OutcomesModel(BaseModel):
    clinical_effectiveness: List[str] = Field(default_factory=list)
    safety: List[str] = Field(default_factory=list)
    quality_of_life: List[str] = Field(default_factory=list)
    clinician_patient_reported: List[str] = Field(default_factory=list)


class Agent1Output(BaseModel):
    handoff_status: Literal["ready", "not_ready"]
    population: str
    intervention: str
    comparator: ComparatorModel
    outcomes: OutcomesModel
    study_design: List[str]


class SecondaryObjective(BaseModel):
    category: Literal[
        "Clinical Effectiveness",
        "Safety",
        "Quality of Life",
        "Clinician / Patient-Reported Outcomes (COA)",
    ]
    objective: str


class Objectives(BaseModel):
    primary: str
    secondary: List[SecondaryObjective]


class Agent2Output(BaseModel):
    study_title: str
    objectives: Objectives


class SemanticChecks(BaseModel):
    """What Agent 3's LLM call actually returns — the 3 checks that require
    genuine reading comprehension rather than exact-match / count logic."""

    comparator_phrasing_preserved: bool
    title_specific: bool
    title_objectives_consistent: bool
    failure_reason: Optional[str] = None


class ValidationChecks(BaseModel):
    """Full 7-check contract, merged from code (deterministic) + Agent 3 (semantic).

    Each field is individually optional: an early short-circuit (precheck failure,
    or an Agent 1 fidelity failure) means some checks never got a chance to run
    because the data they depend on (Agent 2/Agent 3 output) doesn't exist yet.
    Those surface as `null` on their own field — the object itself is always
    present, and any check that *did* run always reports its real boolean."""

    core_fields_unaltered: Optional[bool] = None
    comparator_phrasing_preserved: Optional[bool] = None
    outcome_categories_preserved: Optional[bool] = None
    study_design_complete: Optional[bool] = None
    title_specific: Optional[bool] = None
    objectives_structure_correct: Optional[bool] = None
    title_objectives_consistent: Optional[bool] = None


class ValidationResult(BaseModel):
    pico_set_id: str
    validation_result: Literal["passed", "failed_blocked"]
    checks: Optional[ValidationChecks] = None
    failure_reason: Optional[str] = None
