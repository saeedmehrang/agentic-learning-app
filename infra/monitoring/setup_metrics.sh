#!/usr/bin/env bash
# infra/monitoring/setup_metrics.sh
#
# Creates the Cloud Logging log-based metrics and imports the monitoring dashboard.
# Run once per GCP project. Requires:
#   gcloud auth login (project owner or monitoring.admin role)
#
# Usage:
#   bash infra/monitoring/setup_metrics.sh
#
# After running this script, visit:
#   https://console.cloud.google.com/monitoring/dashboards?project=agentic-learning-app-e13cb

set -euo pipefail

PROJECT="agentic-learning-app-e13cb"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Setting up Cloud Monitoring observability for project: $PROJECT"

# ---------------------------------------------------------------------------
# Log-based metrics
# Each metric extracts a field from structured 'agent_turn_complete' log entries
# emitted by backend/main.py. Labels allow filtering by agent and app_version.
# ---------------------------------------------------------------------------

echo "Creating log-based metric: agent_turn_latency_p50..."
gcloud logging metrics create agent_turn_latency_p50 \
  --project="$PROJECT" \
  --description="p50 latency (ms) per agent_turn_complete log entry" \
  --log-filter='jsonPayload.message="agent_turn_complete"' \
  --value-extractor='EXTRACT(jsonPayload.latency_ms)' \
  --metric-kind=GAUGE \
  --value-type=DISTRIBUTION \
  --label-descriptors='key=agent,valueType=STRING,description="Agent name"' \
  --label-descriptors='key=app_version,valueType=STRING,description="Deployment version (commit SHA)"' \
  --label-extractors='agent=EXTRACT(jsonPayload.agent)' \
  --label-extractors='app_version=EXTRACT(jsonPayload.app_version)' \
  2>/dev/null || echo "  (already exists, skipping)"

echo "Creating log-based metric: agent_turn_latency_p95..."
# p95 uses the same distribution metric — Cloud Monitoring computes percentiles
# from the distribution. We create one distribution metric for latency (not two).
# The dashboard references 'agent_turn_latency_p50' for both p50/p95 percentile
# tiles — Cloud Monitoring UI lets you pick the percentile from the same distribution.
echo "  Note: p50 and p95 are derived from the same distribution metric in the dashboard."

echo "Creating log-based metric: agent_turn_count..."
gcloud logging metrics create agent_turn_count \
  --project="$PROJECT" \
  --description="Count of agent_turn_complete log entries" \
  --log-filter='jsonPayload.message="agent_turn_complete"' \
  --metric-kind=DELTA \
  --value-type=INT64 \
  --label-descriptors='key=agent,valueType=STRING,description="Agent name"' \
  --label-descriptors='key=app_version,valueType=STRING,description="Deployment version (commit SHA)"' \
  --label-extractors='agent=EXTRACT(jsonPayload.agent)' \
  --label-extractors='app_version=EXTRACT(jsonPayload.app_version)' \
  2>/dev/null || echo "  (already exists, skipping)"

# ---------------------------------------------------------------------------
# Import dashboard
# ---------------------------------------------------------------------------

echo "Importing monitoring dashboard..."
gcloud monitoring dashboards create \
  --project="$PROJECT" \
  --config-from-file="$SCRIPT_DIR/dashboard.json"

echo ""
echo "Done. Visit the Monitoring Dashboards page to view:"
echo "  https://console.cloud.google.com/monitoring/dashboards?project=$PROJECT"
echo ""
echo "Cloud Trace (live waterfall view):"
echo "  https://console.cloud.google.com/traces/list?project=$PROJECT"
echo ""
echo "Tip: metrics appear ~1 minute after the first agent_turn_complete log entry."
