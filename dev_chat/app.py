"""
Agentic Learning — Dev Chat

Local Streamlit interface for testing the full agent pipeline interactively.
Calls the FastAPI backend at BACKEND_URL (default: http://localhost:8080).

Layout
------
Left sidebar  — Session State Inspector (live agent output, phase, emotion, help turns)
Main column   — Chat thread (lesson, quiz, help, session complete)

Run:
    streamlit run dev_chat/app.py
"""
from __future__ import annotations

import os
import textwrap
from typing import Any

import requests
import streamlit as st

BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8080")

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="ADK Dev Chat",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Session state initialisation
# ---------------------------------------------------------------------------


def _init_state() -> None:
    defaults: dict[str, Any] = {
        "session_id": None,
        "uid": "test-user-dev",
        "context_output": {},
        "phase": "idle",          # idle | lesson | quiz | help | complete
        "messages": [],            # list of {role, content, meta}
        "current_question": None,  # QuizQuestionResponse dict
        "help_turns_remaining": 3,
        "error": None,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


_init_state()

# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------


def _post(path: str, body: dict | None = None, timeout: int = 60) -> dict:
    url = f"{BACKEND_URL}{path}"
    resp = requests.post(url, json=body or {}, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def _get(path: str, timeout: int = 60) -> dict:
    url = f"{BACKEND_URL}{path}"
    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def _add_message(role: str, content: str, meta: dict | None = None) -> None:
    st.session_state.messages.append({"role": role, "content": content, "meta": meta or {}})


# ---------------------------------------------------------------------------
# Sidebar — Session State Inspector
# ---------------------------------------------------------------------------


def render_sidebar() -> None:
    with st.sidebar:
        st.title("🔍 Session State")

        if st.session_state.phase == "idle":
            st.info("No active session")
        else:
            ctx = st.session_state.context_output
            phase = st.session_state.phase

            # Phase badge
            phase_color = {
                "lesson": "🟡",
                "quiz": "🟢",
                "help": "🔴",
                "complete": "⚫",
            }.get(phase, "⚪")
            st.markdown(f"**Phase:** {phase_color} `{phase.upper()}`")

            st.divider()

            # Context output fields
            if ctx:
                st.markdown("**Context Output**")
                st.caption(f"Concept: `{ctx.get('next_concept_id', '—')}`")
                st.caption(f"Tier: `{ctx.get('difficulty_tier', '—')}`")
                st.caption(f"Character: `{ctx.get('module_character_id', '—')}`")
                st.caption(f"Goal: {ctx.get('session_goal', '—')}")

            st.divider()

            # Session IDs
            st.markdown("**Session**")
            st.caption(f"UID: `{st.session_state.uid}`")
            st.caption(f"ID: `{st.session_state.session_id or '—'}`")

            # Help turns
            if phase == "help" or st.session_state.help_turns_remaining < 3:
                st.divider()
                remaining = st.session_state.help_turns_remaining
                color = "🟢" if remaining > 1 else "🔴"
                st.markdown(f"**Help Turns:** {color} `{remaining}/3 remaining`")

            # Last raw message meta
            if st.session_state.messages:
                last = st.session_state.messages[-1]
                if last.get("meta"):
                    st.divider()
                    st.markdown("**Last Agent Response**")
                    with st.expander("Raw JSON", expanded=False):
                        st.json(last["meta"])

        st.divider()

        # Backend health
        try:
            r = requests.get(f"{BACKEND_URL}/health", timeout=3)
            if r.status_code == 200:
                st.success("Backend: online")
            else:
                st.error(f"Backend: HTTP {r.status_code}")
        except requests.exceptions.ConnectionError:
            st.error("Backend: unreachable")
        except Exception:
            st.warning("Backend: unknown")

        st.caption(f"URL: `{BACKEND_URL}`")


# ---------------------------------------------------------------------------
# Main — chat thread rendering
# ---------------------------------------------------------------------------


def render_messages() -> None:
    for msg in st.session_state.messages:
        role = msg["role"]
        content = msg["content"]
        meta = msg.get("meta", {})

        if role == "assistant":
            emotion = meta.get("character_emotion_state", "")
            emotion_icon = {
                "welcome": "👋",
                "teaching": "📖",
                "curious": "🤔",
                "celebrating": "🎉",
                "encouraging": "👍",
                "helping": "🤝",
            }.get(emotion, "🤖")

            with st.chat_message("assistant", avatar=emotion_icon):
                st.markdown(content)
                if meta.get("key_concepts"):
                    st.caption("Key concepts: " + ", ".join(f"`{c}`" for c in meta["key_concepts"]))
                if meta.get("correct") is True:
                    st.success("✅ Correct!")
                elif meta.get("correct") is False:
                    st.error("❌ Incorrect")
                if meta.get("trigger_help"):
                    st.warning("🆘 Help triggered — HelpAgent activating…")

        elif role == "user":
            with st.chat_message("user"):
                st.markdown(content)

        elif role == "help":
            with st.chat_message("assistant", avatar="🤝"):
                st.markdown(content)
                remaining = meta.get("turns_remaining", 0)
                if meta.get("resolved"):
                    st.success("✅ Resolved!")
                elif remaining == 0:
                    st.warning("Turn limit reached. Generating Gemini handoff…")
                else:
                    st.caption(f"Help turns remaining: {remaining}/3")

        elif role == "handoff":
            with st.container(border=True):
                st.markdown("### 🔗 Still stuck? Continue learning in Gemini")
                prompt_text = meta.get("gemini_handoff_prompt", "")
                st.text_area("Pre-filled prompt (copy → paste into Gemini):", value=prompt_text, height=120)
                st.caption("This prompt was generated by HelpAgent based on your specific struggle.")

        elif role == "complete":
            with st.container(border=True):
                st.markdown("### 🎉 Session Complete!")
                st.markdown(content)
                if meta.get("quiz_scores"):
                    st.markdown("**Quiz scores:**")
                    for k, v in meta["quiz_scores"].items():
                        st.caption(f"`{k}`: {v:.2f}")
                if meta.get("summary_text"):
                    st.caption(meta["summary_text"])

        elif role == "system":
            st.info(content)


# ---------------------------------------------------------------------------
# Action handlers
# ---------------------------------------------------------------------------


def action_start_session(uid: str) -> None:
    st.session_state.uid = uid
    st.session_state.messages = []
    st.session_state.current_question = None
    st.session_state.help_turns_remaining = 3
    st.session_state.error = None

    with st.spinner("Starting session (ContextAgent)…"):
        try:
            data = _post("/session/start", {"uid": uid})
        except Exception as exc:
            st.session_state.error = f"Failed to start session: {exc}"
            return

    st.session_state.session_id = data["session_id"]
    st.session_state.context_output = data["context_output"]
    st.session_state.phase = "lesson"

    ctx = data["context_output"]
    _add_message(
        "system",
        f"Session started. Concept: `{ctx.get('next_concept_id')}` | "
        f"Tier: `{ctx.get('difficulty_tier')}` | "
        f"Character: `{ctx.get('module_character_id')}`",
    )


def action_get_lesson() -> None:
    sid = st.session_state.session_id
    with st.spinner("Loading lesson (LessonAgent)…"):
        try:
            data = _get(f"/session/{sid}/lesson")
        except Exception as exc:
            st.session_state.error = f"Lesson failed: {exc}"
            return

    st.session_state.phase = "quiz"
    _add_message(
        "assistant",
        data["lesson_text"],
        meta={
            "character_emotion_state": data.get("character_emotion_state", "teaching"),
            "key_concepts": data.get("key_concepts", []),
        },
    )


def action_get_question() -> None:
    sid = st.session_state.session_id
    with st.spinner("Generating quiz question…"):
        try:
            data = _get(f"/session/{sid}/quiz/question")
        except Exception as exc:
            st.session_state.error = f"Quiz question failed: {exc}"
            return

    st.session_state.current_question = data
    _add_message(
        "assistant",
        data["question_text"],
        meta={
            "character_emotion_state": data.get("character_emotion_state", "curious"),
            "format": data.get("format"),
            "options": data.get("options", []),
        },
    )


def action_submit_answer(answer: str) -> None:
    sid = st.session_state.session_id
    _add_message("user", answer)

    with st.spinner("Evaluating answer…"):
        try:
            data = _post(f"/session/{sid}/quiz/answer", {"answer": answer})
        except Exception as exc:
            st.session_state.error = f"Answer submission failed: {exc}"
            return

    _add_message(
        "assistant",
        data.get("explanation", ""),
        meta={
            "character_emotion_state": data.get("character_emotion_state", "encouraging"),
            "correct": data.get("correct"),
            "concept_score_delta": data.get("concept_score_delta"),
            "trigger_help": data.get("trigger_help", False),
        },
    )

    if data.get("trigger_help"):
        st.session_state.phase = "help"
        st.session_state.current_question = None
    else:
        st.session_state.current_question = None  # clear; user must request next question


def action_send_help(message: str) -> None:
    sid = st.session_state.session_id
    _add_message("user", message)

    with st.spinner("HelpAgent thinking…"):
        try:
            data = _post(f"/session/{sid}/help", {"message": message})
        except Exception as exc:
            st.session_state.error = f"Help turn failed: {exc}"
            return

    st.session_state.help_turns_remaining = data.get("turns_remaining", 0)

    _add_message(
        "help",
        _format_help_response(data),
        meta={
            "resolved": data.get("resolved"),
            "turns_remaining": data.get("turns_remaining", 0),
            "character_emotion_state": data.get("character_emotion_state", "helping"),
        },
    )

    if not data.get("resolved") and data.get("gemini_handoff_prompt"):
        _add_message(
            "handoff",
            "",
            meta={"gemini_handoff_prompt": data["gemini_handoff_prompt"]},
        )

    if data.get("resolved") or data.get("turns_remaining", 1) == 0:
        st.session_state.phase = "quiz"


def _format_help_response(data: dict) -> str:
    """Extract the agent's text from help response (if embedded)."""
    # HelpAgent returns structured JSON; the explanation is in character_emotion_state context
    # The agent text itself is in the message — return a placeholder here; the meta renders details
    if data.get("resolved"):
        return "Got it! Let's continue."
    return "Let me try to explain this differently…"


def action_complete_session() -> None:
    sid = st.session_state.session_id
    with st.spinner("Wrapping up (SummaryAgent + FSRS)…"):
        try:
            data = _post(f"/session/{sid}/complete")
        except Exception as exc:
            st.session_state.error = f"Session complete failed: {exc}"
            return

    summary = data.get("summary", {})
    st.session_state.phase = "complete"
    _add_message(
        "complete",
        f"Session complete! Time on task: {summary.get('time_on_task_seconds', '—')}s",
        meta=summary,
    )
    st.session_state.session_id = None


# ---------------------------------------------------------------------------
# Input area — phase-aware controls
# ---------------------------------------------------------------------------


def render_input_area() -> None:
    phase = st.session_state.phase
    question = st.session_state.current_question

    if phase == "idle":
        # Session start form
        col1, col2 = st.columns([3, 1])
        with col1:
            uid = st.text_input("Learner UID", value=st.session_state.uid, key="uid_input", label_visibility="collapsed", placeholder="Learner UID (e.g. test-user-dev)")
        with col2:
            if st.button("Start Session", type="primary", use_container_width=True):
                action_start_session(uid.strip() or "test-user-dev")
                st.rerun()

    elif phase == "lesson":
        if st.button("📖 Load Lesson", type="primary"):
            action_get_lesson()
            st.rerun()

    elif phase == "quiz":
        if question is None:
            # No active question — let user request one or finish
            col1, col2 = st.columns(2)
            with col1:
                if st.button("🎯 Next Question", type="primary"):
                    action_get_question()
                    st.rerun()
            with col2:
                if st.button("✅ Finish Session", type="secondary"):
                    action_complete_session()
                    st.rerun()
        else:
            # Render format-appropriate answer input
            fmt = question.get("format", "mc")
            options = question.get("options", [])

            if fmt == "tf":
                col1, col2 = st.columns(2)
                with col1:
                    if st.button(options[0] if options else "True", use_container_width=True):
                        action_submit_answer(options[0] if options else "True")
                        st.rerun()
                with col2:
                    if st.button(options[1] if len(options) > 1 else "False", use_container_width=True):
                        action_submit_answer(options[1] if len(options) > 1 else "False")
                        st.rerun()

            elif fmt in ("mc", "fill", "command"):
                selected = st.radio(
                    "Select your answer:",
                    options=options,
                    key=f"quiz_radio_{len(st.session_state.messages)}",
                )
                if st.button("Submit", type="primary"):
                    if selected:
                        action_submit_answer(selected)
                        st.rerun()

    elif phase == "help":
        help_input = st.chat_input("Ask HelpAgent your question…")
        if help_input:
            action_send_help(help_input)
            st.rerun()

    elif phase == "complete":
        if st.button("🔄 New Session", type="primary"):
            # Reset everything
            for key in list(st.session_state.keys()):
                del st.session_state[key]
            st.rerun()


# ---------------------------------------------------------------------------
# Main layout
# ---------------------------------------------------------------------------


def main() -> None:
    render_sidebar()

    st.title("🤖 Agentic Learning — Dev Chat")
    st.caption(
        "Local pipeline test interface. "
        "Calls the real backend (ContextAgent → LessonAgent → HelpAgent → SummaryAgent) "
        "with live Cloud SQL + Firestore."
    )

    if st.session_state.error:
        st.error(f"**Error:** {st.session_state.error}")
        if st.button("Clear error"):
            st.session_state.error = None
            st.rerun()

    # Chat thread
    render_messages()

    st.divider()

    # Phase-aware input controls
    render_input_area()

    # Dev hint when idle
    if st.session_state.phase == "idle" and not st.session_state.messages:
        st.markdown(
            textwrap.dedent("""
            **How to use:**
            1. Enter a learner UID and click **Start Session** — ContextAgent picks the next concept
            2. Click **Load Lesson** — LessonAgent teaches the concept
            3. Click **Next Question** for each quiz question; select your answer and submit
            4. Answer wrong twice → HelpAgent activates (3 turns max)
            5. Click **Finish Session** → SummaryAgent writes to Firestore and runs FSRS

            Use a different UID each time to test new-learner vs returning-learner flows.
            Wipe `test-user-dev` in Firebase Console to reset progress.
            """)
        )


main()
