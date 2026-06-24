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

resource "google_secret_manager_secret" "bot_token" {
  secret_id = "${var.project_name}-bot-token"
  project   = var.project_id
  replication {
    auto {}
  }
  depends_on = [resource.google_project_service.services]
}

resource "google_secret_manager_secret" "google_client_id" {
  secret_id = "${var.project_name}-google-client-id"
  project   = var.project_id
  replication {
    auto {}
  }
  depends_on = [resource.google_project_service.services]
}

resource "google_secret_manager_secret" "google_client_secret" {
  secret_id = "${var.project_name}-google-client-secret"
  project   = var.project_id
  replication {
    auto {}
  }
  depends_on = [resource.google_project_service.services]
}

resource "google_secret_manager_secret" "encryption_key" {
  secret_id = "${var.project_name}-encryption-key"
  project   = var.project_id
  replication {
    auto {}
  }
  depends_on = [resource.google_project_service.services]
}

# IAM Permissions for App SA to access secrets
resource "google_secret_manager_secret_iam_member" "bot_token_accessor" {
  project   = var.project_id
  secret_id = google_secret_manager_secret.bot_token.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.app_sa.email}"
}

resource "google_secret_manager_secret_iam_member" "google_client_id_accessor" {
  project   = var.project_id
  secret_id = google_secret_manager_secret.google_client_id.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.app_sa.email}"
}

resource "google_secret_manager_secret_iam_member" "google_client_secret_accessor" {
  project   = var.project_id
  secret_id = google_secret_manager_secret.google_client_secret.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.app_sa.email}"
}

resource "google_secret_manager_secret_iam_member" "encryption_key_accessor" {
  project   = var.project_id
  secret_id = google_secret_manager_secret.encryption_key.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.app_sa.email}"
}
