#!/usr/bin/env bash
# Programmatic teardown of all GCP resources for the agentic-learning-app.
#
# What this script does:
#   1. Pre-flight checks  — verifies required tools and credentials
#   2. Cloud Run cleanup  — deletes any deployed Cloud Run services
#   3. Terraform destroy  — removes all Terraform-managed resources
#   4. Secret cleanup     — force-deletes Secret Manager secrets (versions linger)
#   5. State file cleanup — optionally removes local terraform.tfstate
#
# Usage:
#   ./infra/scripts/teardown.sh            # interactive confirmation
#   ./infra/scripts/teardown.sh --yes      # skip confirmation (CI use only)
#
# WARNING: This is irreversible. All data in Firestore will be lost.
set -euo pipefail

# ── Colour helpers ────────────────────────────────────────────────────────────
RED='\033[0;31m'
YELLOW='\033[1;33m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
BOLD='\033[1m'
RESET='\033[0m'

info()    { echo -e "${CYAN}[INFO]${RESET}  $*"; }
warn()    { echo -e "${YELLOW}[WARN]${RESET}  $*"; }
success() { echo -e "${GREEN}[OK]${RESET}    $*"; }
fatal()   { echo -e "${RED}[FATAL]${RESET} $*" >&2; exit 1; }

# ── Resolve repo root & load .env ────────────────────────────────────────────
REPO_ROOT="$(git rev-parse --show-toplevel)"
ENV_FILE="$REPO_ROOT/.env"

if [[ ! -f "$ENV_FILE" ]]; then
  fatal ".env not found at $ENV_FILE — cannot determine project configuration."
fi
set -a; source "$ENV_FILE"; set +a

# Required variables from .env
: "${GCP_PROJECT_ID:?GCP_PROJECT_ID must be set in .env}"
: "${GCP_REGION:?GCP_REGION must be set in .env}"

TF_DIR="$REPO_ROOT/infra/terraform"
TF_STATE="$TF_DIR/terraform.tfstate"
TF_WRAPPER="$REPO_ROOT/infra/scripts/tf.sh"

# ── Parse flags ──────────────────────────────────────────────────────────────
AUTO_YES=false
for arg in "$@"; do
  [[ "$arg" == "--yes" ]] && AUTO_YES=true
done

# ── Pre-flight checks ─────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}━━━  Pre-flight checks  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"

command -v terraform >/dev/null 2>&1 || fatal "'terraform' CLI not found in PATH."
success "terraform $(terraform version -json 2>/dev/null | python3 -c 'import sys,json; print(json.load(sys.stdin)["terraform_version"])' 2>/dev/null || terraform --version | head -1 | awk '{print $2}')"

command -v gcloud >/dev/null 2>&1 || fatal "'gcloud' CLI not found in PATH."
success "gcloud $(gcloud --version 2>/dev/null | head -1)"

# Check gcloud authentication
ACTIVE_ACCOUNT="$(gcloud auth list --filter=status:ACTIVE --format='value(account)' 2>/dev/null | head -1)"
if [[ -z "$ACTIVE_ACCOUNT" ]]; then
  fatal "No active gcloud account. Run: gcloud auth login"
fi
success "Authenticated as: $ACTIVE_ACCOUNT"

# Verify gcloud is pointing at the right project
CURRENT_PROJECT="$(gcloud config get-value project 2>/dev/null || true)"
if [[ "$CURRENT_PROJECT" != "$GCP_PROJECT_ID" ]]; then
  warn "gcloud default project ('$CURRENT_PROJECT') differs from .env ('$GCP_PROJECT_ID')."
  warn "The script will use $GCP_PROJECT_ID from .env for all gcloud commands."
fi

if [[ ! -f "$TF_STATE" ]]; then
  warn "No terraform.tfstate found at $TF_STATE"
  warn "Either resources were already destroyed, or state was manually removed."
  warn "Skipping Terraform destroy step."
  TF_HAS_STATE=false
else
  RESOURCE_COUNT="$(python3 -c "import json,sys; d=json.load(open('$TF_STATE')); print(len([r for r in d.get('resources',[]) if r.get('mode')=='managed']))" 2>/dev/null || echo "unknown")"
  success "Terraform state found — $RESOURCE_COUNT managed resources tracked."
  TF_HAS_STATE=true
fi

# ── Warning banner ────────────────────────────────────────────────────────────
echo ""
echo -e "${RED}${BOLD}━━━  WARNING: IRREVERSIBLE DESTRUCTION  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo -e "${RED}This will permanently delete the following GCP resources:${RESET}"
echo ""
echo "  Project : $GCP_PROJECT_ID"
echo "  Region  : $GCP_REGION"
echo ""
echo "  Resources to be destroyed:"
echo "   • Firestore database  : (default)                    (ALL DOCUMENTS LOST)"
echo "   • GCS pipeline bucket : agentic-learning-pipeline    (ALL OBJECTS LOST)"
echo "   • Secret Manager      : GOOGLE_OAUTH_CLIENT_SECRET"
echo "   • Service Account     : cloud-run-app-identity + all IAM bindings"
echo "   • Artifact Registry   : agentic-learning (all Docker images)"
echo "   • Firebase apps       : Android + iOS registrations"
echo "   • Identity Platform   : Anonymous + Google Sign-In config"
echo "   • Cloud Run services  : all services in $GCP_REGION (if deployed)"
echo ""
echo -e "${YELLOW}NOTE: GCP APIs will NOT be disabled (no cost, safe to leave enabled).${RESET}"
echo -e "${RED}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo ""

# ── Confirmation gate ─────────────────────────────────────────────────────────
if [[ "$AUTO_YES" == false ]]; then
  echo -e "Type ${BOLD}yes${RESET} (exactly) and press Enter to proceed, or anything else to abort:"
  read -r CONFIRMATION
  if [[ "$CONFIRMATION" != "yes" ]]; then
    echo "Aborted. No changes made."
    exit 0
  fi
fi

echo ""
echo -e "${BOLD}━━━  Step 1 / 4 — Cloud Run services  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"

# List all Cloud Run services in the project/region and delete them
CLOUD_RUN_SERVICES="$(gcloud run services list \
  --project="$GCP_PROJECT_ID" \
  --region="$GCP_REGION" \
  --format='value(metadata.name)' 2>/dev/null || true)"

if [[ -z "$CLOUD_RUN_SERVICES" ]]; then
  info "No Cloud Run services found in $GCP_REGION — nothing to delete."
else
  while IFS= read -r SERVICE; do
    [[ -z "$SERVICE" ]] && continue
    info "Deleting Cloud Run service: $SERVICE"
    gcloud run services delete "$SERVICE" \
      --project="$GCP_PROJECT_ID" \
      --region="$GCP_REGION" \
      --quiet
    success "Deleted: $SERVICE"
  done <<< "$CLOUD_RUN_SERVICES"
fi

echo ""
echo -e "${BOLD}━━━  Step 2 / 4 — Terraform destroy  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"

if [[ "$TF_HAS_STATE" == true ]]; then
  info "Running terraform destroy (this can take several minutes)..."
  echo ""
  # tf.sh sources .env and cd's into the terraform directory, then runs terraform "$@"
  "$TF_WRAPPER" destroy -auto-approve
  echo ""
  success "Terraform destroy complete."
else
  warn "Skipping terraform destroy — no state file found."
fi

echo ""
echo -e "${BOLD}━━━  Step 3 / 4 — Secret Manager cleanup  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
# Terraform destroys the secret resource but secret versions can linger in a
# "DESTROY_SCHEDULED" state for up to 24 hours. Force-deleting ensures they're gone.

SECRETS=("GOOGLE_OAUTH_CLIENT_SECRET")

for SECRET in "${SECRETS[@]}"; do
  if gcloud secrets describe "$SECRET" --project="$GCP_PROJECT_ID" >/dev/null 2>&1; then
    info "Force-deleting secret: $SECRET"
    gcloud secrets delete "$SECRET" \
      --project="$GCP_PROJECT_ID" \
      --quiet
    success "Deleted: $SECRET"
  else
    info "Secret already gone: $SECRET"
  fi
done

echo ""
echo -e "${BOLD}━━━  Step 4 / 4 — Local state file cleanup  ━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"

STATE_FILES=("$TF_DIR/terraform.tfstate" "$TF_DIR/terraform.tfstate.backup")
FOUND_STATE_FILES=()

for F in "${STATE_FILES[@]}"; do
  [[ -f "$F" ]] && FOUND_STATE_FILES+=("$F")
done

if [[ ${#FOUND_STATE_FILES[@]} -eq 0 ]]; then
  info "No local state files to clean up."
else
  echo "Found local state files:"
  for F in "${FOUND_STATE_FILES[@]}"; do
    echo "  $F"
  done
  echo ""
  if [[ "$AUTO_YES" == false ]]; then
    echo -e "Delete these files? They are safe to remove after a successful destroy. [y/N]:"
    read -r DELETE_STATE
  else
    DELETE_STATE="y"
  fi

  if [[ "$DELETE_STATE" =~ ^[Yy]$ ]]; then
    for F in "${FOUND_STATE_FILES[@]}"; do
      rm -f "$F"
      success "Deleted: $F"
    done
  else
    info "State files kept. You can delete them manually later."
  fi
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}${BOLD}━━━  Teardown complete  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo ""
echo "Verify nothing was missed by running these checks:"
echo ""
echo "  # Should return empty:"
echo "  gcloud sql instances list --project=$GCP_PROJECT_ID"
echo "  gcloud secrets list --project=$GCP_PROJECT_ID"
echo "  gcloud iam service-accounts list --project=$GCP_PROJECT_ID --filter='email:cloud-run-app-identity*'"
echo "  gcloud artifacts repositories list --location=$GCP_REGION --project=$GCP_PROJECT_ID"
echo "  gcloud run services list --project=$GCP_PROJECT_ID --region=$GCP_REGION"
echo ""
echo -e "${YELLOW}Manual checks (console only):${RESET}"
echo "  • Firestore: https://console.firebase.google.com/project/$GCP_PROJECT_ID/firestore"
echo "  • Billing:   https://console.cloud.google.com/billing — confirm no unexpected charges"
echo ""
echo -e "${YELLOW}NOTE: If you want to re-provision later, run:${RESET}"
echo "  ./infra/scripts/enable_apis.sh"
echo "  ./infra/scripts/tf.sh init"
echo "  ./infra/scripts/tf.sh apply"
echo "  ./infra/scripts/push_secrets.sh"
echo ""
