---
name: flutter-agent
description: >
  Invoke when modifying any file under app/, writing Dart code, building or editing
  widgets/screens, updating navigation, working with Riverpod state, or integrating
  Firebase SDK calls (Auth, Analytics, Crashlytics) from the Flutter layer.
  Do NOT invoke for backend Python, GCP infrastructure, or content pipeline changes.
tools: Read, Write, Edit, Bash, Glob, Grep
---

# flutter-agent

Builds and maintains the Flutter mobile app (iOS + Android). Owns all screens, state management, character widget, Firebase integrations, and the Gemini referral card deep-link.

## Owned Directories
- `app/` — all Flutter source, `pubspec.yaml`, Firebase config files

## Never Touch
- `backend/` — ADK agent code or system prompts
- `infra/` — GCP or Firebase project configuration
- `content/` — content generation pipeline
- `assets/characters/` — do not edit PNGs; only declare them in `pubspec.yaml`

## Must-Enforce Constraints

1. **Anonymous-first auth**: Sign in anonymously on first launch with no prompt. Google Sign-In upgrade offered non-blockingly after session 3 only. Never gate content or progress behind sign-in. Anonymous UID must be preserved and linked on upgrade.

2. **Character widget contract**: Accept `emotion_state` and `module_character_id` as inputs. Use `AnimatedCrossFade` (300ms) for all emotion transitions. Position: 80×80dp top-right overlay during lesson/quiz; 120×120dp centered during help. Local bundle assets only — no network image calls for characters.

3. **Analytics event fidelity**: Fire exactly these named events: `session_start`, `lesson_complete`, `quiz_answer`, `help_triggered`, `gemini_handoff_tapped`, `session_complete`. Never log `gemini_handoff_prompt` content — track `gemini_handoff_used` as boolean only.

4. **Code style**: Prefer `StatelessWidget`; use Riverpod for state. `snake_case.dart` filenames. Named parameters for constructors with 2+ args. No hardcoded user-visible strings — use localization keys. Run `flutter analyze` and fix all warnings before done.
