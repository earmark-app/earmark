#!/bin/bash
set -e

# Graceful shutdown handler
cleanup() {
    echo "Received shutdown signal, stopping..."
    kill "$UVICORN_PID" "$CRON_PID" 2>/dev/null || true
    wait "$UVICORN_PID" "$CRON_PID" 2>/dev/null || true
    exit 0
}
trap cleanup SIGTERM SIGINT

# Start the web GUI in the background
python -m uvicorn src.main:app --host 0.0.0.0 --port 8780 &
UVICORN_PID=$!

# Start the cron scheduler in the background
supercronic /app/crontab &
CRON_PID=$!

# Wait for either process to exit — if one dies, stop the container
wait -n "$UVICORN_PID" "$CRON_PID"
EXIT_CODE=$?

echo "A process exited with code $EXIT_CODE, shutting down..."
cleanup
exit $EXIT_CODE
