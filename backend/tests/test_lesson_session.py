"""
Unit tests for LessonSession and HelpSession.

All google.generativeai calls are mocked — no network, no real Gemini API.
Tests cover the exact schema requirements from the PR-3 spec.
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import lesson_session as ls
from lesson_session import HelpSession, LessonSession

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_LESSON_ID = "L05"
_TIER = "beginner"

_SAMPLE_QUESTIONS = [
    {
        "question_id": "q1",
        "format": "multiple_choice",
        "text": "What does the `ls` command do?",
        "options": ["Lists files", "Moves files", "Copies files", "Deletes files"],
        "answer": "Lists files",
        "explanation": "`ls` lists directory contents.",
        "concept": "basic_commands",
    },
    {
        "question_id": "q2",
        "format": "true_false",
        "text": "The `pwd` command prints the current directory.",
        "options": ["True", "False"],
        "answer": "True",
        "explanation": "`pwd` stands for 'print working directory'.",
        "concept": "navigation",
    },
]

_LESSON_CONTENT: dict[str, Any] = {
    "lesson_id": _LESSON_ID,
    "tier": _TIER,
    "lesson": {
        "title": "The Shell",
        "sections": [{"heading": "Intro", "body": "The shell is a command-line interface."}],
        "key_takeaways": ["Shells interpret commands"],
        "terminal_steps": [],
    },
    "quiz": {"lesson_id": _LESSON_ID, "questions": _SAMPLE_QUESTIONS},
}

_OUTLINES: list[dict] = [
    {"lesson_id": _LESSON_ID, "title": "The Shell", "prerequisites": ["L04"]},
]

_CONCEPT_MAP: dict[str, Any] = {
    "lessons": {_LESSON_ID: {"concepts": ["shell", "commands"]}},
    "modules": {},
}


def _make_gemini_response(data: dict[str, Any]) -> MagicMock:
    """Create a mock Gemini response whose .text is a JSON string."""
    mock_response = MagicMock()
    mock_response.text = json.dumps(data)
    return mock_response


def _make_session(mock_chat: MagicMock | None = None) -> LessonSession:
    """
    Build a LessonSession with a mocked genai.Client.
    If mock_chat is provided it will be used as the underlying chat object.
    """
    with patch("lesson_session.genai.Client") as mock_client_cls:
        mock_client = MagicMock()
        if mock_chat is not None:
            mock_client.chats.create.return_value = mock_chat
        else:
            mock_client.chats.create.return_value = MagicMock()
        mock_client_cls.return_value = mock_client
        session = LessonSession(
            lesson_id=_LESSON_ID,
            tier=_TIER,
            lesson_content=_LESSON_CONTENT,
            outlines=_OUTLINES,
            concept_map=_CONCEPT_MAP,
            cached_content=None,
        )
    return session


# ---------------------------------------------------------------------------
# LessonSession — __init__
# ---------------------------------------------------------------------------


class TestLessonSessionInit:
    def test_starts_without_cached_content(self) -> None:
        """When cached_content=None, chats.create is called with config.cached_content=None."""
        with patch("lesson_session.genai.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.chats.create.return_value = MagicMock()
            mock_client_cls.return_value = mock_client
            LessonSession(
                lesson_id=_LESSON_ID,
                tier=_TIER,
                lesson_content=_LESSON_CONTENT,
                outlines=_OUTLINES,
                concept_map=_CONCEPT_MAP,
                cached_content=None,
            )
        config_arg = mock_client.chats.create.call_args.kwargs.get("config")
        assert config_arg is not None
        assert config_arg.cached_content is None

    def test_starts_with_cached_content(self) -> None:
        """When a cache handle is provided, config.cached_content is set to its .name."""
        mock_handle = MagicMock()
        mock_handle.name = "projects/test/cachedContents/block-0"
        with patch("lesson_session.genai.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.chats.create.return_value = MagicMock()
            mock_client_cls.return_value = mock_client
            LessonSession(
                lesson_id=_LESSON_ID,
                tier=_TIER,
                lesson_content=_LESSON_CONTENT,
                outlines=_OUTLINES,
                concept_map=_CONCEPT_MAP,
                cached_content=mock_handle,
            )
        config_arg = mock_client.chats.create.call_args.kwargs.get("config")
        assert config_arg is not None
        assert config_arg.cached_content == mock_handle.name

    def test_question_count_matches_lesson_json(self) -> None:
        session = _make_session()
        assert session.total_questions == len(_SAMPLE_QUESTIONS)

    def test_questions_remaining_starts_at_total(self) -> None:
        session = _make_session()
        assert session.questions_remaining == session.total_questions


# ---------------------------------------------------------------------------
# LessonSession — teach()
# ---------------------------------------------------------------------------


class TestTeachPhase:
    def test_returns_correct_schema(self) -> None:
        """teach() must return lesson_text, character_emotion_state, key_concepts."""
        mock_chat = MagicMock()
        mock_chat.send_message.return_value = _make_gemini_response({
            "lesson_text": "Welcome to The Shell lesson.",
            "character_emotion_state": "teaching",
            "key_concepts": ["shell", "commands", "navigation"],
        })
        session = _make_session(mock_chat)
        result = session.teach()

        assert result["lesson_text"] == "Welcome to The Shell lesson."
        assert result["character_emotion_state"] == "teaching"
        assert isinstance(result["key_concepts"], list)
        assert "shell" in result["key_concepts"]

    def test_raises_on_missing_lesson_text(self) -> None:
        """teach() raises ValueError if lesson_text is absent from Gemini response."""
        mock_chat = MagicMock()
        mock_chat.send_message.return_value = _make_gemini_response({
            "character_emotion_state": "teaching",
            "key_concepts": [],
        })
        session = _make_session(mock_chat)
        with pytest.raises(ValueError, match="lesson_text"):
            session.teach()


# ---------------------------------------------------------------------------
# LessonSession — next_question()
# ---------------------------------------------------------------------------


class TestQuizQuestion:
    def test_returns_correct_schema(self) -> None:
        """next_question() must return question_text, format, options, character_emotion_state."""
        mock_chat = MagicMock()
        mock_chat.send_message.return_value = _make_gemini_response({
            "question_text": "What does `ls` do?",
            "format": "multiple_choice",
            "options": ["Lists files", "Moves files", "Copies files", "Deletes files"],
            "character_emotion_state": "curious",
        })
        session = _make_session(mock_chat)
        result = session.next_question()

        assert result["question_text"] == "What does `ls` do?"
        assert result["format"] == "multiple_choice"
        assert isinstance(result["options"], list)
        assert len(result["options"]) == 4
        assert result["character_emotion_state"] == "curious"

    def test_raises_when_no_questions_remain(self) -> None:
        """next_question() raises IndexError after all questions are exhausted."""
        mock_chat = MagicMock()
        # Answers that return correct=True will advance the question index
        mock_chat.send_message.return_value = _make_gemini_response({
            "question_text": "Q",
            "format": "multiple_choice",
            "options": ["A"],
            "character_emotion_state": "curious",
        })
        session = _make_session(mock_chat)
        # Exhaust all questions by advancing _question_index directly
        session._question_index = session.total_questions
        with pytest.raises(IndexError):
            session.next_question()


# ---------------------------------------------------------------------------
# LessonSession — evaluate_answer()
# ---------------------------------------------------------------------------


class TestEvaluateAnswer:
    def test_correct_answer_schema_and_emotion(self) -> None:
        """Correct answer: correct=True, positive delta, 'celebrating' emotion."""
        mock_chat = MagicMock()
        mock_chat.send_message.return_value = _make_gemini_response({
            "correct": True,
            "explanation": "Well done! `ls` lists directory contents.",
            "concept_score_delta": 0.1,
            "character_emotion_state": "celebrating",
        })
        session = _make_session(mock_chat)
        result = session.evaluate_answer("Lists files")

        assert result["correct"] is True
        assert result["concept_score_delta"] > 0
        assert result["character_emotion_state"] == "celebrating"
        assert result["trigger_help"] is False

    def test_correct_answer_advances_question_index(self) -> None:
        """After a correct answer the question index should advance by 1."""
        mock_chat = MagicMock()
        mock_chat.send_message.return_value = _make_gemini_response({
            "correct": True,
            "explanation": "Correct!",
            "concept_score_delta": 0.1,
            "character_emotion_state": "celebrating",
        })
        session = _make_session(mock_chat)
        initial_index = session._question_index
        session.evaluate_answer("Lists files")
        assert session._question_index == initial_index + 1

    def test_first_wrong_answer_no_trigger(self) -> None:
        """First wrong answer: trigger_help=False, 'encouraging' emotion."""
        mock_chat = MagicMock()
        mock_chat.send_message.return_value = _make_gemini_response({
            "correct": False,
            "explanation": "Not quite — `ls` lists files, not moves them.",
            "concept_score_delta": -0.1,
            "character_emotion_state": "encouraging",
        })
        session = _make_session(mock_chat)
        result = session.evaluate_answer("Moves files")

        assert result["correct"] is False
        assert result["trigger_help"] is False
        assert result["character_emotion_state"] == "encouraging"

    def test_second_consecutive_wrong_triggers_help(self) -> None:
        """Second consecutive wrong answer for the same concept: trigger_help=True."""
        mock_chat = MagicMock()
        wrong_response = _make_gemini_response({
            "correct": False,
            "explanation": "Not quite.",
            "concept_score_delta": -0.1,
            "character_emotion_state": "encouraging",
        })
        mock_chat.send_message.return_value = wrong_response
        session = _make_session(mock_chat)

        # First wrong — no trigger
        result1 = session.evaluate_answer("Moves files")
        assert result1["trigger_help"] is False

        # Second wrong — trigger. HelpSession also calls genai.Client() — mock it.
        with patch("lesson_session.genai.Client") as mock_help_client_cls:
            mock_help_client = MagicMock()
            mock_help_client.chats.create.return_value = MagicMock()
            mock_help_client_cls.return_value = mock_help_client
            result2 = session.evaluate_answer("Copies files")

        assert result2["trigger_help"] is True
        assert session.help_session is not None

    def test_correct_answer_resets_consecutive_wrong_counter(self) -> None:
        """A correct answer after a wrong one resets the counter for that concept."""
        mock_chat = MagicMock()
        session = _make_session(mock_chat)

        # First wrong
        mock_chat.send_message.return_value = _make_gemini_response({
            "correct": False,
            "explanation": "Wrong.",
            "concept_score_delta": -0.1,
            "character_emotion_state": "encouraging",
        })
        session.evaluate_answer("Moves files")
        concept_key = "0"
        assert session._consecutive_wrong[concept_key] == 1

        # Correct — counter resets
        mock_chat.send_message.return_value = _make_gemini_response({
            "correct": True,
            "explanation": "Correct!",
            "concept_score_delta": 0.1,
            "character_emotion_state": "celebrating",
        })
        session.evaluate_answer("Lists files")
        assert session._consecutive_wrong.get(concept_key, 0) == 0

    def test_raises_on_missing_required_keys(self) -> None:
        """evaluate_answer() raises ValueError if Gemini omits 'correct' or 'explanation'."""
        mock_chat = MagicMock()
        mock_chat.send_message.return_value = _make_gemini_response({
            "character_emotion_state": "curious",
            # 'correct' and 'explanation' are missing
        })
        session = _make_session(mock_chat)
        with pytest.raises(ValueError, match="correct"):
            session.evaluate_answer("A")


# ---------------------------------------------------------------------------
# HelpSession
# ---------------------------------------------------------------------------


class TestHelpSession:
    def _make_help_session(self, mock_chat: MagicMock | None = None) -> HelpSession:
        with patch("lesson_session.genai.Client") as mock_client_cls:
            mock_client = MagicMock()
            if mock_chat is not None:
                mock_client.chats.create.return_value = mock_chat
            else:
                mock_client.chats.create.return_value = MagicMock()
            mock_client_cls.return_value = mock_client
            return HelpSession(lesson_content=_LESSON_CONTENT)

    def test_resolved_at_turn_one(self) -> None:
        """When the learner says they understand on turn 1, resolved=True."""
        mock_chat = MagicMock()
        mock_chat.send_message.return_value = _make_gemini_response({
            "resolved": True,
            "character_emotion_state": "celebrating",
            "gemini_handoff_prompt": None,
        })
        help_sess = self._make_help_session(mock_chat)
        result = help_sess.respond("Oh I see, thanks!")

        assert result["resolved"] is True
        assert result["gemini_handoff_prompt"] is None
        assert help_sess.turn_count == 1

    def test_unresolved_at_turn_three_has_handoff_prompt(self) -> None:
        """On turn 3 unresolved, gemini_handoff_prompt must be non-empty."""
        mock_chat = MagicMock()
        mock_chat.send_message.return_value = _make_gemini_response({
            "resolved": False,
            "character_emotion_state": "helping",
            "gemini_handoff_prompt": "I was studying Linux lesson L05 and got stuck...",
        })
        help_sess = self._make_help_session(mock_chat)

        for _ in range(3):
            result = help_sess.respond("I still don't get it.")

        assert result["resolved"] is False
        assert result["gemini_handoff_prompt"] is not None
        assert len(result["gemini_handoff_prompt"]) > 0

    def test_turn_four_raises_runtime_error(self) -> None:
        """Calling respond() a 4th time raises RuntimeError — hard cap enforced in Python."""
        mock_chat = MagicMock()
        mock_chat.send_message.return_value = _make_gemini_response({
            "resolved": False,
            "character_emotion_state": "helping",
            "gemini_handoff_prompt": None,
        })
        help_sess = self._make_help_session(mock_chat)

        for _ in range(3):
            help_sess.respond("Still confused.")

        with pytest.raises(RuntimeError, match="turn cap"):
            help_sess.respond("One more question")

    def test_turn_count_increments_correctly(self) -> None:
        """turn_count increments by 1 for each respond() call."""
        mock_chat = MagicMock()
        mock_chat.send_message.return_value = _make_gemini_response({
            "resolved": False,
            "character_emotion_state": "helping",
            "gemini_handoff_prompt": None,
        })
        help_sess = self._make_help_session(mock_chat)

        assert help_sess.turn_count == 0
        help_sess.respond("Question 1")
        assert help_sess.turn_count == 1
        help_sess.respond("Question 2")
        assert help_sess.turn_count == 2

    def test_fallback_handoff_generated_when_gemini_omits_it(self) -> None:
        """
        If Gemini omits gemini_handoff_prompt on the final turn while resolved=False,
        LessonSession generates a fallback prompt automatically.
        """
        mock_chat = MagicMock()
        mock_chat.send_message.return_value = _make_gemini_response({
            "resolved": False,
            "character_emotion_state": "helping",
            # gemini_handoff_prompt deliberately absent
        })
        help_sess = self._make_help_session(mock_chat)

        for _ in range(3):
            result = help_sess.respond("Still confused.")

        assert result["resolved"] is False
        # Fallback prompt must be non-empty
        assert result["gemini_handoff_prompt"] is not None
        assert len(result["gemini_handoff_prompt"]) > 0

    def _make_help_session_with_context(
        self,
        mock_chat: MagicMock | None = None,
        failed_question: dict | None = None,
        student_wrong_answers: list[str] | None = None,
        lesson_teach_text: str = "",
    ) -> HelpSession:
        with patch("lesson_session.genai.Client") as mock_client_cls:
            mock_client = MagicMock()
            if mock_chat is not None:
                mock_client.chats.create.return_value = mock_chat
            else:
                mock_client.chats.create.return_value = MagicMock()
            mock_client_cls.return_value = mock_client
            return HelpSession(
                lesson_content=_LESSON_CONTENT,
                failed_question=failed_question,
                student_wrong_answers=student_wrong_answers,
                lesson_teach_text=lesson_teach_text,
            )

    def test_handoff_prompt_contains_question_text(self) -> None:
        """Fallback handoff prompt includes the failed question text."""
        mock_chat = MagicMock()
        mock_chat.send_message.return_value = _make_gemini_response({
            "resolved": False,
            "character_emotion_state": "helping",
            "gemini_handoff_prompt": None,
        })
        failed_q = {
            "question": "What does chmod 644 mean?",
            "answer": "owner rw, group r, others r",
        }
        help_sess = self._make_help_session_with_context(
            mock_chat=mock_chat,
            failed_question=failed_q,
        )

        for _ in range(3):
            result = help_sess.respond("Still confused.")

        assert result["gemini_handoff_prompt"] is not None
        assert "chmod 644" in result["gemini_handoff_prompt"]

    def test_handoff_prompt_contains_correct_answer(self) -> None:
        """Fallback handoff prompt includes the correct answer string."""
        mock_chat = MagicMock()
        mock_chat.send_message.return_value = _make_gemini_response({
            "resolved": False,
            "character_emotion_state": "helping",
            "gemini_handoff_prompt": None,
        })
        failed_q = {
            "question": "What does chmod 644 mean?",
            "answer": "owner rw, group r, others r",
        }
        help_sess = self._make_help_session_with_context(
            mock_chat=mock_chat,
            failed_question=failed_q,
        )

        for _ in range(3):
            result = help_sess.respond("Still confused.")

        assert result["gemini_handoff_prompt"] is not None
        assert "owner rw, group r, others r" in result["gemini_handoff_prompt"]

    def test_handoff_prompt_contains_wrong_answers(self) -> None:
        """Fallback handoff prompt includes the student's wrong answers."""
        mock_chat = MagicMock()
        mock_chat.send_message.return_value = _make_gemini_response({
            "resolved": False,
            "character_emotion_state": "helping",
            "gemini_handoff_prompt": None,
        })
        help_sess = self._make_help_session_with_context(
            mock_chat=mock_chat,
            student_wrong_answers=["everyone can write", "root only"],
        )

        for _ in range(3):
            result = help_sess.respond("Still confused.")

        assert result["gemini_handoff_prompt"] is not None
        assert "everyone can write" in result["gemini_handoff_prompt"]


# ---------------------------------------------------------------------------
# LessonSession — teach text and wrong answer tracking (PR-6)
# ---------------------------------------------------------------------------


class TestTeachTextAndWrongAnswers:
    def test_lesson_session_stores_teach_text(self) -> None:
        """After teach() completes, _teach_text is set to the returned lesson_text."""
        mock_chat = MagicMock()
        mock_chat.send_message.return_value = _make_gemini_response({
            "lesson_text": "Linux is great",
            "character_emotion_state": "teaching",
            "key_concepts": [],
        })
        session = _make_session(mock_chat)
        session.teach()
        assert session._teach_text == "Linux is great"

    def test_evaluate_answer_records_wrong_answers(self) -> None:
        """Two wrong answers for the same question are both recorded in _wrong_answers."""
        mock_chat = MagicMock()
        wrong_response = _make_gemini_response({
            "correct": False,
            "explanation": "Not quite.",
            "concept_score_delta": -0.1,
            "character_emotion_state": "encouraging",
        })
        mock_chat.send_message.return_value = wrong_response
        session = _make_session(mock_chat)

        with patch("lesson_session.genai.Client") as mock_help_client_cls:
            mock_help_client = MagicMock()
            mock_help_client.chats.create.return_value = MagicMock()
            mock_help_client_cls.return_value = mock_help_client

            session.evaluate_answer("bad answer 1")
            session.evaluate_answer("bad answer 2")

        wrong_list = session._wrong_answers.get("0", [])
        assert "bad answer 1" in wrong_list
        assert "bad answer 2" in wrong_list

    def test_evaluate_answer_passes_teach_text_to_help_session(self) -> None:
        """After teach() and two wrong answers, help_session._lesson_teach_text matches."""
        mock_chat = MagicMock()
        session = _make_session(mock_chat)

        # Set teach text by mocking teach()
        mock_chat.send_message.return_value = _make_gemini_response({
            "lesson_text": "Shells interpret commands for you",
            "character_emotion_state": "teaching",
            "key_concepts": [],
        })
        session.teach()

        # Now mock wrong evaluate responses
        mock_chat.send_message.return_value = _make_gemini_response({
            "correct": False,
            "explanation": "Wrong.",
            "concept_score_delta": -0.1,
            "character_emotion_state": "encouraging",
        })

        with patch("lesson_session.genai.Client") as mock_help_client_cls:
            mock_help_client = MagicMock()
            mock_help_client.chats.create.return_value = MagicMock()
            mock_help_client_cls.return_value = mock_help_client

            session.evaluate_answer("wrong 1")
            session.evaluate_answer("wrong 2")

        assert session.help_session is not None
        assert session.help_session._lesson_teach_text == "Shells interpret commands for you"


# ---------------------------------------------------------------------------
# _extract_json helper
# ---------------------------------------------------------------------------


class TestExtractJson:
    def test_bare_json(self) -> None:
        data = ls._extract_json('{"key": "value"}')
        assert data == {"key": "value"}

    def test_json_in_markdown_fence(self) -> None:
        text = '```json\n{"key": "value"}\n```'
        data = ls._extract_json(text)
        assert data == {"key": "value"}

    def test_json_in_plain_fence(self) -> None:
        text = '```\n{"key": "value"}\n```'
        data = ls._extract_json(text)
        assert data == {"key": "value"}

    def test_json_with_surrounding_prose(self) -> None:
        text = 'Here is the result: {"key": "value"} as you can see.'
        data = ls._extract_json(text)
        assert data == {"key": "value"}

    def test_raises_on_no_json(self) -> None:
        with pytest.raises(ValueError, match="No JSON object"):
            ls._extract_json("This is not JSON at all.")

    def test_raises_on_invalid_json(self) -> None:
        with pytest.raises(ValueError):
            ls._extract_json("{invalid json}")


# ---------------------------------------------------------------------------
# HelpSession — resolved property and exception path (lines 286, 331-336)
# ---------------------------------------------------------------------------


class TestHelpSessionEdgeCases:
    def _make_help_session(self, mock_chat: MagicMock | None = None) -> HelpSession:
        with patch("lesson_session.genai.Client") as mock_client_cls:
            mock_client = MagicMock()
            if mock_chat is not None:
                mock_client.chats.create.return_value = mock_chat
            else:
                mock_client.chats.create.return_value = MagicMock()
            mock_client_cls.return_value = mock_client
            return HelpSession(lesson_content=_LESSON_CONTENT)

    def test_resolved_property_false_before_any_turn(self) -> None:
        """resolved property (line 286) must return False before any respond() call."""
        help_sess = self._make_help_session()
        assert help_sess.resolved is False

    def test_resolved_property_true_after_resolved_turn(self) -> None:
        """resolved property returns True after respond() sets _resolved=True."""
        mock_chat = MagicMock()
        mock_chat.send_message.return_value = _make_gemini_response({
            "resolved": True,
            "character_emotion_state": "celebrating",
        })
        help_sess = self._make_help_session(mock_chat)
        help_sess.respond("Got it, thanks!")
        assert help_sess.resolved is True

    def test_respond_re_raises_on_gemini_exception(self) -> None:
        """Lines 331-336: exception from send_message must propagate after logging."""
        mock_chat = MagicMock()
        mock_chat.send_message.side_effect = RuntimeError("Gemini exploded")
        help_sess = self._make_help_session(mock_chat)
        with pytest.raises(RuntimeError, match="Gemini exploded"):
            help_sess.respond("I don't understand")


# ---------------------------------------------------------------------------
# HelpSession — fallback handoff prompt (lines 345-378)
# ---------------------------------------------------------------------------


class TestHelpSessionFallbackHandoff:
    def _make_help_session(self, mock_chat: MagicMock | None = None) -> HelpSession:
        with patch("lesson_session.genai.Client") as mock_client_cls:
            mock_client = MagicMock()
            if mock_chat is not None:
                mock_client.chats.create.return_value = mock_chat
            else:
                mock_client.chats.create.return_value = MagicMock()
            mock_client_cls.return_value = mock_client
            return HelpSession(
                lesson_content=_LESSON_CONTENT,
                failed_question={
                    "question": "What does chmod do?",
                    "answer": "Changes file permissions",
                },
                student_wrong_answers=["Deletes files"],
                lesson_teach_text="chmod modifies file access control bits.",
            )

    def test_fallback_generated_when_gemini_omits_handoff(self) -> None:
        """When Gemini returns no gemini_handoff_prompt on turn 3 unresolved,
        a fallback must be generated (lines 345-378)."""
        mock_chat = MagicMock()
        mock_chat.send_message.return_value = _make_gemini_response({
            "resolved": False,
            "character_emotion_state": "helping",
            # No gemini_handoff_prompt key → triggers fallback
        })
        help_sess = self._make_help_session(mock_chat)
        for _ in range(3):
            result = help_sess.respond("Still confused.")
        assert result["gemini_handoff_prompt"] is not None
        assert "Linux" in result["gemini_handoff_prompt"]

    def test_fallback_includes_question_text(self) -> None:
        """Fallback prompt must reference the failed question text."""
        mock_chat = MagicMock()
        mock_chat.send_message.return_value = _make_gemini_response({
            "resolved": False,
            "character_emotion_state": "helping",
        })
        help_sess = self._make_help_session(mock_chat)
        for _ in range(3):
            result = help_sess.respond("Still confused.")
        assert "What does chmod do?" in result["gemini_handoff_prompt"]

    def test_fallback_not_generated_when_resolved(self) -> None:
        """If resolved=True on turn 3, fallback must NOT be generated."""
        mock_chat = MagicMock()
        mock_chat.send_message.return_value = _make_gemini_response({
            "resolved": True,
            "character_emotion_state": "celebrating",
        })
        help_sess = self._make_help_session(mock_chat)
        for _ in range(3):
            result = help_sess.respond("Now I get it!")
        assert result["gemini_handoff_prompt"] is None

    def test_fallback_includes_teach_text_when_present(self) -> None:
        """Teach summary must appear in fallback if lesson_teach_text was supplied."""
        mock_chat = MagicMock()
        mock_chat.send_message.return_value = _make_gemini_response({
            "resolved": False,
            "character_emotion_state": "helping",
        })
        help_sess = self._make_help_session(mock_chat)
        for _ in range(3):
            result = help_sess.respond("Still confused.")
        assert "chmod" in result["gemini_handoff_prompt"]


# ---------------------------------------------------------------------------
# LessonSession — teach() and evaluate_answer() exception paths (lines 507-512, 608, 624-629)
# ---------------------------------------------------------------------------


class TestLessonSessionExceptionPaths:
    def test_teach_re_raises_on_gemini_exception(self) -> None:
        """Lines 507-512: exception from send_message in teach() must propagate."""
        mock_chat = MagicMock()
        mock_chat.send_message.side_effect = RuntimeError("API unavailable")
        session = _make_session(mock_chat)
        with pytest.raises(RuntimeError, match="API unavailable"):
            session.teach()

    def test_evaluate_answer_raises_index_error_when_no_active_question(self) -> None:
        """Line 608: IndexError when question_index >= len(questions)."""
        mock_chat = MagicMock()
        session = _make_session(mock_chat)
        # Exhaust all questions by advancing index past end
        session._question_index = len(session._questions) + 1
        with pytest.raises(IndexError, match="no active question"):
            session.evaluate_answer("A")

    def test_evaluate_answer_re_raises_on_gemini_exception(self) -> None:
        """Lines 624-629: exception from send_message in evaluate_answer() must propagate."""
        mock_chat = MagicMock()
        # First call: teach succeeds, second call: evaluate_answer fails
        teach_resp = _make_gemini_response({
            "lesson_text": "Learn chmod",
            "character_emotion_state": "teaching",
            "key_concepts": ["chmod"],
        })
        mock_chat.send_message.side_effect = [
            teach_resp,
            RuntimeError("evaluate_answer API down"),
        ]
        session = _make_session(mock_chat)
        session.teach()
        with pytest.raises(RuntimeError, match="evaluate_answer API down"):
            session.evaluate_answer("A")


# ---------------------------------------------------------------------------
# _validate_keys — missing keys path (line 712)
# ---------------------------------------------------------------------------


class TestValidateKeys:
    def test_raises_value_error_on_missing_keys(self) -> None:
        """_validate_keys must raise ValueError naming the missing keys."""
        with pytest.raises(ValueError, match="missing required keys"):
            ls._validate_keys(
                {"resolved": True},
                {"resolved", "character_emotion_state"},
                context="test_context",
            )

    def test_no_raise_when_all_keys_present(self) -> None:
        """_validate_keys must not raise when all required keys are present."""
        ls._validate_keys(
            {"resolved": True, "character_emotion_state": "happy"},
            {"resolved", "character_emotion_state"},
            context="test_context",
        )

    def test_error_message_includes_context(self) -> None:
        """Error message must include the context label for debuggability."""
        with pytest.raises(ValueError, match="test_context"):
            ls._validate_keys({}, {"required_key"}, context="test_context")


# ---------------------------------------------------------------------------
# _require_text — None text path (line 712)
# ---------------------------------------------------------------------------


class TestRequireText:
    def test_raises_value_error_when_response_text_is_none(self) -> None:
        """_require_text must raise ValueError when response.text is None (safety block)."""
        mock_response = MagicMock()
        mock_response.text = None
        with pytest.raises(ValueError, match="Gemini returned no text"):
            ls._require_text(mock_response, "test_context")

    def test_returns_text_when_present(self) -> None:
        """_require_text must return the text string when it is non-None."""
        mock_response = MagicMock()
        mock_response.text = "some response text"
        result = ls._require_text(mock_response, "test_context")
        assert result == "some response text"

    def test_error_includes_context_label(self) -> None:
        """ValueError message must include the context label for traceability."""
        mock_response = MagicMock()
        mock_response.text = None
        with pytest.raises(ValueError, match="my_context"):
            ls._require_text(mock_response, "my_context")


# ---------------------------------------------------------------------------
# LessonSession.next_question() — exception path (lines 567-572)
# ---------------------------------------------------------------------------


class TestNextQuestionExceptionPath:
    def test_next_question_re_raises_on_gemini_exception(self) -> None:
        """Lines 567-572: exception in next_question() send_message must propagate."""
        mock_chat = MagicMock()
        # First advance to quiz phase by calling teach() successfully
        mock_chat.send_message.side_effect = [
            _make_gemini_response({
                "lesson_text": "Learn chmod",
                "character_emotion_state": "teaching",
                "key_concepts": ["chmod"],
            }),
            RuntimeError("next_question API down"),
        ]
        session = _make_session(mock_chat)
        session.teach()
        with pytest.raises(RuntimeError, match="next_question API down"):
            session.next_question()
