#!/usr/bin/env bash
# Polls Cloud Monitoring for Cloud Run active instance count every 30s.
# Exits as soon as the count reaches 0, or after MAX_WAIT_SECONDS.
# Run after load tests complete with no further traffic.
#
# Requires: gcloud with Cloud Monitoring API access (roles/monitoring.viewer)

PROJECT=agentic-learning-app-e13cb
REGION=us-central1
SERVICE=backend
POLL_INTERVAL=30
MAX_WAIT_SECONDS=1200  # 20 minutes hard cap

log() { echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] $*"; }

query_instance_count() {
  # Cloud Monitoring MQL: sum active instance count for this service+region.
  # Returns the most recent data point value, or empty string on error.
  gcloud monitoring read \
    --project="$PROJECT" \
    --freshness="5m" \
    'fetch cloud_run_revision
     | metric "run.googleapis.com/container/instance_count"
     | filter resource.service_name == "'"$SERVICE"'" && resource.location == "'"$REGION"'"
     | group_by [], sum(val())
     | within 5m' \
    --format="value(points[0].value.int64Value)" \
    2>/dev/null
}

log "Polling Cloud Monitoring for instance count (poll every ${POLL_INTERVAL}s, max ${MAX_WAIT_SECONDS}s)..."
log "Service: $SERVICE  Region: $REGION  Project: $PROJECT"
echo ""

elapsed=0
while [ "$elapsed" -lt "$MAX_WAIT_SECONDS" ]; do
  count=$(query_instance_count)

  if [ -z "$count" ]; then
    log "No data point returned yet (metric may have no recent data — likely already 0). Retrying..."
  else
    log "Active instances: $count"
    if [ "$count" -eq 0 ]; then
      echo ""
      log "Scale-to-zero confirmed — 0 active instances after ${elapsed}s idle."
      exit 0
    fi
  fi

  sleep "$POLL_INTERVAL"
  elapsed=$((elapsed + POLL_INTERVAL))
done

echo ""
log "Timed out after ${MAX_WAIT_SECONDS}s — scale-to-zero not confirmed."
log "Check manually: Cloud Console > Cloud Run > $SERVICE > Metrics > Instance count"
exit 1
