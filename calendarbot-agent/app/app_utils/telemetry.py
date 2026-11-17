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

import logging
import os


def setup_telemetry() -> str | None:
    """Configure OpenTelemetry and GenAI telemetry.

    If ``LOGS_BUCKET_NAME`` is set and ``OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT``
    is not ``"false"``, the function configures environment variables required for GenAI
    telemetry export to a GCS bucket. Otherwise, telemetry is disabled but the function
    returns ``None`` without emitting a warning.
    """

    bucket = os.getenv("LOGS_BUCKET_NAME")
    capture_content = os.getenv(
        "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", "false"
    )

    # Only configure telemetry when a bucket is provided and content capture is enabled.
    if bucket and capture_content.lower() != "false":
        logging.info(
            "Prompt‑response logging enabled – mode: NO_CONTENT (metadata only)"
        )
        os.environ["OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"] = "NO_CONTENT"
        os.environ.setdefault("OTEL_INSTRUMENTATION_GENAI_UPLOAD_FORMAT", "jsonl")
        os.environ.setdefault("OTEL_INSTRUMENTATION_GENAI_COMPLETION_HOOK", "upload")
        os.environ.setdefault(
            "OTEL_SEMCONV_STABILITY_OPT_IN", "gen_ai_latest_experimental"
        )
        commit_sha = os.getenv("COMMIT_SHA", "dev")
        os.environ.setdefault(
            "OTEL_RESOURCE_ATTRIBUTES",
            f"service.namespace=calendarbot-agent,service.version={commit_sha}",
        )
        path = os.getenv("GENAI_TELEMETRY_PATH", "completions")
        os.environ.setdefault(
            "OTEL_INSTRUMENTATION_GENAI_UPLOAD_BASE_PATH",
            f"gs://{bucket}/{path}",
        )
        return bucket
    else:
        # Telemetry disabled – no bucket configured or content capture disabled.
        logging.debug(
            "Telemetry disabled (LOGS_BUCKET_NAME not set or content capture disabled)."
        )
        return None
