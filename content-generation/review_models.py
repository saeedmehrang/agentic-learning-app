"""Pydantic models for structured reviewer output."""
from __future__ import annotations

from pydantic import BaseModel, Field


class LessonReviewIssue(BaseModel):
    field: str        # e.g. "sections[1].body", "key_takeaways"
    severity: str     # "blocking" | "suggestion"
    description: str  # 1-2 sentences, specific and actionable
    rule_ref: str     # e.g. "lesson_quality_rule_2"


class QuizReviewIssue(BaseModel):
    question_id: str  # e.g. "L04-B-Q02"
    field: str        # e.g. "options", "explanation"
    severity: str     # "blocking" | "suggestion"
    description: str
    rule_ref: str


class ReviewResult(BaseModel):
    passed: bool = False  # Computed by Python after model_validate(), not by LLM
    lesson_issues: list[LessonReviewIssue] = Field(default_factory=list)
    quiz_issues: list[QuizReviewIssue] = Field(default_factory=list)
    lesson_summary: str = ""
    quiz_summary: str = ""

    def compute_passed(self) -> None:
        """Set passed=True iff no blocking issues exist. Call after model_validate()."""
        self.passed = not any(
            i.severity == "blocking"
            for i in self.lesson_issues + self.quiz_issues
        )
