terraform {
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
    google-beta = {
      source  = "hashicorp/google-beta"
      version = "~> 6.0"
    }
    local = {
      source  = "hashicorp/local"
      version = "~> 2.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

provider "google-beta" {
  project               = var.project_id
  region                = var.region
  user_project_override = true
  billing_project       = var.project_id
}

# ---------------------------------------------------------------------------
# APIs — core GCP services required by Cloud Run jobs and agents
# ---------------------------------------------------------------------------

resource "google_project_service" "vertex_ai_api" {
  service            = "aiplatform.googleapis.com"
  disable_on_destroy = false
}

# ---------------------------------------------------------------------------
# Service Account — Cloud Run least-privilege identity
# ---------------------------------------------------------------------------

resource "google_service_account" "cloud_run_sa" {
  account_id   = "cloud-run-app-identity"
  display_name = "Cloud Run Least Privilege SA"
}

# ---------------------------------------------------------------------------
# Secret Manager — containers only; values added via CLI (never in .tf files)
# ---------------------------------------------------------------------------

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

resource "google_project_iam_member" "cloudtrace_agent" {
  project = var.project_id
  role    = "roles/cloudtrace.agent"
  member  = "serviceAccount:${google_service_account.cloud_run_sa.email}"
}

resource "google_project_iam_member" "log_writer" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.cloud_run_sa.email}"
}

resource "google_project_iam_member" "metric_writer" {
  project = var.project_id
  role    = "roles/monitoring.metricWriter"
  member  = "serviceAccount:${google_service_account.cloud_run_sa.email}"
}

# ---------------------------------------------------------------------------
# GCS pipeline bucket — stores all intermediate content generation outputs:
# generated/, reviewed/, approved/, embedded/, pipeline_log.json
# Survives Cloud Run job completion/crashes; enables --resume across invocations.
# ---------------------------------------------------------------------------

resource "google_storage_bucket" "pipeline" {
  name                        = "agentic-learning-pipeline"
  location                    = var.region
  uniform_bucket_level_access = true
  force_destroy               = true # dev: allow bucket deletion without emptying first

  lifecycle_rule {
    condition {
      age = 90 # days — auto-delete old pipeline artifacts
    }
    action {
      type = "Delete"
    }
  }
}

resource "google_storage_bucket_iam_member" "pipeline_sa_admin" {
  bucket = google_storage_bucket.pipeline.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.cloud_run_sa.email}"
}

output "pipeline_bucket_name" {
  description = "GCS bucket for content generation pipeline outputs"
  value       = google_storage_bucket.pipeline.name
}
