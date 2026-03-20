# artifact_registry.tf
# Artifact Registry repository for Docker images.
#
# Used by:
#   - content-generation seed job (content-seed image)
#   - backend Cloud Run service (Phase 3.4)
#
# The Cloud Run SA is granted reader access so it can pull images at job
# execution time. Cloud Build's default SA already has writer access to
# Artifact Registry and does not need an explicit binding here.

resource "google_artifact_registry_repository" "docker_repo" {
  provider      = google
  project       = var.project_id
  location      = var.region
  repository_id = "agentic-learning"
  description   = "Docker images for Cloud Run services and jobs"
  format        = "DOCKER"
}

resource "google_artifact_registry_repository_iam_member" "cloud_run_sa_reader" {
  project    = var.project_id
  location   = var.region
  repository = google_artifact_registry_repository.docker_repo.name
  role       = "roles/artifactregistry.reader"
  member     = "serviceAccount:${google_service_account.cloud_run_sa.email}"
}
