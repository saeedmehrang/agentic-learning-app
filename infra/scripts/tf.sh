#!/bin/bash
# Wrapper for terraform commands that injects variables from repo root .env
# Usage: ./infra/scripts/tf.sh [init|plan|apply|destroy|...]
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
set -a; source "$REPO_ROOT/.env"; set +a

export TF_VAR_project_id="$GCP_PROJECT_ID"
export TF_VAR_region="$GCP_REGION"
export TF_VAR_google_oauth_client_id="$GOOGLE_OAUTH_CLIENT_ID"
export TF_VAR_google_oauth_client_secret="$GOOGLE_OAUTH_CLIENT_SECRET"

cd "$REPO_ROOT/infra/terraform"
exec terraform "$@"
