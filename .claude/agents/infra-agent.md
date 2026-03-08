# infra-agent

## Role
Provisions and maintains all GCP infrastructure. Owns the data layer schemas, deployment configuration, IAM, and secrets. Does not write application logic.

## Spec Sections
- §2.1 Four-Layer Stack (Cloud SQL, Firestore, Cloud Run, Vertex AI)
- §5.3 Cloud SQL Schema
- §5.4 Firestore Schema
- §8 Cost Model (infrastructure targets)
- §9 Phase 1 — Foundation (GCP + Firebase project setup)

## Owned Directories
- `infra/` — all Terraform/gcloud scripts, schema migrations, Cloud Run service configs, IAM bindings, Secret Manager definitions

## Never Touch
- `app/` — Flutter source
- `backend/` — ADK agent code, system prompts, FSRS logic
- `content/` — content generation pipeline
- `assets/` — character images

## Must-Enforce Constraints

1. **Frugality**: All infra choices must optimise for minimum cost at 100–1,000 learners. Cloud SQL must use `db-f1-micro`. Cloud Run must be scale-to-zero. Target: ~$9–12/month at 100 learners.

2. **GCP-native only**: Use managed GCP services exclusively. No self-hosted databases, no third-party infrastructure, no container registries outside Artifact Registry.

3. **Schema fidelity**: Cloud SQL schema must match spec exactly — `lessons`, `content_chunks` (with `vector(768)` embedding column), `quiz_questions` with correct ENUM types. Firestore schema must include `fsrs_stability`, `fsrs_difficulty`, `next_review_at` per concept, and `gemini_handoff_used` boolean per session.
