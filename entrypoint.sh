#!/bin/bash
set -e

# LinuxServer-style PUID/PGID support
PUID=${PUID:-1000}
PGID=${PGID:-1000}

if [ "$(id -u)" = "0" ]; then
    # Update earmark user/group to match requested PUID/PGID
    groupmod -o -g "$PGID" earmark
    usermod -o -u "$PUID" earmark

    # Ensure directories are writable
    mkdir -p /data
    chown -R earmark:earmark /data /app

    echo "Running as uid=$PUID gid=$PGID"
    exec gosu earmark "$0" "$@"
fi

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
