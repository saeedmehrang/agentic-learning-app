#!/usr/bin/env bash
# enable_pgvector.sh
# One-time post-apply step: enables the pgvector extension inside the
# learning_app database.
#
# The Cloud SQL instance has no public IP and lives inside a VPC, so the
# Cloud SQL Auth Proxy cannot reach it from a local devcontainer.
# This script uses `gcloud sql connect`, which tunnels through the Cloud SQL
# API (no direct VPC access required from the caller).
#
# Prerequisites:
#   - gcloud CLI authenticated: gcloud auth login
#   - psql installed (used under the hood by gcloud sql connect)
#   - Caller has roles/cloudsql.client on the instance
#
# Usage:
#   ./infra/scripts/enable_pgvector.sh
#
# Recommended: run from Cloud Shell (https://shell.cloud.google.com) where
# gcloud and psql are pre-installed and IAM auth is automatic.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/../../.env"

if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  set -o allexport && source "$ENV_FILE" && set +o allexport
fi

: "${GCP_PROJECT_ID:=agentic-learning-app-e13cb}"
: "${DB_INSTANCE_NAME:=learning-app-db}"
: "${DB_NAME:=learning_app}"
: "${DB_USER:=app_user}"

# Fetch DB_PASSWORD from Secret Manager (never stored in .env)
DB_PASSWORD=$(gcloud secrets versions access latest --secret="DB_PASSWORD" --project="${GCP_PROJECT_ID}" 2>/dev/null)

if [[ -z "$DB_PASSWORD" ]]; then
  echo "ERROR: Could not read DB_PASSWORD from Secret Manager. Run push_secrets.sh first." >&2
  exit 1
fi

echo "Enabling pgvector extension in ${DB_NAME} on instance ${DB_INSTANCE_NAME}..."
echo "CREATE EXTENSION IF NOT EXISTS vector;" | \
  PGPASSWORD="$DB_PASSWORD" gcloud sql connect "${DB_INSTANCE_NAME}" \
    --user="${DB_USER}" \
    --database="${DB_NAME}" \
    --project="${GCP_PROJECT_ID}" \
    --quiet

echo "pgvector enabled successfully."
