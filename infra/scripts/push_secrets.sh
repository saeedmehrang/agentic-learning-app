#!/bin/bash
# push_secrets.sh — push secret values to Secret Manager without touching disk.
#
# Manages Google OAuth client secret only.
# DB_PASSWORD and DB_CONNECTION_NAME have been removed — Cloud SQL is decommissioned.
#
# Run after `terraform apply` if GOOGLE_OAUTH_CLIENT_SECRET needs to be pushed.
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
set -a; source "$REPO_ROOT/.env"; set +a   # need GCP_PROJECT_ID only

# ── helpers ──────────────────────────────────────────────────────────────────

secret_has_version() {
  local name="$1"
  gcloud secrets versions list "$name" \
    --project="$GCP_PROJECT_ID" \
    --filter="state=ENABLED" \
    --format="value(name)" \
    --limit=1 2>/dev/null | grep -q .
}

push_from_stdin() {
  local name="$1"
  gcloud secrets versions add "$name" \
    --project="$GCP_PROJECT_ID" \
    --data-file=-
}

# ── GOOGLE_OAUTH_CLIENT_SECRET ────────────────────────────────────────────────

echo ""
echo "=== GOOGLE_OAUTH_CLIENT_SECRET ==="

if secret_has_version "GOOGLE_OAUTH_CLIENT_SECRET"; then
  echo "Already has an enabled version — skipping."
  echo "To update, disable all existing versions first."
else
  echo "Enter your Google OAuth client secret (input hidden):"
  read -rs oauth_secret
  if [ -z "$oauth_secret" ]; then
    echo "SKIP: GOOGLE_OAUTH_CLIENT_SECRET — re-run after obtaining OAuth credentials"
  else
    printf '%s' "$oauth_secret" | push_from_stdin "GOOGLE_OAUTH_CLIENT_SECRET"
    echo "  pushed: GOOGLE_OAUTH_CLIENT_SECRET"
    unset oauth_secret
  fi
fi

echo ""
echo "Done. Verify with:"
echo "  gcloud secrets versions list GOOGLE_OAUTH_CLIENT_SECRET --project=$GCP_PROJECT_ID"
