#!/bin/bash
# push_secrets.sh — push secret values to Secret Manager without touching disk.
#
# DB_PASSWORD  : managed by Terraform (random_password resource in cloudsql.tf).
#                This script skips it if a version already exists (Terraform will
#                have written one). Only relevant if you need to manually rotate.
# DB_CONNECTION_NAME : pushed here after Cloud SQL is provisioned via Terraform.
#                      The value is read from terraform output cloud_sql_connection_name,
#                      or can be entered manually.
#
# Run after `tf.sh apply` for Phase 0.3 to push DB_CONNECTION_NAME.
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
set -a; source "$REPO_ROOT/.env"; set +a   # need GCP_PROJECT_ID only

# ── helpers ──────────────────────────────────────────────────────────────────

# Check whether a secret already has at least one version in Secret Manager.
secret_has_version() {
  local name="$1"
  gcloud secrets versions list "$name" \
    --project="$GCP_PROJECT_ID" \
    --filter="state=ENABLED" \
    --format="value(name)" \
    --limit=1 2>/dev/null | grep -q .
}

# Push data arriving on stdin to a Secret Manager secret.
push_from_stdin() {
  local name="$1"
  gcloud secrets versions add "$name" \
    --project="$GCP_PROJECT_ID" \
    --data-file=-
}

# ── DB_PASSWORD ───────────────────────────────────────────────────────────────
# Generated with /dev/urandom — never stored, never echoed, piped straight
# to gcloud. The value is only ever in the kernel pipe buffer.

echo ""
echo "=== DB_PASSWORD ==="

if secret_has_version "DB_PASSWORD"; then
  echo "Already has an enabled version — skipping."
  echo "To rotate, disable all existing versions first:"
  echo "  gcloud secrets versions list DB_PASSWORD --project=$GCP_PROJECT_ID"
else
  # LC_ALL=C restricts the character class to 7-bit ASCII so tr works correctly
  # across all locales. 48 bytes of urandom → ~64 base64 chars; we trim to 32.
  LC_ALL=C tr -dc 'A-Za-z0-9!#%+:=@^_~' </dev/urandom \
    | head -c 32 \
    | push_from_stdin "DB_PASSWORD"
  echo "  pushed: DB_PASSWORD (auto-generated, 32 chars)"
  echo "  You do not need to know this value — the app reads it from Secret Manager."
fi

# ── DB_CONNECTION_NAME ────────────────────────────────────────────────────────
# Prompted interactively. The value lives only in $conn_name for the duration
# of this script and is never written to disk or history.

echo ""
echo "=== DB_CONNECTION_NAME ==="

if secret_has_version "DB_CONNECTION_NAME"; then
  echo "Already has an enabled version — skipping."
  echo "To update, disable all existing versions first."
else
  # Try to read from terraform output first
  TF_DIR="$(git rev-parse --show-toplevel)/infra/terraform"
  conn_name=$(cd "$TF_DIR" && terraform output -raw cloud_sql_connection_name 2>/dev/null || true)

  if [ -z "$conn_name" ]; then
    echo "Format: project-id:region:instance-name"
    echo "Leave blank to skip (re-run this script after Cloud SQL is provisioned)."
    read -rp "DB_CONNECTION_NAME: " conn_name
  else
    echo "Auto-detected from terraform output: $conn_name"
  fi

  if [ -z "$conn_name" ]; then
    echo "SKIP: DB_CONNECTION_NAME — re-run after Phase 0.3 Cloud SQL provisioning"
  else
    printf '%s' "$conn_name" | push_from_stdin "DB_CONNECTION_NAME"
    echo "  pushed: DB_CONNECTION_NAME"
    unset conn_name
  fi
fi

echo ""
echo "Done. Verify with:"
echo "  gcloud secrets versions list DB_PASSWORD --project=$GCP_PROJECT_ID"
echo "  gcloud secrets versions list DB_CONNECTION_NAME --project=$GCP_PROJECT_ID"
