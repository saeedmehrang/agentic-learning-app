#!/usr/bin/env bash
# enable_pgvector.sh
# One-time post-apply step: enables the pgvector extension inside the
# learning_app database via Cloud SQL Auth Proxy.
#
# Prerequisites:
#   - cloud-sql-proxy installed (brew install cloud-sql-proxy or gcloud components install cloud-sql-proxy)
#   - psql installed
#   - Authenticated: gcloud auth application-default login
#   - .env file present with DB_CONNECTION_NAME and DB_PASSWORD set
#
# Usage:
#   ./infra/scripts/enable_pgvector.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/../../.env"

if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  set -o allexport && source "$ENV_FILE" && set +o allexport
fi

: "${DB_CONNECTION_NAME:?DB_CONNECTION_NAME must be set in .env}"
: "${DB_NAME:=learning_app}"
: "${DB_USER:=app_user}"
: "${DB_PORT:=5432}"

# Fetch DB_PASSWORD from Secret Manager (never stored in .env)
DB_PASSWORD=$(gcloud secrets versions access latest --secret="DB_PASSWORD" --project="${GCP_PROJECT_ID:-agentic-learning-app-e13cb}" 2>/dev/null)

if [[ -z "$DB_PASSWORD" ]]; then
  echo "ERROR: Could not read DB_PASSWORD from Secret Manager. Run push_secrets.sh first." >&2
  exit 1
fi

echo "Starting Cloud SQL Auth Proxy for ${DB_CONNECTION_NAME}..."
cloud-sql-proxy "${DB_CONNECTION_NAME}" --port "${DB_PORT}" &
PROXY_PID=$!
trap 'kill $PROXY_PID 2>/dev/null; exit' INT TERM EXIT

# Wait for proxy to be ready
sleep 3

echo "Enabling pgvector extension in ${DB_NAME}..."
PGPASSWORD="$DB_PASSWORD" psql \
  "host=127.0.0.1 port=${DB_PORT} dbname=${DB_NAME} user=${DB_USER} sslmode=disable" \
  -c "CREATE EXTENSION IF NOT EXISTS vector;"

echo "pgvector enabled successfully."
