"""Layer 1 view: cognitive assessment.

Per-question timer (theme-specific). Uses streamlit_autorefresh to tick
the clock every second. On expiry, submission is forced server-side
(comparing start_time to now).

Renders dynamic option counts (3-5 options) and an optional answer-grid
image for abstract reasoning questions.
"""

from __future__ import annotations

import time

import streamlit as st
from streamlit_autorefresh import st_autorefresh

from assessment_logic.layer1_logic import (
    QUESTIONS_PER_THEME,
    THEMES,
    select_questions,
    theme_score,
    time_limit_for,
)
from database import db

from .state import advance_stage


def render() -> None:
    candidate_id = st.session_state.candidate_id
    theme_idx = st.session_state.l1_theme_idx
    question_idx = st.session_state.l1_question_idx

    if theme_idx >= len(THEMES):
        _finish_layer(candidate_id)
        return

    theme = THEMES[theme_idx]

    # Theme intro screen (only before the first question of a theme)
    if question_idx == 0 and not st.session_state.get(f"l1_{theme}_started", False):
        _theme_intro(theme, theme_idx)
        return

    # Lazy-load questions for this theme
    if theme not in st.session_state.l1_questions_cache:
        st.session_state.l1_questions_cache[theme] = select_questions(candidate_id, theme)

    questions = st.session_state.l1_questions_cache[theme]

    if question_idx >= len(questions):
        _finish_theme(candidate_id, theme)
        return

    question = questions[question_idx]
    _render_question(candidate_id, theme, theme_idx, question_idx, question, len(questions))


def _theme_intro(theme: str, theme_idx: int) -> None:
    st.title(f"Layer 1 — {theme.capitalize()} Reasoning")
    st.caption(f"Theme {theme_idx + 1} of {len(THEMES)}")

    seconds = time_limit_for(theme)

    if theme_idx == 0:
        st.markdown(
            f"""
            Layer 1 tests your reasoning across three themes: logical,
            numerical, and verbal. You'll see **{QUESTIONS_PER_THEME} questions
            per theme** ({QUESTIONS_PER_THEME * len(THEMES)} total).

            **Each question has a per-theme time limit.** If time expires, the
            question is marked incorrect and you move on automatically.
            Unanswered questions cannot be revisited.

            ### Before you begin — please make sure you have:
            - 📝 **Pen and paper** for working through problems
            - 🧮 **A calculator** (the numerical theme requires arithmetic on
              percentages, ratios, and multi-step figures)
            - 🪑 A quiet, uninterrupted environment for the next ~30 minutes

            The logical block mixes verbal-logic prompts with abstract
            figural-reasoning items (pick the next figure in a sequence).
            The numerical block uses charts and tables to test data
            interpretation. The verbal block presents short passages followed
            by a statement — your job is to decide whether the statement is
            **True**, **False**, or **Cannot Say** based only on the passage.

            Pick the best answer; you will not see whether you got each
            question right.

            **{theme.capitalize()} time limit: {seconds} seconds per question.**
            """
        )
    else:
        st.markdown(
            f"Theme {theme_idx} complete. Next up: **{theme.capitalize()} Reasoning** "
            f"— {QUESTIONS_PER_THEME} questions, **{seconds} seconds each**."
        )

    if st.button(f"Begin {theme.capitalize()} Theme", type="primary"):
        st.session_state[f"l1_{theme}_started"] = True
        st.session_state.l1_question_started_at = time.time()
        st.rerun()


def _render_question(
    candidate_id: str, theme: str, theme_idx: int, question_idx: int,
    question, total: int,
) -> None:
    seconds = time_limit_for(theme)

    # Tick every second
    st_autorefresh(interval=1000, key=f"l1_tick_{theme}_{question_idx}")

    started_at = st.session_state.l1_question_started_at or time.time()
    if st.session_state.l1_question_started_at is None:
        st.session_state.l1_question_started_at = started_at

    elapsed = time.time() - started_at
    remaining = max(0, int(seconds - elapsed))

    # Header
    col1, col2 = st.columns([3, 1])
    with col1:
        st.markdown(f"**Layer 1 — {theme.capitalize()} Reasoning**")
        st.progress((question_idx) / total, text=f"Question {question_idx + 1} of {total}")
    with col2:
        # Color thresholds scale with the time limit
        green_cut = max(20, seconds // 3)
        yellow_cut = max(10, seconds // 6)
        color = "🟢" if remaining > green_cut else ("🟡" if remaining > yellow_cut else "🔴")
        st.metric("Time remaining", f"{color} {remaining}s")

    st.divider()

    # Main image (chart, sequence, etc.)
    if question.chart_path:
        try:
            st.image(question.chart_path)
        except Exception:
            pass

    st.markdown(f"### {question.question_text}")

    # Optional second image (abstract: A-E option grid)
    if question.answer_image_path:
        try:
            st.image(question.answer_image_path)
        except Exception:
            pass

    # Options — dynamic count, support 3/4/5
    n_opts = len(question.options)
    letters = ["A", "B", "C", "D", "E"][:n_opts]
    selection_key = f"l1_{theme}_{question_idx}_selection"

    # Letter-only rendering is reserved for abstract-reasoning items where
    # the letters are baked into the answer-grid image. Everything else
    # (including verbal True/False/Cannot Say) shows the option text.
    use_letter_only = question.locked and question.answer_image_path is not None

    if use_letter_only:
        display = [f"**{letters[i]}**" for i in range(n_opts)]
    else:
        display = [opt for opt in question.options]

    choice_display = st.radio(
        "Select one:",
        options=display,
        key=selection_key,
        index=None,
        horizontal=use_letter_only,  # letter-only options look better in a row
    )
    chosen_letter = None
    if choice_display is not None:
        chosen_letter = letters[display.index(choice_display)]

    # Auto-submit on timeout OR manual submit
    submit_clicked = st.button(
        "Submit answer",
        type="primary",
        disabled=(chosen_letter is None),
        key=f"submit_{theme}_{question_idx}",
    )
    timed_out = remaining <= 0

    if submit_clicked or timed_out:
        _save_and_advance(
            candidate_id, theme, theme_idx, question_idx, question,
            chosen_letter, int(elapsed), timed_out, seconds,
        )


def _save_and_advance(
    candidate_id: str, theme: str, theme_idx: int, question_idx: int,
    question, chosen_letter: str | None, elapsed: int, timed_out: bool,
    seconds: int,
) -> None:
    is_correct = (chosen_letter == question.correct_option)
    db.save_layer1_result(
        candidate_id=candidate_id,
        theme=theme,
        question_id=question.question_id,
        question_text=question.question_text,
        options_shown=question.options,
        correct_option=question.correct_option,
        candidate_answer=chosen_letter,
        is_correct=is_correct,
        time_taken_seconds=min(elapsed, seconds),
    )

    # reset timer for next question
    st.session_state.l1_question_started_at = time.time()
    st.session_state.l1_question_idx = question_idx + 1
    st.rerun()


def _finish_theme(candidate_id: str, theme: str) -> None:
    rows = [r for r in db.get_layer1_results(candidate_id) if r["theme"] == theme]
    correct = sum(1 for r in rows if r["is_correct"])
    st.session_state.l1_theme_scores[theme] = theme_score(correct, QUESTIONS_PER_THEME)
    st.session_state.l1_theme_idx += 1
    st.session_state.l1_question_idx = 0
    st.session_state.l1_question_started_at = None
    st.rerun()


def _finish_layer(candidate_id: str) -> None:
    """All three themes done. Move on to Layer 2 with no score reveal."""
    st.title("Layer 1 Complete")
    st.success(
        "Nice work — you've finished the cognitive assessment. Your full results "
        "will be shown after you complete all three layers."
    )

    st.markdown(
        """
        ---
        **Next — Layer 2: Firm Simulation**

        You'll run a consulting firm for 8 simulated weeks. Assign consultants to
        projects, manage cash and reputation, and respond to events as they
        happen. **20 minutes** in one continuous timer.
        """
    )

    if st.button("Begin Layer 2", type="primary", use_container_width=True):
        advance_stage("layer2")
