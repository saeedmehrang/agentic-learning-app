output "cloud_run_sa_email" {
  description = "Service account email to attach to Cloud Run services"
  value       = google_service_account.cloud_run_sa.email
}

output "firestore_database_name" {
  description = "Firestore database name"
  value       = google_firestore_database.default.name
}

output "firebase_android_app_id" {
  description = "Firebase Android app ID"
  value       = google_firebase_android_app.default.app_id
}

output "firebase_ios_app_id" {
  description = "Firebase iOS app ID"
  value       = google_firebase_apple_app.default.app_id
}
