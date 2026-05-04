"""Layer 3 view: AI-led structured behavioral interview.

Flow per competency:
  1. Show the main question. Candidate records (or types) their answer.
  2. LLM picks a follow-up bucket and writes the follow-up.
  3. Candidate records (or types) their follow-up answer.
  4. LLM scores the competency 0-20 based on both exchanges.
  5. Save row, advance to next competency.

5 competencies total -> Layer 3 score is 0-100.
"""

from __future__ import annotations

import time

import streamlit as st

from assessment_logic.layer3_logic import (
    COMPETENCY_COUNT,
    generate_followup,
    load_main_questions,
    score_competency,
)
from assessment_logic.llm_client import transcribe_audio
from assessment_logic.tts import speak
from database import db

from .state import advance_stage

try:
    from streamlit_mic_recorder import mic_recorder
    MIC_AVAILABLE = True
except ImportError:
    MIC_AVAILABLE = False


def render() -> None:
    candidate_id = st.session_state.candidate_id

    if not st.session_state.get("l3_started", False):
        _intro()
        return

    if not st.session_state.l3_main_questions:
        st.session_state.l3_main_questions = load_main_questions(candidate_id)

    comp_idx = st.session_state.l3_question_idx

    if comp_idx >= COMPETENCY_COUNT:
        _finish_layer()
        return

    comp = st.session_state.l3_main_questions[comp_idx]
    phase = st.session_state.l3_phase  # 'main' or 'followup'

    if phase == "main":
        _render_question(
            comp=comp,
            phase="main",
            question_text=comp["question"],
        )
    else:
        followup = st.session_state.l3_current_followup or {}
        _render_question(
            comp=comp,
            phase="followup",
            question_text=followup.get("question", "Can you tell me more about that?"),
        )


def _intro() -> None:
    st.title("Layer 3 — AI-Led Interview")
    st.markdown(
        f"""
        You'll be asked **{COMPETENCY_COUNT} interview questions**, one for each
        competency we're assessing. The AI interviewer will read each question
        out loud, then you'll record a voice answer (up to 2 minutes). The AI
        will then ask one follow-up based on what you said, and read that out
        loud too.

        **The five areas:** Proactivity, Learning Mindset, Adaptability,
        Collaboration, and Self-Reflection.

        **How it works:**
        1. Listen as the AI reads the question.
        2. Click **Start recording** and answer out loud.
        3. Click **Stop** when you're done (or the 2-minute timer will stop you).
        4. Review the transcript, then continue to the AI's follow-up.

        If you miss a question you can press **🔊 Replay question** at any
        point. If transcription fails, you can type your answer instead.

        **Tips:**
        - Use concrete, specific examples.
        - It's fine to pause and think before you answer.
        - Don't rush. Clarity beats speed.
        - Make sure your speakers or headphones are on.

        Total time: about 20 minutes.
        """
    )

    if not MIC_AVAILABLE:
        st.warning(
            "The voice recorder component isn't available. You'll be able to "
            "type your answers instead."
        )

    if st.button("Begin Layer 3", type="primary", use_container_width=True):
        st.session_state.l3_started = True
        st.session_state.l3_question_started_at = time.time()
        st.rerun()


def _render_question(comp: dict, phase: str, question_text: str) -> None:
    comp_idx = st.session_state.l3_question_idx
    phase_label = "Main question" if phase == "main" else "Follow-up"
    exchange_num = (comp_idx * 2) + (1 if phase == "main" else 2)
    total_exchanges = COMPETENCY_COUNT * 2

    col1, col2 = st.columns([3, 1])
    with col1:
        st.markdown(f"**Layer 3 — Competency {comp_idx + 1} of {COMPETENCY_COUNT}: {comp['competency_name']}**")
        st.progress(
            (exchange_num - 1) / total_exchanges,
            text=f"Exchange {exchange_num} of {total_exchanges}",
        )
    with col2:
        pass

    st.divider()
    heading = f"Question {comp_idx + 1}"
    if phase == "followup":
        heading += " — follow-up"
    st.markdown(f"### {heading}")
    st.info(question_text)

    # Speak the question. We track which (comp_idx, phase) pairs have already
    # been auto-played so we only autoplay once when the candidate first sees
    # the question. On reruns triggered by recording, we still render the
    # replay button but skip autoplay (otherwise the question would replay
    # every time the mic recorder updates).
    spoken_key = f"l3_spoken_{comp_idx}_{phase}"
    autoplay = not st.session_state.get(spoken_key, False)
    speak(question_text, autoplay=autoplay)
    if autoplay:
        st.session_state[spoken_key] = True

    # Use phase-specific keys so re-recording one phase doesn't clobber the other
    transcript_key = f"l3_transcript_{comp_idx}_{phase}"
    audio_bytes_key = f"l3_audio_{comp_idx}_{phase}"
    transcript_shown_key = f"l3_transcript_shown_{comp_idx}_{phase}"

    # --- Recording UI ---
    if not st.session_state.get(transcript_shown_key):
        if MIC_AVAILABLE:
            st.markdown("**Record your answer** (up to 2 minutes):")
            audio = mic_recorder(
                start_prompt="🎙️ Start recording",
                stop_prompt="⏹️ Stop recording",
                just_once=False,
                use_container_width=True,
                key=f"mic_{comp_idx}_{phase}",
            )
            if audio and audio.get("bytes"):
                st.session_state[audio_bytes_key] = audio["bytes"]
                with st.spinner("Transcribing..."):
                    try:
                        transcript = transcribe_audio(audio["bytes"])
                        if not transcript:
                            raise ValueError("Empty transcript")
                        st.session_state[transcript_key] = transcript
                        st.session_state[transcript_shown_key] = True
                        st.rerun()
                    except Exception as e:
                        # Surface enough detail to debug Azure config without
                        # spilling secrets. Common failure modes:
                        # - 401: bad/expired AZURE_OPENAI_API_KEY in secrets
                        # - 404 DeploymentNotFound: capstone-transcribe missing
                        # - 400 unsupported_format: API version too old (need
                        #   2025-03-01-preview or newer for gpt-4o-mini-transcribe)
                        st.error(
                            f"Transcription failed: {type(e).__name__} — {e}\n\n"
                            "Type your answer below as a fallback. "
                            "If this keeps happening, check the Streamlit logs "
                            "for the underlying Azure error."
                        )

        with st.expander("Or type your answer instead"):
            typed = st.text_area(
                "Type your answer",
                key=f"typed_{comp_idx}_{phase}",
                height=180,
            )
            if st.button("Submit typed answer", key=f"submit_typed_{comp_idx}_{phase}"):
                if typed.strip():
                    st.session_state[transcript_key] = typed.strip()
                    st.session_state[transcript_shown_key] = True
                    st.rerun()
                else:
                    st.warning("Please enter an answer first.")

    # --- Review and continue ---
    else:
        transcript = st.session_state.get(transcript_key, "")
        st.markdown("**Your transcribed answer:**")
        st.write(f"> {transcript}")

        if st.button("Re-record this answer", key=f"rerecord_{comp_idx}_{phase}"):
            st.session_state[transcript_shown_key] = False
            st.session_state.pop(transcript_key, None)
            st.session_state.pop(audio_bytes_key, None)
            st.rerun()

        if st.button("Continue", type="primary", key=f"continue_{comp_idx}_{phase}"):
            _advance_after_answer(comp, phase, transcript)


def _advance_after_answer(comp: dict, phase: str, transcript: str) -> None:
    """Branch on whether we just got a main answer or a follow-up answer."""
    comp_idx = st.session_state.l3_question_idx

    if phase == "main":
        # stash the main transcript, generate a follow-up, move to followup phase
        st.session_state[f"l3_main_transcript_{comp_idx}"] = transcript
        with st.spinner("Generating a follow-up question..."):
            followup = generate_followup(
                main_question=comp["question"],
                transcript=transcript,
                competency_name=comp["competency_name"],
                followup_goal=comp["followup_goal"],
            )
        st.session_state.l3_current_followup = followup
        st.session_state.l3_phase = "followup"
        st.session_state.l3_question_started_at = time.time()
        st.rerun()
        return

    # phase == "followup": we have everything needed to score this competency.
    main_transcript = st.session_state.get(f"l3_main_transcript_{comp_idx}", "")
    followup = st.session_state.get("l3_current_followup") or {}

    with st.spinner("Scoring this competency..."):
        result = score_competency(
            main_question=comp["question"],
            main_transcript=main_transcript,
            followup_question=followup.get("question", ""),
            followup_transcript=transcript,
            competency_name=comp["competency_name"],
            followup_goal=comp["followup_goal"],
        )

    main_dur = min(120.0, len(main_transcript.split()) / 2.5) if main_transcript else 0.0
    fu_dur = min(120.0, len(transcript.split()) / 2.5) if transcript else 0.0

    db.save_layer3_result(
        candidate_id=st.session_state.candidate_id,
        competency_order=comp_idx + 1,
        competency_id=comp["competency_id"],
        competency_key=comp["competency_key"],
        competency_name=comp["competency_name"],
        main_question=comp["question"],
        main_transcript=main_transcript,
        main_audio_duration_seconds=main_dur,
        followup_bucket=followup.get("bucket"),
        followup_question=followup.get("question"),
        followup_transcript=transcript,
        followup_audio_duration_seconds=fu_dur,
        competency_score=result["score"],
        scripted_flag=result["scripted_flag"],
        rationale=result["rationale"],
    )

    st.session_state.l3_answer_scores.append({
        "competency_key": comp["competency_key"],
        "competency_id": comp["competency_id"],
        "score": result["score"],
        "scripted_flag": result["scripted_flag"],
    })

    # advance to next competency
    st.session_state.l3_question_idx = comp_idx + 1
    st.session_state.l3_phase = "main"
    st.session_state.l3_current_followup = None
    st.session_state.l3_question_started_at = time.time()
    st.rerun()


def _finish_layer() -> None:
    st.title("Layer 3 Complete")
    st.success(
        "You've completed all three layers. On the next screen you'll see your "
        "full results and personalized feedback."
    )

    if st.button("See my results", type="primary", use_container_width=True):
        advance_stage("results")
