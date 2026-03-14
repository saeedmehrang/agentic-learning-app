# ---------------------------------------------------------------------------
# Firebase — project initialization, app registration, Crashlytics APIs
#
# NOTE: The GCP project itself was created manually via the Firebase Console.
# On first apply you must import the existing Firebase project:
#
#   terraform import google_firebase_project.default projects/YOUR_PROJECT_ID
#
# After that, terraform plan should show no changes for this resource.
# ---------------------------------------------------------------------------

# Enable Firebase Management API
resource "google_project_service" "firebase_api" {
  provider           = google-beta
  project            = var.project_id
  service            = "firebase.googleapis.com"
  disable_on_destroy = false
}

# Enable Firebase Crashlytics API
resource "google_project_service" "crashlytics_api" {
  provider           = google-beta
  project            = var.project_id
  service            = "firebasecrashlytics.googleapis.com"
  disable_on_destroy = false
}

# ---------------------------------------------------------------------------
# Attach Firebase to the existing GCP project.
# Import this resource if the project was already Firebase-ified via console:
#   terraform import google_firebase_project.default projects/<project_id>
# ---------------------------------------------------------------------------
resource "google_firebase_project" "default" {
  provider = google-beta
  project  = var.project_id

  depends_on = [
    google_project_service.firebase_api,
    google_project_service.crashlytics_api,
  ]
}

# ---------------------------------------------------------------------------
# Register the Android app
# ---------------------------------------------------------------------------
resource "google_firebase_android_app" "default" {
  provider     = google-beta
  project      = var.project_id
  display_name = var.firebase_android_display_name
  package_name = var.android_package_name

  depends_on = [google_firebase_project.default]
}

# Download the google-services.json config for the Android app
data "google_firebase_android_app_config" "default" {
  provider = google-beta
  app_id   = google_firebase_android_app.default.app_id
}

# Write google-services.json to the Flutter app directory
resource "local_file" "google_services_json" {
  content  = base64decode(data.google_firebase_android_app_config.default.config_file_contents)
  filename = "${path.module}/../../app/android/app/google-services.json"
}

# ---------------------------------------------------------------------------
# Register the iOS/Apple app
# ---------------------------------------------------------------------------
resource "google_firebase_apple_app" "default" {
  provider     = google-beta
  project      = var.project_id
  display_name = var.firebase_ios_display_name
  bundle_id    = var.ios_bundle_id

  depends_on = [google_firebase_project.default]
}

# Download the GoogleService-Info.plist config for the iOS app
data "google_firebase_apple_app_config" "default" {
  provider = google-beta
  app_id   = google_firebase_apple_app.default.app_id
}

# Write GoogleService-Info.plist to the Flutter app directory
resource "local_file" "google_service_info_plist" {
  content  = base64decode(data.google_firebase_apple_app_config.default.config_file_contents)
  filename = "${path.module}/../../app/ios/Runner/GoogleService-Info.plist"
}
