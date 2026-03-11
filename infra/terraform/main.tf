terraform {
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

# ---------------------------------------------------------------------------
# Service Account — Cloud Run least-privilege identity
# ---------------------------------------------------------------------------

resource "google_service_account" "cloud_run_sa" {
  account_id   = "cloud-run-app-identity"
  display_name = "Cloud Run Least Privilege SA"
}

resource "google_project_iam_member" "sql_client" {
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${google_service_account.cloud_run_sa.email}"
}

resource "google_project_iam_member" "vertex_user" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.cloud_run_sa.email}"
}

# ---------------------------------------------------------------------------
# Secret Manager — containers only; values added via CLI (never in .tf files)
# ---------------------------------------------------------------------------

resource "google_secret_manager_secret" "db_password" {
  secret_id = "DB_PASSWORD"
  replication {
    auto {}
  }
}

resource "google_secret_manager_secret" "db_connection_name" {
  secret_id = "DB_CONNECTION_NAME"
  replication {
    auto {}
  }
}

# ---------------------------------------------------------------------------
# Firestore — Native mode, co-located with Cloud Run and Cloud SQL
# ---------------------------------------------------------------------------

resource "google_firestore_database" "default" {
  project             = var.project_id
  name                = "(default)"
  location_id         = var.region
  type = "FIRESTORE_NATIVE"
}

resource "google_project_iam_member" "firestore_user" {
  project = var.project_id
  role    = "roles/datastore.user"
  member  = "serviceAccount:${google_service_account.cloud_run_sa.email}"
}

# Grant the SA read access to each secret
resource "google_secret_manager_secret_iam_member" "sa_db_password" {
  secret_id = google_secret_manager_secret.db_password.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.cloud_run_sa.email}"
}

resource "google_secret_manager_secret_iam_member" "sa_db_connection_name" {
  secret_id = google_secret_manager_secret.db_connection_name.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.cloud_run_sa.email}"
}
