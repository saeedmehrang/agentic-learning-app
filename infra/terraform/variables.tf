variable "project_id" {
  description = "GCP project ID"
  type        = string
}

variable "region" {
  description = "GCP region for all resources"
  type        = string
}

variable "firebase_android_display_name" {
  description = "Display name for the Firebase Android app"
  type        = string
  default     = "Agentic Learning Android"
}

variable "android_package_name" {
  description = "Android app package name"
  type        = string
  default     = "com.agenticlearning.app"
}

variable "firebase_ios_display_name" {
  description = "Display name for the Firebase iOS app"
  type        = string
  default     = "Agentic Learning iOS"
}

variable "ios_bundle_id" {
  description = "iOS app bundle ID"
  type        = string
  default     = "com.agenticlearning.app"
}

variable "google_oauth_client_id" {
  description = "Google OAuth 2.0 client ID for Firebase Google Sign-In (from GCP APIs & Services > Credentials)"
  type        = string
}


