# Dockerfile — Content generation + embedding + seeding pipeline image.
#
# Installs all three pipelines (generate_content.py, embed_content.py, seed_db.py)
# into a single image. The default entrypoint runs generate_content.py.
# Override --entrypoint on `gcloud run jobs execute` to run the other scripts.
#
# Build from repo root (required — includes both content-generation/ and courses/):
#
#   gcloud builds submit \
#     --config infra/cloudbuild/content-generate.yaml \
#     .
#
# Environment variables (Cloud Run Job):
#   GCS_PIPELINE_BUCKET   — GCS bucket name for pipeline I/O (required on Cloud Run)
#   GCS_PIPELINE_BUCKET is absent → local filesystem used (local dev only)
#
# Secret bindings (seed job only):
#   DB_PASSWORD                  — from Secret Manager
#   DB_INSTANCE_CONNECTION_NAME  — from Secret Manager

FROM python:3.13-slim

WORKDIR /app

# Install all pipeline dependencies (gcs + seed extras)
COPY content-generation/pyproject.toml .
RUN pip install --no-cache-dir ".[gcs,seed]"

# Copy pipeline scripts and shared modules
COPY content-generation/generate_content.py \
     content-generation/embed_content.py \
     content-generation/seed_db.py \
     content-generation/config.py \
     content-generation/review_models.py \
     content-generation/storage.py \
     content-generation/token_usage_log.py \
     ./

# Copy static course assets needed at generation time
# (pipeline/ outputs are NOT copied — they live in GCS on Cloud Run)
COPY courses/linux-basics/outlines.yaml     /courses/linux-basics/outlines.yaml
COPY courses/linux-basics/concept_map.json  /courses/linux-basics/concept_map.json
COPY courses/linux-basics/prompts/          /courses/linux-basics/prompts/

ENTRYPOINT ["python", "generate_content.py"]
