# infra/monitoring

Cloud Monitoring dashboard and log-based metrics for the backend agent pipeline.

## Files

| File | Purpose |
|---|---|
| `setup_metrics.sh` | One-time setup: creates log-based metrics in Cloud Logging and imports the dashboard |
| `dashboard.json` | Cloud Monitoring dashboard definition (importable via `gcloud`) |

## One-time setup

Run once per GCP project (requires `roles/monitoring.admin` or project owner):

```bash
bash infra/monitoring/setup_metrics.sh
```

This creates:
- **`agent_turn_latency_p50`** — distribution metric extracted from `agent_turn_complete` log entries; labelled by `agent` and `app_version`
- **`agent_turn_count`** — delta count of `agent_turn_complete` entries; same labels
- The **Agentic Learning — Backend Latency** dashboard in Cloud Monitoring

## Dashboard

The dashboard contains three panels:

| Panel | What it shows |
|---|---|
| Agent Turn Latency by Version (p50/p95) | Latency trend per agent, broken out by deployment version — use this to compare before/after a code change |
| Agent Turn Count by Agent & Version | Request rate per agent per version (stacked bar) |
| Backend Error Rate (5xx) | Cloud Run 5xx error rate from built-in Cloud Run metrics |

View after setup:
[Cloud Monitoring Dashboards](https://console.cloud.google.com/monitoring/dashboards?project=agentic-learning-app-e13cb)

## How version grouping works

`APP_VERSION` is set to the merge commit SHA at deploy time:

```bash
gcloud run deploy backend --set-env-vars APP_VERSION=$(git rev-parse --short HEAD) ...
```

Convention: **squash-merge to `main`, then deploy immediately.** Each series on the latency chart corresponds to one squash-merge PR, making before/after comparison unambiguous. Local `uvicorn` runs always emit `app_version=dev` and are separated from deployed traffic.

## Log filter reference

Useful Cloud Logging queries for manual investigation:

```
# All agent turn completions
jsonPayload.message="agent_turn_complete"

# Slow turns (> 5 seconds)
jsonPayload.message="agent_turn_complete" AND jsonPayload.latency_ms > 5000

# Specific agent only
jsonPayload.message="agent_turn_complete" AND jsonPayload.agent="lesson_agent"

# Specific deployment version
jsonPayload.message="agent_turn_complete" AND jsonPayload.app_version="a1b2c3d"

# Errors
severity=ERROR
```
