"""Regression tests for pure scoring functions.

These tests do NOT call the LLM or the DB. They cover:
  - Layer 1 deterministic question selection and scoring
  - Layer 2 simulation engine (week ticks, fatigue, cash, completions, scoring)
  - Layer 3 aggregation (no LLM)
  - Scoring matrix (overall + top fit)
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from assessment_logic import layer1_logic, layer2_logic, scoring_matrix


# ----- Layer 1 -----

def test_theme_score_perfect():
    assert layer1_logic.theme_score(10, 10) == 100.0


def test_theme_score_zero():
    assert layer1_logic.theme_score(0, 10) == 0.0


def test_theme_score_partial():
    assert layer1_logic.theme_score(7, 10) == 70.0


def test_aggregate_layer1_averages_themes():
    total, comp = layer1_logic.aggregate_layer1(
        {"logical": 80, "numerical": 60, "verbal": 70}
    )
    assert total == 70.0
    assert comp["competency_analytical"] == 80
    assert comp["competency_numerical"] == 60
    assert comp["competency_verbal"] == 70


def test_select_questions_is_deterministic():
    q1 = layer1_logic.select_questions("candidate-abc", "logical")
    q2 = layer1_logic.select_questions("candidate-abc", "logical")
    assert [q.question_id for q in q1] == [q.question_id for q in q2]
    assert [q.correct_option for q in q1] == [q.correct_option for q in q2]


def test_select_questions_different_candidates_may_differ():
    q1 = layer1_logic.select_questions("candidate-aaa", "logical")
    q2 = layer1_logic.select_questions("candidate-zzz", "logical")
    assert q1 != q2


# ----- Layer 2 simulation engine -----

@pytest.fixture
def scenario():
    return layer2_logic.load_scenario()


def test_initial_state_seeds_correctly(scenario):
    state = layer2_logic.initial_state(scenario)
    assert state["current_week"] == 1
    assert state["cash"] == 500000
    assert state["reputation"] == 60
    assert state["total_weeks"] == 8
    # all 6 consultants start at 0 fatigue
    assert all(v == 0 for v in state["fatigue"].values())
    assert len(state["fatigue"]) == 6
    # all projects start as "available"
    assert all(p["status"] == "available" for p in state["projects"].values())


def test_advance_week_increments_counter(scenario):
    state = layer2_logic.initial_state(scenario)
    new_state = layer2_logic.advance_week(scenario, state, weekly_assignments={})
    assert new_state["current_week"] == 2
    # original state untouched (deepcopy)
    assert state["current_week"] == 1


def test_unstaffed_active_project_eventually_cancels(scenario):
    """If a project becomes active and is then unstaffed for 2+ weeks, it cancels."""
    state = layer2_logic.initial_state(scenario)
    # Week 1: staff P3 (Operations+Analytics) with David and Eva
    state = layer2_logic.advance_week(scenario, state, {"P3": ["C4", "C5"]})
    assert state["projects"]["P3"]["status"] == "active"
    # Week 2 and 3: leave it unstaffed
    state = layer2_logic.advance_week(scenario, state, {})
    state = layer2_logic.advance_week(scenario, state, {})
    assert state["projects"]["P3"]["status"] == "cancelled"
    # reputation should have dropped
    assert state["reputation"] < 60


def test_project_completes_when_fully_staffed_for_duration(scenario):
    """P1 has duration 4 weeks. Staff Anna for 4 straight weeks -> completes."""
    state = layer2_logic.initial_state(scenario)
    initial_cash = state["cash"]
    for _ in range(4):
        state = layer2_logic.advance_week(scenario, state, {"P1": ["C1"]})
    assert state["projects"]["P1"]["status"] == "completed"
    # cash should have grown (revenue 320k - 4 weeks * 60k burn = +80k at full quality, plus rep gain)
    assert state["cash"] > initial_cash


def test_fatigue_rises_with_staffing_falls_on_bench(scenario):
    """Anna on a project for 2 weeks -> fatigue rises. Then bench her -> fatigue falls."""
    state = layer2_logic.initial_state(scenario)
    state = layer2_logic.advance_week(scenario, state, {"P1": ["C1"]})
    state = layer2_logic.advance_week(scenario, state, {"P1": ["C1"]})
    assert state["fatigue"]["C1"] == 30  # 15 per week * 2

    state = layer2_logic.advance_week(scenario, state, {})  # bench everyone
    assert state["fatigue"]["C1"] == 5  # 30 - 25 recovery


def test_sick_consultant_cant_be_staffed(scenario):
    """Ben is sick in Week 3. Assigning him this week should not consume him."""
    state = layer2_logic.initial_state(scenario)
    # advance to Week 3
    for _ in range(2):
        state = layer2_logic.advance_week(scenario, state, {})
    assert state["current_week"] == 3
    # try to staff Ben (C2) on P2
    state = layer2_logic.advance_week(scenario, state, {"P2": ["C2"]})
    # P2 should still be unstaffed (Ben was filtered out as sick)
    # weeks_unstaffed_consecutive should have incremented
    assert state["projects"]["P2"]["weeks_unstaffed_consecutive"] >= 1


def test_double_booking_only_counts_first_project(scenario):
    """Anna assigned to P1 and P2 same week: she only counts on P1 (the first listed)."""
    state = layer2_logic.initial_state(scenario)
    state = layer2_logic.advance_week(scenario, state, {"P1": ["C1"], "P2": ["C1"]})
    # Anna's fatigue should rise once (15), not twice (30)
    assert state["fatigue"]["C1"] == 15
    # P1 should have progressed, P2 should not
    assert state["projects"]["P1"]["weeks_staffed_correctly"] == 1
    assert state["projects"]["P2"]["weeks_staffed_correctly"] == 0


def test_validate_weekly_assignments_catches_double_booking(scenario):
    state = layer2_logic.initial_state(scenario)
    warnings = layer2_logic.validate_weekly_assignments(
        scenario, state, week=1,
        assignments={"P1": ["C1"], "P2": ["C1"]},
    )
    assert any("multiple" in w.lower() for w in warnings)


def test_validate_weekly_assignments_catches_invisible_project(scenario):
    """P5 is only available from Week 3. Assigning in Week 1 should warn."""
    state = layer2_logic.initial_state(scenario)
    warnings = layer2_logic.validate_weekly_assignments(
        scenario, state, week=1,
        assignments={"P5": ["C1"]},
    )
    assert any("aren't active" in w or "not active" in w.lower() for w in warnings)


def test_tradeoff_choice_applied_in_week_6(scenario):
    """Picking option A in Week 6 should add cash and apply reputation effect."""
    state = layer2_logic.initial_state(scenario)
    # advance to Week 6
    for _ in range(5):
        state = layer2_logic.advance_week(scenario, state, {})
    assert state["current_week"] == 6
    cash_before = state["cash"]
    rep_before = state["reputation"]
    state = layer2_logic.advance_week(scenario, state, {}, tradeoff_choice="A")
    # Option A: cash +220k, reputation -5
    assert state["cash"] == cash_before + 220000
    assert state["reputation"] == rep_before - 5
    assert state["tradeoff_choice"] == "A"


def test_visible_projects_filters_by_week_and_status(scenario):
    """P5 not visible in Week 1 (available from W3). P8 not visible in Week 5 (available from W7)."""
    state = layer2_logic.initial_state(scenario)
    visible_w1 = [p["id"] for p in layer2_logic.projects_visible_in_week(scenario, state, 1)]
    assert "P5" not in visible_w1
    assert "P8" not in visible_w1
    assert "P1" in visible_w1

    visible_w7 = [p["id"] for p in layer2_logic.projects_visible_in_week(scenario, state, 7)]
    assert "P5" in visible_w7
    assert "P8" in visible_w7


def test_outcome_score_starting_state_baseline(scenario):
    """End the sim with no actions -> 0 score (calibration anchor)."""
    state = layer2_logic.initial_state(scenario)
    for _ in range(8):
        state = layer2_logic.advance_week(scenario, state, {})
    result = layer2_logic.final_layer2_score(state, scenario)
    # Calibration: doing nothing should give 0
    assert result["layer2_total"] == 0.0
    assert result["outcome_score"] == 0.0
    assert result["process_score"] == 0.0


def test_decent_play_scores_meaningfully(scenario):
    """A decent strategy should land somewhere in the 70s-90s range."""
    state = layer2_logic.initial_state(scenario)
    plans = [
        {"P1": ["C1"], "P2": ["C2", "C3"], "P3": ["C4", "C5"], "P4": ["C6"]},
        {"P1": ["C1"], "P2": ["C2", "C3"], "P3": ["C4", "C5"], "P4": ["C6"]},
        {"P1": ["C1"], "P2": ["C3"],       "P3": ["C4", "C5"], "P4": ["C6"]},
        {"P1": ["C1"], "P2": ["C2", "C3"], "P3": ["C4", "C5"], "P4": ["C6"]},
        {"P2": ["C2", "C3"], "P3": ["C4", "C5"], "P5": ["C1", "C6"]},
        {"P2": ["C2", "C3"], "P3": ["C4", "C5"], "P5": ["C1", "C6"]},
        {"P2": ["C2", "C3"], "P3": ["C4", "C5"], "P8": ["C6"]},
        {"P3": ["C4", "C5"], "P8": ["C6"]},
    ]
    for week_idx, plan in enumerate(plans, start=1):
        d = ("david_resigns", "retain") if week_idx == 2 else None
        t = "A" if week_idx == 6 else None
        state = layer2_logic.advance_week(scenario, state, plan, tradeoff_choice=t, decision_choice=d)
    result = layer2_logic.final_layer2_score(state, scenario)
    assert result["layer2_total"] >= 70


def test_week2_decision_retain_preserves_consultant(scenario):
    """If candidate retains David, he keeps working through the simulation."""
    state = layer2_logic.initial_state(scenario)
    state = layer2_logic.advance_week(scenario, state, {"P3": ["C4", "C5"]})
    cash_after_w1 = state["cash"]
    state = layer2_logic.advance_week(
        scenario, state, {"P3": ["C4", "C5"]},
        decision_choice=("david_resigns", "retain"),
    )
    # cash should be lower by the retention bonus (40k) plus burn
    assert state["cash"] < cash_after_w1
    # David is still around in week 5
    available_w5 = layer2_logic.consultants_available_in_week(scenario, state, 5)
    assert any(c["id"] == "C4" for c in available_w5)


def test_week2_decision_let_go_consultant_leaves_after_notice(scenario):
    """If candidate lets David go with 1 week notice (decided in W2), he's available
    through W3 and gone from W4 onwards."""
    state = layer2_logic.initial_state(scenario)
    state = layer2_logic.advance_week(scenario, state, {})
    state = layer2_logic.advance_week(
        scenario, state, {},
        decision_choice=("david_resigns", "let_go"),
    )
    # Week 3: David still available (notice period)
    available_w3 = layer2_logic.consultants_available_in_week(scenario, state, 3)
    assert any(c["id"] == "C4" for c in available_w3)
    # Week 4: David gone
    available_w4 = layer2_logic.consultants_available_in_week(scenario, state, 4)
    assert not any(c["id"] == "C4" for c in available_w4)


def test_week2_decision_accelerate_consultant_leaves_immediately(scenario):
    """Accelerate means David is gone starting that same week."""
    state = layer2_logic.initial_state(scenario)
    state = layer2_logic.advance_week(scenario, state, {})
    state = layer2_logic.advance_week(
        scenario, state, {},
        decision_choice=("david_resigns", "accelerate"),
    )
    # In Week 3 (and beyond), David is not available
    available_w3 = layer2_logic.consultants_available_in_week(scenario, state, 3)
    assert not any(c["id"] == "C4" for c in available_w3)


def test_pending_decision_detected_in_week2(scenario):
    state = layer2_logic.initial_state(scenario)
    # Advance to Week 2
    state = layer2_logic.advance_week(scenario, state, {})
    # Week 2 should have a pending decision
    pending = layer2_logic.pending_decision_for_week(scenario, state, 2)
    assert pending is not None
    assert pending["consultant_id"] == "C4"


def test_pending_decision_cleared_after_choice(scenario):
    state = layer2_logic.initial_state(scenario)
    state = layer2_logic.advance_week(scenario, state, {})  # to Week 2
    state = layer2_logic.advance_week(
        scenario, state, {},
        decision_choice=("david_resigns", "retain"),
    )
    # Now in Week 3, no pending decision
    pending_w3 = layer2_logic.pending_decision_for_week(scenario, state, 3)
    assert pending_w3 is None


def test_full_layer2_score_keys(scenario):
    """The final score dict has all expected keys."""
    state = layer2_logic.initial_state(scenario)
    for _ in range(8):
        state = layer2_logic.advance_week(scenario, state, {})
    result = layer2_logic.final_layer2_score(state, scenario)
    assert set(result.keys()) >= {"layer2_total", "outcome_score", "process_score",
                                    "outcome_breakdown", "process_breakdown"}
    assert 0 <= result["layer2_total"] <= 100
    assert 0 <= result["outcome_score"] <= 100
    assert 0 <= result["process_score"] <= 100


def test_aggregate_layer2_returns_competencies(scenario):
    state = layer2_logic.initial_state(scenario)
    for _ in range(8):
        state = layer2_logic.advance_week(scenario, state, {})
    total, comp = layer2_logic.aggregate_layer2(state, scenario)
    assert "competency_strategic" in comp
    assert "competency_adaptability" in comp


def test_good_player_scores_higher_than_no_action(scenario):
    """A reasonable strategy outscores doing nothing."""
    # No-action baseline
    no_action = layer2_logic.initial_state(scenario)
    for _ in range(8):
        no_action = layer2_logic.advance_week(scenario, no_action, {})
    no_action_result = layer2_logic.final_layer2_score(no_action, scenario)

    # Reasonable play: staff main projects continuously
    good = layer2_logic.initial_state(scenario)
    weekly_plan = [
        {"P1": ["C1"], "P2": ["C2", "C3"], "P3": ["C4", "C5"], "P4": ["C6"]},
        {"P1": ["C1"], "P2": ["C2", "C3"], "P3": ["C4", "C5"], "P4": ["C6"]},
        {"P1": ["C1"], "P2": ["C3"],       "P3": ["C4", "C5"], "P4": ["C6"]},  # Ben sick W3
        {"P1": ["C1"], "P2": ["C2", "C3"], "P3": ["C4", "C5"], "P4": ["C6"]},
        {"P2": ["C2", "C3"], "P3": ["C4", "C5"], "P4": ["C6"], "P1": ["C1"]},  # P1 completes W4
        {"P2": ["C2", "C3"], "P3": ["C4", "C5"], "P4": ["C6"]},
        {"P2": ["C2", "C3"], "P3": ["C4", "C5"], "P8": ["C6", "C1"]},
        {"P3": ["C4", "C5"], "P8": ["C6", "C1"]},
    ]
    for week_idx, plan in enumerate(weekly_plan, start=1):
        tradeoff = "A" if week_idx == 6 else None
        good = layer2_logic.advance_week(scenario, good, plan, tradeoff_choice=tradeoff)
    good_result = layer2_logic.final_layer2_score(good, scenario)

    assert good_result["layer2_total"] > no_action_result["layer2_total"]


# ----- Scoring matrix -----

def test_overall_score_weights():
    # 60*0.30 + 70*0.35 + 80*0.35 = 18 + 24.5 + 28 = 70.5
    assert scoring_matrix.overall_score(60, 70, 80) == 70.5


def test_top_fit_happy_path():
    competencies = {
        "competency_analytical": 80,
        "competency_numerical": 75,
        "competency_strategic": 78,
    }
    assert scoring_matrix.classify_top_fit(75, 70, 75, 80, competencies) == 1


def test_top_fit_fails_on_overall():
    competencies = {"a": 90, "b": 90}
    assert scoring_matrix.classify_top_fit(65, 70, 70, 70, competencies) == 0


def test_top_fit_fails_on_layer_floor():
    competencies = {"a": 90, "b": 90}
    assert scoring_matrix.classify_top_fit(72, 70, 55, 90, competencies) == 0


def test_top_fit_fails_on_competency_count():
    competencies = {"a": 80, "b": 70, "c": 65}  # only one >= 75
    assert scoring_matrix.classify_top_fit(75, 70, 70, 75, competencies) == 0


def test_assemble_final_scores_structure():
    data = scoring_matrix.assemble_final_scores(
        candidate_id="cid-1",
        layer1=75, layer2=80, layer3=70,
        l1_comp={"competency_analytical": 75, "competency_numerical": 75, "competency_verbal": 75},
        l2_comp={"competency_strategic": 80, "competency_adaptability": 80},
        l3_comp={
            "competency_l3_proactivity": 70,
            "competency_l3_learning_mindset": 70,
            "competency_l3_adaptability": 70,
            "competency_l3_collaboration": 70,
            "competency_l3_self_reflection": 70,
        },
        candidate_feedback="feedback",
        recruiter_summary="summary",
    )
    assert data["candidate_id"] == "cid-1"
    assert data["top_fit"] == 1
    assert "overall_score" in data
    assert data["competency_l3_proactivity"] == 70


# ----- Layer 3 aggregation (pure, no LLM) -----

def test_aggregate_layer3_sums_to_100():
    from assessment_logic import layer3_logic
    competency_scores = [
        {"competency_key": "proactivity", "score": 14},
        {"competency_key": "learning_mindset", "score": 12},
        {"competency_key": "adaptability", "score": 10},
        {"competency_key": "collaboration", "score": 16},
        {"competency_key": "self_reflection", "score": 8},
    ]
    total, comp = layer3_logic.aggregate_layer3(competency_scores)
    assert total == 60.0  # 14+12+10+16+8
    # individual scores scaled to 0-100
    assert comp["competency_l3_proactivity"] == 70.0
    assert comp["competency_l3_self_reflection"] == 40.0


def test_aggregate_layer3_empty():
    from assessment_logic import layer3_logic
    total, comp = layer3_logic.aggregate_layer3([])
    assert total == 0.0
    assert comp["competency_l3_proactivity"] == 0.0
    assert comp["competency_l3_self_reflection"] == 0.0


def test_interpret_total_bands():
    from assessment_logic import layer3_logic
    assert layer3_logic.interpret_total(35)["label"] == "Below threshold"
    assert layer3_logic.interpret_total(50)["label"] == "Borderline"
    assert layer3_logic.interpret_total(65)["label"] == "Good"
    assert layer3_logic.interpret_total(80)["label"] == "Strong"
    assert layer3_logic.interpret_total(95)["label"] == "Exceptional"
