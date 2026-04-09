# ---------------------------------------------------------------------------
# Firebase Authentication with Identity Platform
#
# Upgrades the project to Identity Platform (free up to 50k MAU).
# Enables Anonymous sign-in and Google Sign-In.
#
# Prerequisites (one-time manual steps):
#   1. GCP Console > APIs & Services > OAuth Consent Screen
#      - Audience: External
#      - App name, support email — save.
#   2. GCP Console > APIs & Services > Credentials
#      - Create OAuth Client ID > Web application
#      - Add authorised redirect URI:
#          https://agentic-learning-app-e13cb.firebaseapp.com/__/auth/handler
#      - Copy the Client ID and Client Secret.
#   3. First apply — pass the secret via inline prompt (never stored on disk,
#      never appears in shell history):
#        terraform apply -var="google_oauth_client_secret=$(read -rs s && echo $s)"
#
#   All subsequent applies — read from Secret Manager:
#        export TF_VAR_google_oauth_client_secret=$(gcloud secrets versions access latest \
#          --secret=GOOGLE_OAUTH_CLIENT_SECRET)
#        terraform apply
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
  client_secret = var.google_oauth_client_secret

  depends_on = [google_identity_platform_config.auth]
}

# ---------------------------------------------------------------------------
# Store the OAuth client secret in Secret Manager (never hardcoded in .tf)
# ---------------------------------------------------------------------------
resource "google_secret_manager_secret" "google_oauth_client_secret" {
  secret_id = "GOOGLE_OAUTH_CLIENT_SECRET"
  project   = var.project_id
  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "google_oauth_client_secret_value" {
  secret      = google_secret_manager_secret.google_oauth_client_secret.id
  secret_data = var.google_oauth_client_secret
}

resource "google_secret_manager_secret_iam_member" "sa_google_oauth_client_secret" {
  secret_id = google_secret_manager_secret.google_oauth_client_secret.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.cloud_run_sa.email}"
}
