#!/bin/bash
set -euo pipefail

# Load environment variables from repo root .env
REPO_ROOT="$(git rev-parse --show-toplevel)"
set -a; source "$REPO_ROOT/.env"; set +a

echo "Enabling required GCP APIs for project: $GCP_PROJECT_ID"

gcloud services enable \
    run.googleapis.com \
    generativelanguage.googleapis.com \
    secretmanager.googleapis.com \
    artifactregistry.googleapis.com \
    storage.googleapis.com \
    firestore.googleapis.com \
    identitytoolkit.googleapis.com \
    firebase.googleapis.com \
    firebasecrashlytics.googleapis.com \
    cloudbuild.googleapis.com \
    cloudtrace.googleapis.com \
    monitoring.googleapis.com \
    logging.googleapis.com \
    --project="$GCP_PROJECT_ID"

echo "All APIs enabled successfully."
