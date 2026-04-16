"""
lesson_session.py — Stateful multi-turn Gemini chat for a single learning session.

Session flow
------------
    LessonSession.teach()               → lesson text + character emotion
    LessonSession.next_question()       → quiz question
    LessonSession.evaluate_answer(ans)  → correct/wrong + optional trigger_help
        └─ if trigger_help → LessonSession.help_session created
    LessonSession.help_session.respond(msg)  → up to 3 turns
    (repeat quiz loop until no more questions)

Hard constraints
----------------
- HelpSession is capped at 3 turns, enforced in Python (not just in the prompt).
  A 4th call to respond() raises RuntimeError.
- trigger_help fires on the 2nd consecutive wrong answer for the same concept.
  The counter resets to 0 on any correct answer for that concept.
- All Gemini responses are validated for required keys before being returned.
  Missing keys raise ValueError so failures are loud, not silently swallowed.
- The Gemini model for LessonSession is settings.lesson_model (gemini-2.5-flash).
  The Gemini model for HelpSession is settings.help_model (gemini-2.5-flash-lite).
- HelpSession receives full quiz-failure context at creation time: failed question,
  correct answer, student wrong answers, and the original lesson explanation. This
  context is used to generate a rich gemini_handoff_prompt on unresolved turn 3,
  linking to AI Studio with the model pre-set to gemini-3-flash-preview.
- cached_content may be None when ENABLE_LESSON_CACHE=false — handled gracefully.
"""
from __future__ import annotations

import json
import logging
from typing import Any

import google.genai as genai
from google.genai import types as genai_types

from config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Emotion state constants
# ---------------------------------------------------------------------------

EMOTION_TEACHING = "teaching"
EMOTION_CURIOUS = "curious"
EMOTION_CELEBRATING = "celebrating"
EMOTION_ENCOURAGING = "encouraging"
EMOTION_THINKING = "thinking"
EMOTION_HELPING = "helping"
EMOTION_CONCERNED = "concerned"

# ---------------------------------------------------------------------------
# System prompt builders
# ---------------------------------------------------------------------------


def _build_lesson_system_prompt(
    lesson_content: dict[str, Any],
    outlines: Any,
    concept_map: Any,
) -> str:
    """
    Build the system prompt for LessonSession.

    Includes:
    - Role and behaviour instructions for the tutor character.
    - The full lesson JSON (lesson text, quiz questions).
    - Relevant sections from outlines and concept_map for context.

    The prompt is injected once as the system instruction for the chat session.
    When Gemini context caching is enabled, the lesson block is already in the
    cached prefix — this system prompt adds the per-lesson details on top.
    """
    lesson_id = lesson_content.get("lesson_id", "unknown")
    tier = lesson_content.get("tier", "beginner")

    # Pull prerequisite info from outlines if available
    prereq_text = ""
    if outlines and isinstance(outlines, list):
        for entry in outlines:
            if isinstance(entry, dict) and entry.get("lesson_id") == lesson_id:
                prereqs = entry.get("prerequisites", [])
                if prereqs:
                    prereq_text = f"\nPrerequisites for this lesson: {', '.join(prereqs)}"
                break

    # Pull cross-lesson context from concept_map if available
    concept_map_text = ""
    if concept_map and isinstance(concept_map, dict):
        lessons_map = concept_map.get("lessons", {})
        lesson_concepts = lessons_map.get(lesson_id, {})
        if lesson_concepts:
            concept_map_text = (
                f"\nKey concepts for {lesson_id}: "
                + json.dumps(lesson_concepts, ensure_ascii=False)
            )

    return f"""You are an encouraging, patient tutor for a self-paced Linux course.
Your role is to teach the lesson below, then quiz the learner on the material.

LESSON DETAILS
--------------
Lesson ID : {lesson_id}
Difficulty: {tier}
{prereq_text}
{concept_map_text}

LESSON CONTENT (JSON)
---------------------
{json.dumps(lesson_content, indent=2, ensure_ascii=False)}

BEHAVIOURAL RULES
-----------------
1. When asked to teach, present the lesson clearly and engagingly. End with a
   brief summary of key takeaways.
2. When asked for a quiz question, present ONE question at a time from the lesson's
   quiz section. Use the exact question text, format, and options from the JSON.
3. When evaluating an answer, return ONLY valid JSON matching the required schema.
4. Be warm and encouraging. Celebrate correct answers; gently correct wrong ones.
5. Never reveal quiz answers before the learner submits a response.
6. Respond ONLY in valid JSON when a JSON schema is specified in the instruction.
"""


def _build_help_system_prompt(
    lesson_content: dict[str, Any],
    failed_question: dict[str, Any] | None = None,
    student_wrong_answers: list[str] | None = None,
    lesson_teach_text: str = "",
) -> str:
    """
    Build the system prompt for HelpSession.

    HelpSession is a separate chat with a 3-turn cap. It receives enough lesson
    context to answer follow-up questions, but does not share chat history with
    the main LessonSession.

    When quiz-failure context is provided (failed_question, student_wrong_answers,
    lesson_teach_text), it is injected into the prompt so the tutor knows exactly
    what the learner got wrong and can target the explanation accordingly. The
    handoff instruction block tells the model how to build a self-contained prompt
    for the Gemini app if the learner remains stuck on turn 3.
    """
    lesson_id = lesson_content.get("lesson_id", "unknown")
    tier = lesson_content.get("tier", "beginner")
    failed_question = failed_question or {}
    student_wrong_answers = student_wrong_answers or []

    # Build quiz-failure context block
    question_text = failed_question.get("question", failed_question.get("question_text", ""))
    correct_answer = failed_question.get("answer", failed_question.get("correct_answer", ""))
    options = failed_question.get("options", [])
    options_text = "\n".join(f"  - {o}" for o in options) if options else ""
    wrong_answers_text = (
        "\n".join(f"  - {a}" for a in student_wrong_answers)
        if student_wrong_answers
        else "  (not recorded)"
    )

    quiz_context = f"""
FAILED QUIZ QUESTION
--------------------
Question : {question_text}
Options  :{("\n" + options_text) if options_text else " (none)"}
Correct  : {correct_answer}
Student's wrong answer(s):
{wrong_answers_text}
"""

    teach_context = ""
    if lesson_teach_text:
        teach_context = f"""
ORIGINAL LESSON EXPLANATION (what the student was shown)
---------------------------------------------------------
{lesson_teach_text[:1500]}
"""

    handoff_instruction = """
HANDOFF PROMPT INSTRUCTIONS (for turn 3 only, if unresolved)
-------------------------------------------------------------
If the learner is still confused on your final turn, set "gemini_handoff_prompt" to a
self-contained prompt structured EXACTLY as follows (fill in all placeholders):

\"\"\"
I am a beginner learning Linux. I am studying [CONCEPT NAME] from lesson [LESSON_ID].

My tutor explained it like this:
[PASTE A 2-3 SENTENCE SUMMARY OF THE ORIGINAL EXPLANATION]

I got this quiz question wrong twice:
Question: [EXACT QUESTION TEXT]
Options: [LIST OPTIONS IF ANY]
Correct answer: [CORRECT ANSWER]
My wrong answer(s): [STUDENT'S WRONG ANSWERS]

My tutor then tried to help me in a short session but I am still confused.

Please explain this concept to me differently, using a fresh real-world analogy.
Then ask me one simple question to check my understanding.
\"\"\"

This prompt must be usable with zero additional context — treat it as if it will be
sent to a brand-new AI that has never seen this conversation.
If resolved, set "gemini_handoff_prompt" to null.
"""

    return f"""You are a patient tutor helping a learner who got stuck on a quiz question
in a Linux course.

LESSON CONTEXT
--------------
Lesson ID : {lesson_id}
Difficulty: {tier}

LESSON CONTENT (JSON)
---------------------
{json.dumps(lesson_content, indent=2, ensure_ascii=False)}
{teach_context}{quiz_context}
BEHAVIOURAL RULES
-----------------
1. Answer the learner's question clearly, using examples from the lesson where helpful.
2. Do NOT simply give away the quiz answer — guide the learner toward understanding.
3. Keep responses concise (3–5 sentences). You have a maximum of 3 turns.
4. On your final turn (turn 3), if the learner still seems confused, include a
   "gemini_handoff_prompt" field in your JSON response — structured as described below.
5. Respond ONLY in valid JSON matching the required schema.
{handoff_instruction}"""


# ---------------------------------------------------------------------------
# HelpSession
# ---------------------------------------------------------------------------


class HelpSession:
    """
    A short side-conversation (max 3 turns) to help a learner who is stuck.

    Created by LessonSession when evaluate_answer() sets trigger_help=True.
    After the session ends (resolved or 3 turns exhausted), control returns
    to the quiz loop in LessonSession.

    The 3-turn cap is enforced in Python — calling respond() a 4th time raises
    RuntimeError regardless of what the prompt says.
    """

    MAX_TURNS = 3

    def __init__(
        self,
        lesson_content: dict[str, Any],
        failed_question: dict[str, Any] | None = None,
        student_wrong_answers: list[str] | None = None,
        lesson_teach_text: str = "",
    ) -> None:
        self._lesson_content = lesson_content
        self._failed_question = failed_question or {}
        self._student_wrong_answers = student_wrong_answers or []
        self._lesson_teach_text = lesson_teach_text
        self._turn_count = 0
        self._resolved = False

        client = genai.Client()
        self._chat = client.chats.create(
            model=settings.help_model,
            config=genai_types.GenerateContentConfig(
                system_instruction=_build_help_system_prompt(
                    lesson_content,
                    failed_question=self._failed_question,
                    student_wrong_answers=self._student_wrong_answers,
                    lesson_teach_text=self._lesson_teach_text,
                ),
            ),
        )

    @property
    def turn_count(self) -> int:
        return self._turn_count

    @property
    def resolved(self) -> bool:
        return self._resolved

    def respond(self, message: str) -> dict[str, Any]:
        """
        Send one learner message to the help tutor and get a response.

        Args:
            message: The learner's question or follow-up message.

        Returns:
            Dict with keys:
                resolved (bool): True if the learner indicated they understand.
                character_emotion_state (str): Emotion for the character widget.
                gemini_handoff_prompt (str | None): Non-empty on turn 3 if unresolved,
                    None otherwise. This prompt is shown to the learner as a deep-link
                    into the Gemini app. NEVER logged.

        Raises:
            RuntimeError: If called more than MAX_TURNS times.
        """
        if self._turn_count >= self.MAX_TURNS:
            raise RuntimeError(
                f"HelpSession turn cap reached ({self.MAX_TURNS}/{self.MAX_TURNS}). "
                "No further turns allowed."
            )

        self._turn_count += 1
        is_final_turn = self._turn_count >= self.MAX_TURNS

        schema_note = (
            'Return JSON: {"resolved": bool, "character_emotion_state": str, '
            '"gemini_handoff_prompt": str_or_null}'
        )
        if is_final_turn:
            schema_note += (
                " This is the FINAL turn. If the learner still seems confused, "
                'populate "gemini_handoff_prompt" with a self-contained prompt '
                "they can use in the Gemini app. If resolved, set it to null."
            )

        prompt = f"{message}\n\n[Instruction: {schema_note}]"

        try:
            response = self._chat.send_message(prompt)
            raw = _extract_json(_require_text(response, "HelpSession.respond"))
        except Exception as exc:
            logger.error(
                "HelpSession Gemini call failed on turn %d: %s", self._turn_count, exc,
                exc_info=True,
            )
            raise

        _validate_keys(raw, {"resolved", "character_emotion_state"}, context="HelpSession.respond")

        self._resolved = bool(raw.get("resolved", False))

        # gemini_handoff_prompt is optional — None when resolved or not final turn
        handoff = raw.get("gemini_handoff_prompt") or None

        # On final turn, if unresolved and no handoff provided, generate a fallback
        if is_final_turn and not self._resolved and not handoff:
            lesson_id = self._lesson_content.get("lesson_id", "this lesson")
            question_text = self._failed_question.get(
                "question",
                self._failed_question.get("question_text", "the quiz question"),
            )
            correct_answer = self._failed_question.get(
                "answer",
                self._failed_question.get("correct_answer", ""),
            )
            wrong = (
                ", ".join(self._student_wrong_answers)
                if self._student_wrong_answers
                else "unknown"
            )
            teach_summary = self._lesson_teach_text[:300] if self._lesson_teach_text else ""
            handoff = (
                f"I am a beginner learning Linux. I am studying lesson {lesson_id}.\n\n"
                + (
                    f"My tutor explained it like this:\n{teach_summary}\n\n"
                    if teach_summary
                    else ""
                )
                + f"I got this quiz question wrong twice:\nQuestion: {question_text}\n"
                + (f"Correct answer: {correct_answer}\n" if correct_answer else "")
                + f"My wrong answer(s): {wrong}\n\n"
                "My tutor tried to help me but I am still confused.\n\n"
                "Please explain this concept to me differently using a fresh real-world"
                " analogy, then ask me one simple question to check my understanding."
            )
            logger.info(
                "HelpSession: generated fallback gemini_handoff_prompt (not logged for privacy)"
            )

        return {
            "resolved": self._resolved,
            "character_emotion_state": raw.get("character_emotion_state", EMOTION_HELPING),
            "gemini_handoff_prompt": handoff,
        }


# ---------------------------------------------------------------------------
# LessonSession
# ---------------------------------------------------------------------------


class LessonSession:
    """
    Stateful multi-turn Gemini chat session for one lesson.

    Lifecycle:
        1. __init__  — builds system prompt, starts Gemini chat (with or without cache).
        2. teach()   — Turn 1: delivers the lesson text, returns key concepts.
        3. next_question() — returns the next quiz question from the lesson JSON.
        4. evaluate_answer(answer) — evaluates the answer; may set trigger_help=True.
        5. (Repeat 3–4 until all questions exhausted or session is completed.)

    When trigger_help=True, the caller should create a HelpSession via
    self.help_session (automatically set on the 2nd consecutive wrong answer
    for the same concept).
    """

    def __init__(
        self,
        lesson_id: str,
        tier: str,
        lesson_content: dict[str, Any],
        outlines: Any,
        concept_map: Any,
        cached_content: Any | None = None,
    ) -> None:
        """
        Initialise a new lesson session.

        Args:
            lesson_id: e.g. "L01".
            tier: "beginner" | "intermediate" | "advanced".
            lesson_content: Parsed lesson JSON for this lesson+tier. Must contain
                            "lesson" and "quiz" keys.
            outlines: Parsed outlines.yaml (list of lesson dicts). Used to build
                      the system prompt with prerequisite info.
            concept_map: Parsed concept_map.json (dict). Used to add concept
                         cross-reference context to the system prompt.
            cached_content: Gemini CachedContent handle from cache_manager.get_cache(),
                            or None when caching is disabled. When None, the model
                            is initialised without a cached prefix.
        """
        self.lesson_id = lesson_id
        self.tier = tier
        self._lesson_content = lesson_content
        self._outlines = outlines
        self._concept_map = concept_map

        # Extract quiz questions from lesson JSON
        quiz = lesson_content.get("quiz", {})
        self._questions: list[dict[str, Any]] = list(quiz.get("questions", []))
        self._question_index = 0

        # Track consecutive wrong answers per concept to trigger help
        # Key: concept identifier (question index as str), Value: wrong answer count
        self._consecutive_wrong: dict[str, int] = {}

        # Track all wrong answer strings per concept for HelpSession context
        self._wrong_answers: dict[str, list[str]] = {}

        # Lesson text stored after teach() completes — passed to HelpSession
        self._teach_text: str = ""

        # HelpSession is created on demand when trigger_help fires
        self.help_session: HelpSession | None = None

        # Build system prompt
        system_prompt = _build_lesson_system_prompt(lesson_content, outlines, concept_map)

        # Initialise Gemini chat — with or without cached prefix.
        # GenerateContentConfig.cached_content expects a cache name string (e.g.
        # "projects/.../cachedContents/..."), not the full CachedContent object.
        client = genai.Client()
        cache_name: str | None = cached_content.name if cached_content is not None else None
        config = genai_types.GenerateContentConfig(
            system_instruction=system_prompt,
            cached_content=cache_name,
        )
        self._chat = client.chats.create(
            model=settings.lesson_model,
            config=config,
        )
        logger.debug(
            "LessonSession started for %s (cache=%s)", lesson_id, cache_name is not None
        )

    # -----------------------------------------------------------------------
    # Teach phase
    # -----------------------------------------------------------------------

    def teach(self) -> dict[str, Any]:
        """
        Deliver the lesson (Turn 1 of the chat session).

        Asks Gemini to present the lesson text and extract key concepts.

        Returns:
            Dict with keys:
                lesson_text (str): The lesson content presented to the learner.
                character_emotion_state (str): Initial character emotion ("teaching").
                key_concepts (list[str]): High-level concepts covered in this lesson.

        Raises:
            ValueError: If the Gemini response is missing required keys.
        """
        prompt = (
            "Please teach this lesson to the learner. Present the content clearly and "
            "engagingly, then summarise the key concepts.\n\n"
            "Return JSON: "
            '{"lesson_text": str, "character_emotion_state": str, "key_concepts": [str]}'
        )

        try:
            response = self._chat.send_message(prompt)
            raw = _extract_json(_require_text(response, "LessonSession.teach"))
        except Exception as exc:
            logger.error(
                "LessonSession.teach Gemini call failed for %s/%s: %s",
                self.lesson_id, self.tier, exc, exc_info=True,
            )
            raise

        _validate_keys(
            raw, {"lesson_text", "character_emotion_state", "key_concepts"}, context="teach"
        )

        self._teach_text = raw["lesson_text"]

        return {
            "lesson_text": raw["lesson_text"],
            "character_emotion_state": raw.get("character_emotion_state", EMOTION_TEACHING),
            "key_concepts": raw.get("key_concepts", []),
        }

    # -----------------------------------------------------------------------
    # Quiz phase
    # -----------------------------------------------------------------------

    def next_question(self) -> dict[str, Any]:
        """
        Return the next quiz question for this lesson.

        Questions are served in order from the lesson JSON. Gemini is asked to
        format the question for display; the raw question data is also available
        in the lesson JSON if needed for validation.

        Returns:
            Dict with keys:
                question_text (str): The question as presented to the learner.
                format (str): "multiple_choice" | "true_false" | "fill_blank"
                    | "command_completion".
                options (list[str]): Answer options (tap-to-select).
                character_emotion_state (str): Character emotion ("curious").

        Raises:
            IndexError: If there are no more questions.
            ValueError: If the Gemini response is missing required keys.
        """
        if self._question_index >= len(self._questions):
            raise IndexError(
                f"No more quiz questions for {self.lesson_id} "
                f"(asked {self._question_index}/{len(self._questions)})"
            )

        q = self._questions[self._question_index]
        prompt = (
            f"Present quiz question {self._question_index + 1} to the learner.\n\n"
            f"Question data: {json.dumps(q, ensure_ascii=False)}\n\n"
            'Return JSON: {"question_text": str, "format": str, "options": [str], '
            '"character_emotion_state": str}'
        )

        try:
            response = self._chat.send_message(prompt)
            raw = _extract_json(_require_text(response, "LessonSession.next_question"))
        except Exception as exc:
            logger.error(
                "LessonSession.next_question Gemini call failed for %s q%d: %s",
                self.lesson_id, self._question_index, exc, exc_info=True,
            )
            raise

        _validate_keys(raw, {"question_text", "format", "options"}, context="next_question")

        return {
            "question_text": raw["question_text"],
            "format": raw.get("format", q.get("format", "multiple_choice")),
            "options": raw.get("options", q.get("options", [])),
            "character_emotion_state": raw.get("character_emotion_state", EMOTION_CURIOUS),
        }

    def evaluate_answer(self, answer: str) -> dict[str, Any]:
        """
        Evaluate the learner's answer to the current quiz question.

        Advances the question index on completion. Tracks consecutive wrong
        answers per question/concept and sets trigger_help=True on the 2nd
        consecutive wrong answer. Creates self.help_session when triggered.

        Args:
            answer: The learner's selected answer (e.g. "A", "True", a text string).

        Returns:
            Dict with keys:
                correct (bool): Whether the answer was correct.
                explanation (str): Brief explanation of the correct answer.
                concept_score_delta (float): Score change hint for the caller
                    (+0.1 correct, -0.1 wrong). Final FSRS update happens in summary_call.
                character_emotion_state (str): Emotion for the character widget.
                trigger_help (bool): True when the learner should be offered HelpSession.

        Raises:
            IndexError: If called when no question is active (question_index out of range).
            ValueError: If the Gemini response is missing required keys.
        """
        if self._question_index >= len(self._questions):
            raise IndexError("evaluate_answer called but no active question")

        q = self._questions[self._question_index]
        concept_key = str(self._question_index)

        prompt = (
            f"The learner answered: {answer!r}\n\n"
            f"Question data: {json.dumps(q, ensure_ascii=False)}\n\n"
            "Evaluate whether the answer is correct. Provide a brief, encouraging explanation.\n\n"
            'Return JSON: {"correct": bool, "explanation": str, "concept_score_delta": float, '
            '"character_emotion_state": str}'
        )

        try:
            response = self._chat.send_message(prompt)
            raw = _extract_json(_require_text(response, "LessonSession.evaluate_answer"))
        except Exception as exc:
            logger.error(
                "LessonSession.evaluate_answer Gemini call failed for %s q%d: %s",
                self.lesson_id, self._question_index, exc, exc_info=True,
            )
            raise

        _validate_keys(
            raw, {"correct", "explanation", "concept_score_delta"}, context="evaluate_answer"
        )

        correct = bool(raw.get("correct", False))

        # Update consecutive-wrong tracker
        if correct:
            self._consecutive_wrong[concept_key] = 0
            self._question_index += 1  # advance only on correct answer
        else:
            self._consecutive_wrong[concept_key] = (
                self._consecutive_wrong.get(concept_key, 0) + 1
            )
            self._wrong_answers.setdefault(concept_key, []).append(answer)

        # Trigger help on 2nd consecutive wrong answer for this concept
        trigger_help = not correct and self._consecutive_wrong.get(concept_key, 0) >= 2
        if trigger_help and self.help_session is None:
            self.help_session = HelpSession(
                lesson_content=self._lesson_content,
                failed_question=q,
                student_wrong_answers=list(self._wrong_answers_for_question(concept_key)),
                lesson_teach_text=self._teach_text,
            )
            logger.info(
                "HelpSession created for %s q%d after %d consecutive wrong answers",
                self.lesson_id, self._question_index,
                self._consecutive_wrong.get(concept_key, 0),
            )

        # Default emotions
        if correct:
            emotion = raw.get("character_emotion_state", EMOTION_CELEBRATING)
        elif trigger_help:
            emotion = raw.get("character_emotion_state", EMOTION_CONCERNED)
        else:
            emotion = raw.get("character_emotion_state", EMOTION_ENCOURAGING)

        return {
            "correct": correct,
            "explanation": raw["explanation"],
            "concept_score_delta": float(raw.get("concept_score_delta", 0.1 if correct else -0.1)),
            "character_emotion_state": emotion,
            "trigger_help": trigger_help,
        }

    # -----------------------------------------------------------------------
    # State helpers
    # -----------------------------------------------------------------------

    @property
    def questions_remaining(self) -> int:
        """Number of quiz questions not yet answered correctly."""
        return max(0, len(self._questions) - self._question_index)

    @property
    def total_questions(self) -> int:
        """Total number of quiz questions for this lesson."""
        return len(self._questions)

    def _wrong_answers_for_question(self, concept_key: str) -> list[str]:
        """Return the list of wrong answer strings recorded for a given concept key."""
        return self._wrong_answers.get(concept_key, [])


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _require_text(response: Any, context: str) -> str:
    """
    Extract non-None text from a Gemini response, raising ValueError if absent.

    The new google.genai SDK types response.text as ``str | None`` — it is None
    when the model returns no text candidate (e.g. safety block). Treating a
    missing text as a ValueError keeps errors loud and traceable.
    """
    text = response.text
    if text is None:
        raise ValueError(
            f"Gemini returned no text (possible safety block or empty response) in {context}"
        )
    return text


def _extract_json(text: str) -> dict[str, Any]:
    """
    Extract and parse a JSON object from a Gemini response string.

    Gemini sometimes wraps JSON in markdown code fences (```json ... ```).
    This function strips the fence if present and parses the bare JSON.

    Args:
        text: Raw text from response.text.

    Returns:
        Parsed dict.

    Raises:
        ValueError: If no valid JSON object can be extracted.
    """
    text = text.strip()

    # Strip markdown code fence if present
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove opening fence (```json or ```)
        lines = lines[1:]
        # Remove closing fence
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    # Find the outermost JSON object
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError(f"No JSON object found in Gemini response: {text[:200]!r}")

    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError(f"Failed to parse JSON from Gemini response: {exc}") from exc


def _validate_keys(
    data: dict[str, Any],
    required: set[str],
    context: str = "",
) -> None:
    """
    Assert that all required keys are present in a response dict.

    Args:
        data: The parsed response dict.
        required: Set of key names that must be present.
        context: Label for the error message (e.g. "teach", "HelpSession.respond").

    Raises:
        ValueError: If any required key is missing.
    """
    missing = required - data.keys()
    if missing:
        raise ValueError(
            f"Gemini response missing required keys {missing} in {context}. "
            f"Got keys: {set(data.keys())}"
        )
