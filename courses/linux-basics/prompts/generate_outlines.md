# Prompt: Generate outlines.yaml

You are a curriculum designer creating the lesson outline file for a self-paced mobile Linux basics course. This file is the **source of truth** used by the AI content generation pipeline to produce lesson text and quiz questions for every lesson × tier combination.

## Task

Produce a YAML file named `outlines.yaml` containing an ordered list of all 29 lesson outline objects for the Linux Basics course (9 modules).

## Course Structure

The course covers 9 modules in this fixed order:

| Module | Title                    | Lessons         |
|--------|--------------------------|-----------------|
| 1      | Linux Foundations        | L01–L03         |
| 2      | The Command Line         | L04–L07         |
| 3      | The Filesystem           | L08–L11         |
| 4      | Working with Files       | L12–L14         |
| 5      | Users and Permissions    | L15–L17         |
| 6      | Processes and Services   | L18–L20         |
| 7      | Networking Basics        | L21–L23         |
| 8      | Package Management       | L24–L25         |
| 9      | Shell Scripting          | L26–L29         |

## Output Format

Return a valid YAML list. Each element is one lesson object with exactly these fields:

```yaml
- lesson_id: L01          # Two-digit zero-padded ID: L01–L29
  module_id: 1            # Integer module number
  title: "string"         # Concise title, max 50 chars
  learning_objectives:    # 3 objectives, each a measurable action verb + observable outcome
    - "string"
    - "string"
    - "string"
  key_concepts:           # 4–6 concepts taught for the first time in this lesson
    - "string"
    - "string"
  example_commands_or_scenarios:  # 3–5 concrete shell commands or learner scenarios
    - "string"            # commands: include inline comment explaining purpose
    - "string"            # scenarios: start with "Scenario: "
  prerequisites:          # list of lesson_id strings this lesson directly depends on; [] if none
    - L01
```

## Field Rules

**`learning_objectives`**: Use action verbs (Explain, Run, Identify, Describe, Write, Use). Each objective must be independently verifiable — a quiz question can be written against it. Exactly 3 per lesson.

**`key_concepts`**: Only concepts **introduced for the first time** in this lesson. Do not repeat concepts from earlier lessons even if they are used heavily. 4–6 per lesson.

**`example_commands_or_scenarios`**: At least 2 shell commands and at least 1 scenario per lesson, except for purely conceptual lessons (L01, L02, L03) which may have 2 scenarios and 2–3 commands. Format commands as:
```
"command [args]   # plain-English comment"
```

**`prerequisites`**: List only **direct** dependencies — the lessons whose concepts this lesson immediately builds on. Do not list transitive dependencies (if L09 depends on L08, and L08 depends on L07, then L09 only lists L08). Use `[]` for L01 (no prerequisites).

## Quality Rules

1. Objectives must be achievable on a simulated terminal or through reading — no physical hardware required.
2. Key concepts must form a coherent introduction sequence across the 29 lessons: no concept should be used before the lesson in which it is first introduced.
3. Commands must be real, standard Linux commands available on Ubuntu 22.04 LTS.
4. Scenarios must be grounded and realistic — a learner doing a real task, not a contrived example.
5. Lesson scope must be tightly bounded: do not let a lesson teach what belongs in the next lesson.
6. The YAML must be valid and parseable by Python's `yaml.safe_load()`.
7. Do not include any text outside the YAML (no markdown fences, no preamble).
