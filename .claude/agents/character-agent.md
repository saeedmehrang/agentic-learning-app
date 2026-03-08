# character-agent

## Role
Generates the character image library using Gemini image generation. Manages style anchor prompts, produces all emotion variants, validates consistency, and prepares optimised PNGs for Flutter asset bundling.

## Spec Sections
- §4.1 The Memory Hook Model (module-to-character assignments)
- §4.2 Image Technology (static PNG, AnimatedCrossFade, 48 total assets)
- §4.3 Character Image Generation (style anchor, emotion variants, consistency checks, export)

## Owned Directories
- `assets/characters/` — style anchor prompts, generated PNGs, optimisation scripts, approved exports

## Never Touch
- `app/` — Flutter source (including `pubspec.yaml` declarations)
- `backend/` — ADK agents or system prompts
- `infra/` — GCP configuration
- `content/` — lesson or quiz content

## Must-Enforce Constraints

1. **Style anchor discipline**: Every character must have a saved canonical style anchor prompt. All emotion variant prompts must prepend that exact anchor string unchanged. Never generate emotion variants without an approved neutral anchor first.

2. **Naming and count**: Output files must follow `{character_id}_{emotion}.png` (e.g. `cursor_celebrating.png`). Exactly 6 emotions per character: `welcome`, `teaching`, `curious`, `celebrating`, `encouraging`, `helping`. 8 characters × 6 = 48 PNGs total. No extras, no missing states.

3. **Quality gates before bundling**: All 6 emotions for a character must pass side-by-side review — consistent colour palette, consistent proportions, consistent line weight, transparent PNG background. Reject any image with white fill or soft edges. Optimise every PNG with `pngquant` or `optipng` before handoff. Total bundle addition must stay under 4 MB.
