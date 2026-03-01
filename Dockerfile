FROM python:3.12-slim AS base

# Install supercronic
COPY --from=ghcr.io/aptible/supercronic:latest /usr/local/bin/supercronic /usr/local/bin/supercronic

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

EXPOSE 8787

CMD ["/app/entrypoint.sh"]
