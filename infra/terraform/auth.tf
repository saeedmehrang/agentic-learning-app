# ---------------------------------------------------------------------------
# Firebase Authentication with Identity Platform
#
# Upgrades the project to Identity Platform (free up to 50k MAU).
# Enables Anonymous sign-in and Google Sign-In.
#
# Prerequisites (one-time manual steps — never passed via terraform var):
#   1. GCP Console > APIs & Services > OAuth Consent Screen
#      - Audience: External
#      - App name, support email — save.
#   2. GCP Console > APIs & Services > Credentials
#      - Create OAuth Client ID > Web application
#      - Add authorised redirect URI:
#          https://agentic-learning-app-e13cb.firebaseapp.com/__/auth/handler
#      - Copy the Client ID and Client Secret.
#   3. Store the secret manually in Secret Manager (one-time, never touches tfstate):
#        gcloud secrets create GOOGLE_OAUTH_CLIENT_SECRET \
#          --project=agentic-learning-app-e13cb \
#          --replication-policy=automatic
#        gcloud secrets versions add GOOGLE_OAUTH_CLIENT_SECRET \
#          --project=agentic-learning-app-e13cb \
#          --data-file=<(echo -n "YOUR_CLIENT_SECRET")
#   4. Run terraform apply normally — the secret is read from Secret Manager at
#      apply time via a data source and is never written into tfstate.
# ---------------------------------------------------------------------------

# Enable Identity Platform API
resource "google_project_service" "identity_platform_api" {
  provider           = google-beta
  project            = var.project_id
  service            = "identitytoolkit.googleapis.com"
  disable_on_destroy = false
}

# ---------------------------------------------------------------------------
# Identity Platform — initialise and configure sign-in providers
# ---------------------------------------------------------------------------
resource "google_identity_platform_config" "auth" {
  provider = google-beta
  project  = var.project_id

  sign_in {
    anonymous {
      enabled = true
    }
    # Prevent duplicate accounts when anonymous users upgrade to Google Sign-In
    allow_duplicate_emails = false
  }

  # The Identity Platform API re-adds multi_tenant, email, and phone_number
  # blocks with default values after every apply. Ignore them to prevent
  # perpetual drift.
  lifecycle {
    ignore_changes = [multi_tenant, sign_in[0].email, sign_in[0].phone_number]
  }

  depends_on = [google_project_service.identity_platform_api]
}

# Enable Google Sign-In
resource "google_identity_platform_default_supported_idp_config" "google_sign_in" {
  provider = google-beta
  project  = var.project_id
  enabled  = true
  idp_id   = "google.com"

  client_id     = var.google_oauth_client_id
  client_secret = data.google_secret_manager_secret_version.google_oauth_client_secret.secret_data

  depends_on = [google_identity_platform_config.auth]
}

# ---------------------------------------------------------------------------
# OAuth client secret — managed manually in Secret Manager, never via Terraform.
# Terraform only holds the shell resource so IAM can reference it.
# The secret value is never written into tfstate.
# ---------------------------------------------------------------------------
resource "google_secret_manager_secret" "google_oauth_client_secret" {
  secret_id = "GOOGLE_OAUTH_CLIENT_SECRET"
  project   = var.project_id
  replication {
    auto {}
  }

  # Terraform owns the secret shell but never the value.
  # Prevent accidental destroy/recreate from rotating the secret.
  lifecycle {
    ignore_changes = [replication]
  }
}

# Read the secret value at apply time — never stored in tfstate.
data "google_secret_manager_secret_version" "google_oauth_client_secret" {
  secret  = google_secret_manager_secret.google_oauth_client_secret.id
  project = var.project_id
}

resource "google_secret_manager_secret_iam_member" "sa_google_oauth_client_secret" {
  secret_id = google_secret_manager_secret.google_oauth_client_secret.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.cloud_run_sa.email}"
}
