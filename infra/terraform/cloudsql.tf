# ---------------------------------------------------------------------------
# Cloud SQL — PostgreSQL 15 with pgvector, private IP only
# Phase 0.3
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Private Service Access — VPC peering so Cloud Run (serverless VPC) can
# reach Cloud SQL on its private IP without a public endpoint.
# ---------------------------------------------------------------------------

resource "google_compute_global_address" "private_ip_range" {
  name          = "learning-app-private-ip-range"
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = 16
  network       = "projects/${var.project_id}/global/networks/default"
}

resource "google_service_networking_connection" "private_vpc_connection" {
  network                 = "projects/${var.project_id}/global/networks/default"
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.private_ip_range.name]
}

# ---------------------------------------------------------------------------
# DB password — generated once, stored in Secret Manager.
# The random_password resource means `terraform apply` is self-contained:
# no need to run push_secrets.sh for DB_PASSWORD before applying.
# ---------------------------------------------------------------------------

resource "random_password" "db_password" {
  length  = 32
  special = false # Avoids shell quoting issues in connection strings
}

resource "google_secret_manager_secret_version" "db_password" {
  secret      = google_secret_manager_secret.db_password.id
  secret_data = random_password.db_password.result

  # Prevent Terraform from rotating the password on every plan
  lifecycle {
    ignore_changes = [secret_data]
  }
}

# ---------------------------------------------------------------------------
# Cloud SQL instance
# ---------------------------------------------------------------------------

resource "google_sql_database_instance" "learning_app" {
  name             = "learning-app-db"
  database_version = "POSTGRES_17"
  region           = var.region

  # Dev environment — allow destroy without protection
  deletion_protection = false

  settings {
    tier    = "db-f1-micro" # Frugal constraint — must not change
    edition = "ENTERPRISE"  # PostgreSQL 16+ defaults to Enterprise Plus via API — force Enterprise to avoid ~3× cost increase

    ip_configuration {
      ipv4_enabled    = false # No public IP — private VPC only
      private_network = "projects/${var.project_id}/global/networks/default"
    }

    backup_configuration {
      enabled    = true
      start_time = "03:00" # 03:00 UTC daily backup window
    }

  }

  depends_on = [google_service_networking_connection.private_vpc_connection]
}

# ---------------------------------------------------------------------------
# Application database
# ---------------------------------------------------------------------------

resource "google_sql_database" "learning_app" {
  name     = "learning_app"
  instance = google_sql_database_instance.learning_app.name
}

# ---------------------------------------------------------------------------
# Application user
# ---------------------------------------------------------------------------

resource "google_sql_user" "app_user" {
  name     = "app_user"
  instance = google_sql_database_instance.learning_app.name
  password = random_password.db_password.result
}

# ---------------------------------------------------------------------------
# Outputs — referenced by push_secrets.sh and local dev setup
# ---------------------------------------------------------------------------

output "cloud_sql_connection_name" {
  description = "Cloud SQL connection name for Auth Proxy and Secret Manager"
  value       = google_sql_database_instance.learning_app.connection_name
}

output "cloud_sql_private_ip" {
  description = "Private IP of the Cloud SQL instance (for Cloud Run VPC access)"
  value       = google_sql_database_instance.learning_app.private_ip_address
}

# ---------------------------------------------------------------------------
# pgvector activation
# Cloud SQL's database_flags enables the pgvector capability at the instance
# level (see above). The extension itself must be created inside the database
# after apply:
#
#   # Via Cloud SQL Auth Proxy (local dev):
#   cloud-sql-proxy agentic-learning-app-e13cb:us-central1:learning-app-db &
#   psql "host=127.0.0.1 port=5432 dbname=learning_app user=app_user" \
#     -c "CREATE EXTENSION IF NOT EXISTS vector;"
#
# This one-time step is documented in infra/scripts/enable_pgvector.sh
# ---------------------------------------------------------------------------
