# content-agent

## Role
Generates and loads all course content into Cloud SQL. Runs the one-time authoring pipeline: Gemini lesson generation, quiz question generation, embedding, and database ingestion.

## Spec Sections
- §5.1 Linux Basics Course Structure (25 lessons, 8 modules)
- §5.2 Content Generation Pipeline
- §5.3 Cloud SQL Schema (write target)

## Owned Directories
- `content/` — pipeline scripts, lesson outlines, prompt templates, generation logs, approved content exports

## Never Touch
- `app/` — Flutter source
- `backend/` — ADK agent code
- `assets/` — character images
- `infra/` — schema DDL or GCP config (read schema; do not modify it)

## Must-Enforce Constraints

1. **3-tier generation**: Every lesson must produce exactly 3 difficulty variants — `beginner` (heavy analogies), `intermediate` (practical focus), `advanced` (concise, system-level). Never load single-tier content.

2. **Quiz coverage**: Each lesson × tier must have up to 12 quiz questions covering all 4 formats: `mc`, `tf`, `fill`, `command`. Include `explanation` for every question.

3. **Frugality**: Use `gemini-2.5-flash` for generation. Full 25-lesson pipeline must cost ≤$0.20. Use `text-embedding-004` (768-dim) for embeddings. Never call embedding APIs redundantly — embed once per chunk and store.
