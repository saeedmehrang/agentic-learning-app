#!/bin/bash
set -euo pipefail

# Load environment variables from repo root .env
REPO_ROOT="$(git rev-parse --show-toplevel)"
set -a; source "$REPO_ROOT/.env"; set +a

echo "Enabling required GCP APIs for project: $GCP_PROJECT_ID"

gcloud services enable \
    `# Cloud Run — backend service + content-generate job` \
    run.googleapis.com \
    `# Vertex AI — Gemini generation (google.genai vertexai=True) + text-embedding-005` \
    aiplatform.googleapis.com \
    `# Secret Manager — backend config reads secrets at startup via pydantic-settings` \
    secretmanager.googleapis.com \
    `# Artifact Registry — Docker image pull for Cloud Run jobs and services` \
    artifactregistry.googleapis.com \
    `# Cloud Storage — GCS pipeline bucket (generated/, approved/, embedded/)` \
    storage.googleapis.com \
    `# Firestore — user memory and learner state` \
    firestore.googleapis.com \
    `# Identity Platform — Firebase Auth (anonymous + Google Sign-In upgrade)` \
    identitytoolkit.googleapis.com \
    `# Firebase — project initialisation, Android/iOS app registration` \
    firebase.googleapis.com \
    `# Crashlytics — Flutter crash reporting` \
    firebasecrashlytics.googleapis.com \
    `# Cloud Build — gcloud builds submit for image builds` \
    cloudbuild.googleapis.com \
    `# Cloud Trace — OpenTelemetry CloudTraceSpanExporter in backend` \
    cloudtrace.googleapis.com \
    `# Cloud Monitoring — metricWriter IAM role, OTel metrics` \
    monitoring.googleapis.com \
    `# Cloud Logging — logWriter IAM role, structured logging` \
    logging.googleapis.com \
    --project="$GCP_PROJECT_ID"

echo "All APIs enabled successfully."
