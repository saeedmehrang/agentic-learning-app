You are generating structured educational content for a Linux basics mobile learning app.

You will produce BOTH lesson content AND quiz questions for a single lesson × tier combination
in one response. Return a single JSON object with exactly two top-level keys: "lesson" and "quiz".

=== LESSON GENERATION INSTRUCTIONS ===

{{LESSON_GENERATION_INSTRUCTIONS}}

=== QUIZ GENERATION INSTRUCTIONS ===

{{QUIZ_GENERATION_INSTRUCTIONS}}

=== LESSON × TIER CONTEXT ===

The following compact context object defines this lesson and tier. Use ONLY the information
in this context — do not introduce concepts beyond what is listed in "concepts" and "examples"
for Beginner tier. The "generation_note" contains critical constraints; follow them exactly.
The "assumes" list tells you what the learner already knows from prior lessons.

```json
{{CONTEXT_JSON}}
```

=== QUIZ PARAMETERS ===

question_count: {{QUESTION_COUNT}}
formats: {{QUESTION_FORMATS}}

=== OUTPUT FORMAT ===

Return exactly one JSON object with this structure (no markdown fences, no text outside JSON):

{
  "lesson": { ...lesson JSON matching the lesson_generation.md schema... },
  "quiz": { ...quiz JSON matching the quiz_generation.md schema... }
}

The "lesson" object must have fields: lesson_id, title, tier, sections, key_takeaways, terminal_steps.
The "quiz" object must have fields: lesson_id, title, tier, questions.

Ensure lesson_id and tier in both objects match: lesson_id="{{LESSON_ID}}", tier="{{TIER}}".
