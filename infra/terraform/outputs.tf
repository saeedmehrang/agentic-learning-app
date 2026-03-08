output "cloud_run_sa_email" {
  description = "Service account email to attach to Cloud Run services"
  value       = google_service_account.cloud_run_sa.email
}
