# flutter-agent

## Role
Builds and maintains the Flutter mobile app (iOS + Android). Owns all screens, state management, character widget, Firebase integrations, and the Gemini referral card deep-link.

## Spec Sections
- §6.1 App Architecture (Firebase Auth, Riverpod, Hive, AnimatedCrossFade, Analytics)
- §6.2 Screen Flow (7 screens)
- §6.3 Character Widget Behaviour
- §7 Authentication & Learner Identity
- §3.3 Gemini App Handoff (referral card + url_launcher)
- §3.4 Character Emotion States (consumer of agent JSON contract)

## Owned Directories
- `app/` — all Flutter source, `pubspec.yaml`, Firebase config files

## Never Touch
- `backend/` — ADK agent code or system prompts
- `infra/` — GCP or Firebase project configuration
- `content/` — content generation pipeline
- `assets/characters/` — do not edit PNGs; only declare them in `pubspec.yaml`

## Must-Enforce Constraints

1. **Anonymous-first auth**: App must sign in anonymously on first launch with no prompt. Google Sign-In upgrade must be offered non-blockingly after session 3 only. Never gate content or progress behind sign-in. Anonymous UID must be preserved and linked on upgrade.

2. **Character widget contract**: The character widget must accept `emotion_state` and `module_character_id` as inputs and use `AnimatedCrossFade` (300ms) for all emotion transitions. Position: 80×80dp top-right overlay during lesson/quiz; scale to 120×120dp centered during help. Asset loading must be local bundle only — no network image calls for characters.

3. **Analytics event fidelity**: Fire exactly these named events via Firebase Analytics: `session_start`, `lesson_complete`, `quiz_answer`, `help_triggered`, `gemini_handoff_tapped`, `session_complete`. Never log the content of the `gemini_handoff_prompt` in any analytics event — track `gemini_handoff_used` as a boolean only.
