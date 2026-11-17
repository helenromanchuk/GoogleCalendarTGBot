# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


resource "google_cloud_run_v2_service" "app" {
  name                = var.project_name
  location            = var.region
  project             = var.project_id
  deletion_protection = false
  ingress             = "INGRESS_TRAFFIC_ALL"
  labels = {
    "created-by"                  = "adk"
  }

  template {
    containers {
      image = "us-docker.pkg.dev/cloudrun/container/hello"
      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
      }

      dynamic "volume_mounts" {
        for_each = var.cloud_sql_instance_connection_name == "" ? [] : [var.cloud_sql_instance_connection_name]
        content {
          name       = "cloudsql"
          mount_path = "/cloudsql"
        }
      }

      env {
        name = "DATABASE_URL"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.database_url.secret_id
            version = "latest"
          }
        }
      }

      env {
        name  = "LOGS_BUCKET_NAME"
        value = google_storage_bucket.logs_data_bucket.name
      }

      env {
        name  = "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"
        value = "NO_CONTENT"
      }

      env {
        name  = "BOT_TOKEN"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.bot_token.secret_id
            version = "latest"
          }
        }
      }

      env {
        name  = "GOOGLE_CLIENT_ID"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.google_client_id.secret_id
            version = "latest"
          }
        }
      }

      env {
        name  = "GOOGLE_CLIENT_SECRET"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.google_client_secret.secret_id
            version = "latest"
          }
        }
      }

      env {
        name  = "ENCRYPTION_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.encryption_key.secret_id
            version = "latest"
          }
        }
      }

      env {
        name  = "WEBHOOK_SECRET"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.webhook_secret.secret_id
            version = "latest"
          }
        }
      }
    }

    service_account = google_service_account.app_sa.email
    max_instance_request_concurrency = 8

    dynamic "volumes" {
      for_each = var.cloud_sql_instance_connection_name == "" ? [] : [var.cloud_sql_instance_connection_name]
      content {
        name = "cloudsql"
        cloud_sql_instance {
          instances = [volumes.value]
        }
      }
    }

    scaling {
      # Keep calendar-picker FSM data warm until shared durable storage is configured.
      min_instance_count = 1
      max_instance_count = 10
    }

    session_affinity = true
  }

  traffic {
    type    = "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST"
    percent = 100
  }

  # This lifecycle block prevents Terraform from overwriting the container image when it's
  # updated by Cloud Run deployments outside of Terraform (e.g., via CI/CD pipelines)
  lifecycle {
    ignore_changes = [
      template[0].containers[0].image,
    ]
  }

  # Make dependencies conditional to avoid errors.
  depends_on = [
    resource.google_project_service.services,
    google_secret_manager_secret_iam_member.database_url_accessor,
    google_project_iam_member.cloud_sql_client,
  ]
}
