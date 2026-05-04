"""Final scoring matrix.

Combines the three layer scores into an overall score, maps competencies,
and classifies top-fit candidates.
"""

from __future__ import annotations

from typing import Tuple

LAYER1_WEIGHT = 0.30
LAYER2_WEIGHT = 0.35
LAYER3_WEIGHT = 0.35

TOP_FIT_MIN_OVERALL = 70
TOP_FIT_MIN_LAYER = 60
TOP_FIT_HIGH_COMPETENCY_THRESHOLD = 75
TOP_FIT_MIN_HIGH_COMPETENCIES = 2


def overall_score(layer1: float, layer2: float, layer3: float) -> float:
    """Weighted sum of the three layer scores."""
    return round(
        LAYER1_WEIGHT * layer1 + LAYER2_WEIGHT * layer2 + LAYER3_WEIGHT * layer3,
        2,
    )


def classify_top_fit(
    overall: float,
    layer1: float,
    layer2: float,
    layer3: float,
    competencies: dict,
) -> int:
    """Returns 1 if top-fit, 0 otherwise.
    
    Requirements (all must hold):
      - overall >= 70
      - no single layer score below 60
      - at least 2 competencies >= 75
    """
    if overall < TOP_FIT_MIN_OVERALL:
        return 0
    if min(layer1, layer2, layer3) < TOP_FIT_MIN_LAYER:
        return 0
    high_count = sum(
        1 for v in competencies.values() if v is not None and v >= TOP_FIT_HIGH_COMPETENCY_THRESHOLD
    )
    if high_count < TOP_FIT_MIN_HIGH_COMPETENCIES:
        return 0
    return 1


def assemble_final_scores(
    candidate_id: str,
    layer1: float,
    layer2: float,
    layer3: float,
    l1_comp: dict,
    l2_comp: dict,
    l3_comp: dict,
    candidate_feedback: str,
    recruiter_summary: str,
) -> dict:
    """Build the dict that goes into final_scores."""
    overall = overall_score(layer1, layer2, layer3)
    competencies = {**l1_comp, **l2_comp, **l3_comp}
    top_fit = classify_top_fit(overall, layer1, layer2, layer3, competencies)
    return {
        "candidate_id": candidate_id,
        "layer1_score": layer1,
        "layer2_score": layer2,
        "layer3_score": layer3,
        "overall_score": overall,
        "competency_analytical": l1_comp.get("competency_analytical"),
        "competency_numerical": l1_comp.get("competency_numerical"),
        "competency_verbal": l1_comp.get("competency_verbal"),
        "competency_strategic": l2_comp.get("competency_strategic"),
        "competency_adaptability": l2_comp.get("competency_adaptability"),
        "competency_l3_proactivity": l3_comp.get("competency_l3_proactivity"),
        "competency_l3_learning_mindset": l3_comp.get("competency_l3_learning_mindset"),
        "competency_l3_adaptability": l3_comp.get("competency_l3_adaptability"),
        "competency_l3_collaboration": l3_comp.get("competency_l3_collaboration"),
        "competency_l3_self_reflection": l3_comp.get("competency_l3_self_reflection"),
        "top_fit": top_fit,
        "recruiter_summary": recruiter_summary,
        "candidate_feedback": candidate_feedback,
    }
