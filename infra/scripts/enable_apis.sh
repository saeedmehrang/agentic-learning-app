#!/bin/bash
set -euo pipefail

PROJECT_ID="agentic-learning-app"

echo "Enabling required GCP APIs for project: $PROJECT_ID"

gcloud services enable \
    run.googleapis.com \
    sqladmin.googleapis.com \
    aiplatform.googleapis.com \
    secretmanager.googleapis.com \
    artifactregistry.googleapis.com \
    --project="$PROJECT_ID"

echo "All APIs enabled successfully."
