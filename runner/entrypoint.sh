#!/bin/sh
# Runner entrypoint — selects cron or service mode based on SERVICE_MODE env var.
#
# SERVICE_MODE=true  → start the FastAPI HTTP server (service agent)
# SERVICE_MODE unset → run the agent loop once and exit (cron agent)

set -e

if [ "$SERVICE_MODE" = "true" ]; then
    exec uvicorn service_runner:app --host 0.0.0.0 --port "${SERVICE_PORT:-8080}"
else
    exec python run_agent.py
fi
