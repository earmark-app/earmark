# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Earmark is a Dockerized bidirectional sync service between **Hardcover** (book tracking, GraphQL API) and **Audiobookshelf** (self-hosted audiobook server, REST API). It syncs Hardcover reading statuses into ABS collections/playlists and ABS reading progress back to Hardcover. Multi-user, configured via a web GUI on port 8787.

The full specification lives in `spec.md` — refer to it for API details, database schema, sync logic, and edge cases.

## Tech Stack

- **Python 3.12+**, **FastAPI** (web + REST API), **uvicorn** (ASGI server)
- **httpx** (async HTTP), **gql** with httpx transport (Hardcover GraphQL)
- **SQLite** via built-in `sqlite3` (database at `/data/earmark.db`)
- **Pydantic v2** + **pydantic-settings** (models and config)
- **rapidfuzz** (fuzzy title/author matching), **tenacity** (retry/backoff)
- **Supercronic** (container-native cron scheduler)
- **Vanilla JS + Tailwind CSS (CDN)** frontend — no build step

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run the web server (development)
uvicorn src.main:app --host 0.0.0.0 --port 8787 --reload

# Run sync engine manually
python -m src.sync_engine

# Run tests
pytest tests/
pytest tests/test_matching.py           # single test file
pytest tests/test_matching.py::test_fn  # single test function

# Docker
docker compose up --build
docker compose up -d                    # detached
```

## Architecture

```
src/
├── main.py                 # FastAPI app entry point
├── config.py               # Pydantic settings from env vars
├── db.py                   # SQLite schema, migrations, queries
├── models.py               # Pydantic models (API + internal)
├── sync_engine.py          # Sync orchestrator (CLI entry via python -m)
├── platforms/
│   ├── audiobookshelf.py   # ABS REST API client
│   └── hardcover.py        # Hardcover GraphQL client + rate limiter
├── matching/
│   └── book_matcher.py     # 3-tier matching: ASIN → ISBN → fuzzy title+author
└── web/
    ├── routes.py           # FastAPI routes (REST API + GUI serving)
    └── static/             # SPA frontend (index.html, app.js, style.css)
```

**Sync cycle** (cron, default every 15 min):
1. **HC → ABS (list sync)**: For each sync rule, fetch HC books by status/list, match to ABS items via 3-tier matching, compute diff, batch add/remove from ABS collections/playlists.
2. **ABS → HC (progress sync)**: Fetch ABS progress, map to HC books, update HC status (reading/read) based on progress. Highest-value-wins — never decrease progress.

**Book matching tiers**: (1) ASIN exact match, (2) ISBN exact match (isbn_13/isbn_10 across all editions), (3) Fuzzy title+author with Levenshtein similarity, accept if confidence >= 0.85.

## Key Design Decisions

- **Conflict resolution**: HC status is authoritative for list membership; ABS progress is authoritative for reading position. DNF (status 5) is never overwritten by ABS progress.
- **Loop prevention**: After writing to a target, immediately update sync state so the change isn't re-detected.
- **Empty list safety**: If HC returns 0 books but previous sync had >0, skip removal and log a warning.
- **Token masking**: API tokens are returned as `"***"` in GET responses; only accepted on POST/PUT.
- **Rate limiting**: Hardcover has 60 req/min limit (token bucket in `hardcover.py`). ABS has no limit but self-impose max 10 req/sec.
- **Auth**: No GUI auth by default (LAN/VPN use). Set `GUI_PASSWORD` env var to enable HTTP Basic Auth (username: `admin`).

## Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `TZ` | `UTC` | Timezone for cron/logs |
| `LOG_LEVEL` | `info` | Python logging level |
| `DATA_DIR` | `/data` | SQLite DB location |
| `GUI_PASSWORD` | *(none)* | Enables HTTP Basic Auth if set |
| `DRY_RUN` | `false` | Log changes without executing |
| `PORT` | `8787` | Web GUI port |

## External APIs

- **Hardcover**: `https://api.hardcover.app/v1/graphql` — Bearer token auth, 60 req/min, status IDs: 1=Want to Read, 2=Currently Reading, 3=Read, 5=DNF
- **Audiobookshelf**: User-configured URL — Bearer API key auth, no rate limit. Collections are library-scoped (admin required), playlists are user-scoped.
