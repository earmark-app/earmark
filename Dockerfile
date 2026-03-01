FROM python:3.12-slim AS base

# Install supercronic (cron for containers)
ARG TARGETARCH
RUN apt-get update && apt-get install -y --no-install-recommends curl && \
    SUPERCRONIC_URL="https://github.com/aptible/supercronic/releases/download/v0.2.33/supercronic-linux-${TARGETARCH}" && \
    curl -fsSL "${SUPERCRONIC_URL}" -o /usr/local/bin/supercronic && \
    chmod +x /usr/local/bin/supercronic && \
    apt-get purge -y curl && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Create non-root user
RUN groupadd -r earmark && useradd -r -g earmark earmark

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app
COPY src/ ./src/
COPY crontab /app/crontab
COPY --chmod=755 entrypoint.sh /app/entrypoint.sh

# Data directory (mount as volume)
RUN mkdir -p /data && chown -R earmark:earmark /app /data

USER earmark

EXPOSE 8780

CMD ["/app/entrypoint.sh"]
