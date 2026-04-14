# Prompt: Generate concept_map.json

You are a curriculum designer creating the concept dependency map for a self-paced mobile Linux basics course. This file is consumed directly by the AI content generation pipeline: the `generation_note` and `assumes` fields are injected verbatim into every Gemini generation prompt to enforce pedagogical sequencing.

## Task

Given the completed `outlines.yaml`, produce a single JSON file named `concept_map.json` that maps every lesson to its introduced concepts, its dependencies, and authoring guidance for the content generation model.

## Input

You will receive the full `outlines.yaml` as context.

## Output Format

Return a single JSON object with exactly these top-level keys:

```json
{
  "course_id": "linux_basics",
  "course_title": "Linux Basics",
  "generated_by": "string",
  "generated_at": "YYYY-MM-DD",
  "course_metadata": {
    "total_lessons": 29,
    "total_modules": 9,
    "tiers": ["Beginner", "Intermediate", "Advanced"],
    "modules": {
      "1": { "title": "string", "lessons": ["L01", "L02", "L03"] },
      ...
    }
  },
  "lessons": {
    "L01": { ...lesson entry... },
    ...
  },
  "concept_index": {
    "concept name": "LXX",
    ...
  }
}
```

## Lesson Entry Schema

Each entry under `"lessons"` must have exactly these fields:

```json
"LXX": {
  "title": "string",
  "introduces": ["concept A", "concept B", ...],
  "assumes": [
    { "concept": "concept name", "introduced_in": "LXX" },
    ...
  ],
  "generation_note": "string",
  "cross_lesson_flag": "string"   // optional — only when a known ordering anomaly exists
}
```

### `introduces`

List every concept taught **for the first time** in this lesson. These must match exactly the `key_concepts` from `outlines.yaml` (same strings). Order: most foundational concept first.

### `assumes`

List every concept from prior lessons that this lesson's content directly depends on. Each entry names the concept and the lesson where it was introduced. Rules:
- Only list concepts that the **content** of this lesson actively uses or extends — not every concept the learner has ever seen.
- Do not list concepts introduced in the same lesson.
- Use the exact concept string as it appears in `introduces` of the source lesson.

### `generation_note`

A direct instruction string for the Gemini content generation model. This is the most important field — write it as if briefing a careful technical writer. Include:

1. **What is new here**: name the 1–2 most important concepts being introduced and why they matter pedagogically.
2. **What the learner already knows**: reference specific prior lessons for key prerequisites so the model doesn't re-explain them.
3. **Hard constraints**: anything the model must NOT do (e.g. "do not introduce X — that comes in LYY"). Be explicit.
4. **Tier guidance** (if needed): if Beginner and Advanced tiers must handle a concept differently, say so.
5. **Linking instruction** (if applicable): point out natural connections to past or future lessons that the model should surface.

Keep `generation_note` to 3–6 sentences. Be precise, not general — "do not assume learners know X" is better than "keep it simple".

### `cross_lesson_flag` (optional)

Include only when there is a **known pedagogical ordering tension** — a concept used in a lesson before it is formally defined in a later lesson. Value is a short snake_case string naming the anomaly. Example: `"root_user_not_yet_introduced"`. Omit the field entirely when there is no anomaly.

## `concept_index`

A flat dictionary mapping every concept string to the lesson ID where it is introduced. This is derived directly from `introduces` across all lesson entries. Every concept that appears in any `introduces` list must appear here exactly once.

## Quality Rules

1. **Completeness**: every concept listed in `outlines.yaml` key_concepts must appear in exactly one lesson's `introduces` and in `concept_index`.
2. **No forward references in `assumes`**: a lesson's `assumes` must only reference lessons with a lower lesson number, with the exception of documented `cross_lesson_flag` anomalies.
3. **`generation_note` must be actionable**: avoid vague phrases like "keep it appropriate for the tier". Say exactly what the model should and should not do.
4. **Anomaly handling**: if `outlines.yaml` contains a cross-lesson ordering issue (a concept used before its defining lesson), document it in `cross_lesson_flag` and address it explicitly in `generation_note` with tier-specific instructions.
5. **JSON validity**: output must be parseable by Python's `json.loads()`. No trailing commas. No comments. No text outside the JSON object.
