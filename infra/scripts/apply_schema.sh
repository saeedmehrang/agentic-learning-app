#!/usr/bin/env bash
# apply_schema.sh
# Applies a SQL migration file to the learning_app Cloud SQL database.
#
# Uses `gcloud sql connect` which starts its own Cloud SQL Auth Proxy internally.
#
# Prerequisites:
#   - gcloud CLI authenticated: gcloud auth login
#   - psql installed (used under the hood by gcloud sql connect)
#   - Caller has roles/cloudsql.client on the instance
#   - DB_PASSWORD stored in Secret Manager (run push_secrets.sh first)
#
# Usage:
#   ./infra/scripts/apply_schema.sh [--file path/to/migration.sql]
#
# Defaults to infra/sql/001_create_schema.sql if --file is not specified.
#
# NOTE: This script starts its own proxy internally. If you already have
# cloud-sql-proxy running on port 5432, use psql directly instead to avoid conflicts:
#
#   SECRET=$(gcloud secrets versions access latest --secret="DB_PASSWORD" --project=agentic-learning-app-e13cb)
#   PGPASSWORD="$SECRET" psql --host=127.0.0.1 --port=5432 \
#     --username=app_user --dbname=learning_app --file=infra/sql/001_create_schema.sql

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
ENV_FILE="${REPO_ROOT}/.env"

if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  set -o allexport && source "$ENV_FILE" && set +o allexport
fi

: "${GCP_PROJECT_ID:=agentic-learning-app-e13cb}"
: "${DB_INSTANCE_NAME:=learning-app-db}"
: "${DB_NAME:=learning_app}"
: "${DB_USER:=app_user}"

# Default SQL file
SQL_FILE="${REPO_ROOT}/infra/sql/001_create_schema.sql"

# Parse optional --file argument
while [[ $# -gt 0 ]]; do
  case "$1" in
    --file)
      SQL_FILE="$2"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1" >&2
      echo "Usage: $0 [--file path/to/migration.sql]" >&2
      exit 1
      ;;
  esac
done

if [[ ! -f "$SQL_FILE" ]]; then
  echo "ERROR: SQL file not found: ${SQL_FILE}" >&2
  exit 1
fi

# Fetch DB_PASSWORD from Secret Manager (never stored in .env)
DB_PASSWORD=$(gcloud secrets versions access latest --secret="DB_PASSWORD" --project="${GCP_PROJECT_ID}" 2>/dev/null)

if [[ -z "$DB_PASSWORD" ]]; then
  echo "ERROR: Could not read DB_PASSWORD from Secret Manager. Run push_secrets.sh first." >&2
  exit 1
fi

echo "Applying schema from: ${SQL_FILE}"
echo "Target: ${DB_NAME} on instance ${DB_INSTANCE_NAME} (project ${GCP_PROJECT_ID})"

PGPASSWORD="$DB_PASSWORD" gcloud sql connect "${DB_INSTANCE_NAME}" \
  --user="${DB_USER}" \
  --database="${DB_NAME}" \
  --project="${GCP_PROJECT_ID}" \
  --quiet < "${SQL_FILE}"

echo "Schema applied successfully."
