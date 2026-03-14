---
name: session
description: Show the current development phase, next 3 tasks, and relevant constraints for a focused work session.
---

Read `development_roadmap.md` from the repository root. Then:

1. Find the **current phase**: the first phase section containing at least one unchecked item (`- [ ]`).
2. List the **next 3 unchecked items** in order from that phase (verbatim from the file).
3. Based on the phase domain, surface the most relevant constraints from CLAUDE.md:
   - Phase 0 / infra: frugality (≤$12/month, db-f1-micro, scale-to-zero), GCP-native only
   - Phase 1 / content: 3-tier generation rule, ≤$0.20 pipeline cost, pgvector LIMIT + course_id filter
   - Phase 2 / characters: 48 PNGs (8 × 6 emotions), naming `{character_id}_{emotion}.png`, bundle < 4 MB
   - Phase 3 / backend scaffold: frugality (≤$12/month, db-f1-micro, scale-to-zero), GCP-native only, ContextAgent model is Gemini 2.5 Flash
   - Phase 4 / agents: HelpAgent hard cap (3 turns, always outputs gemini_handoff_prompt on unresolved exit), fixed model assignments (never swap without asking), no new agents without explicit request
   - Phase 5 / Flutter: anonymous-first auth (never gate content behind sign-in, Google Sign-In offered after session 3 only), AnimatedCrossFade 300ms for character transitions, analytics event fidelity (never log gemini_handoff_prompt content — track gemini_handoff_used as boolean only)
   - Phase 6 / launch: no PII in Cloud SQL (user data in Firestore keyed by anonymous UID only), gemini_handoff_used as boolean only in analytics
4. Name the **sub-agent** to invoke for this phase's domain:
   - Phase 0: `infra-agent`
   - Phase 1: `content-agent`
   - Phase 2: `character-agent`
   - Phase 3: `backend-agent` (for agent scaffold) or `infra-agent` (for Cloud Run deployment)
   - Phase 4: `backend-agent`
   - Phase 5: `flutter-agent`
   - Phase 6: depends on the specific task — use `infra-agent` for monitoring/store config, `content-agent` for content iteration

Output this exact structure and then stop — do not begin any work until the user gives an instruction:

---
## Session Brief

**Current Phase:** [number and title]

**Next 3 Tasks:**
1. [verbatim task text from roadmap]
2. [verbatim task text from roadmap]
3. [verbatim task text from roadmap]

**Active Constraints:**
- [constraint 1]
- [constraint 2]
- [constraint 3 if applicable]

**Sub-agent:** [agent name]
---
