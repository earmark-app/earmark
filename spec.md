# 🔖 Earmark — Audiobookshelf ↔ Hardcover Sync Service

## 📌 Overview

Earmark is a Dockerized service that provides **bidirectional sync between Hardcover and Audiobookshelf (ABS)**. The primary use case is syncing Hardcover reading statuses (especially "Want to Read") into ABS collections/playlists, and syncing ABS reading progress back to Hardcover. It supports **multiple users**, provides a **web GUI** for configuration, and deploys via **Docker Compose**.

### Core Use Cases

1. **Hardcover → ABS list sync**: When a user marks a book "Want to Read" on Hardcover, it appears in a synced ABS collection or playlist
2. **ABS → Hardcover progress sync**: Reading progress and finished status in ABS syncs to Hardcover
3. **Multi-user**: Each user maps their own Hardcover account to their own ABS account
4. **Configurable sync targets**: Each Hardcover status can map to a different ABS collection or playlist

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────┐
│                 Docker Compose                   │
│                                                  │
│  ┌──────────────┐    ┌────────────────────────┐  │
│  │   Web GUI     │    │     Sync Engine        │  │
│  │  (FastAPI)    │◄──►│  (Python, Cron-based)  │  │
│  │  Port 8787    │    │  via Supercronic       │  │
│  └──────┬───────┘    └──────────┬─────────────┘  │
│         │                       │                 │
│         ▼                       ▼                 │
│  ┌──────────────────────────────────────────┐    │
│  │         SQLite Database                   │    │
│  │   /data/earmark.db (Docker volume)      │    │
│  └──────────────────────────────────────────┘    │
└─────────────────────────────────────────────────┘
         │                       │
         ▼                       ▼
  ┌──────────────┐     ┌──────────────────┐
  │  Hardcover    │     │  Audiobookshelf   │
  │  GraphQL API  │     │  REST API         │
  └──────────────┘     └──────────────────┘
```

### Tech Stack

| Component | Technology | Rationale |
|-----------|-----------|-----------|
| Language | **Python 3.12+** | Built-in sqlite3, excellent HTTP clients, fast prototyping |
| Web framework | **FastAPI** | Async, auto-generated OpenAPI docs, lightweight |
| HTTP client | **httpx** | Async support, connection pooling, timeout handling |
| GraphQL client | **gql** with `httpx` transport | Clean query building, variable support |
| Scheduler | **Supercronic** | Container-native cron, logs to stdout, no root required |
| Database | **SQLite** | Zero-config, file-based, transactional, Docker volume-friendly |
| Frontend | **Vanilla JS + Tailwind CSS (CDN)** | No build step, served by FastAPI static files |
| Container | **Python 3.12-slim + Supercronic** | Minimal image size (~150 MB) |

---

## 📁 Project Structure

```
earmark/
├── docker-compose.yml
├── Dockerfile
├── crontab
├── requirements.txt
├── .env.example
├── README.md
├── SPEC.md                          # This file
│
├── src/
│   ├── __init__.py
│   ├── main.py                      # FastAPI app entry point
│   ├── config.py                    # Pydantic settings from env vars
│   ├── db.py                        # SQLite schema, migrations, queries
│   ├── models.py                    # Pydantic models (API + internal)
│   ├── sync_engine.py               # Main sync orchestrator (CLI entry point)
│   │
│   ├── platforms/
│   │   ├── __init__.py
│   │   ├── audiobookshelf.py        # ABS REST API client
│   │   └── hardcover.py             # Hardcover GraphQL API client
│   │
│   ├── matching/
│   │   ├── __init__.py
│   │   └── book_matcher.py          # 3-tier ISBN/ASIN/fuzzy matching
│   │
│   └── web/
│       ├── __init__.py
│       ├── routes.py                # FastAPI routes for GUI + API
│       └── static/
│           ├── index.html           # SPA shell
│           ├── app.js               # Frontend logic
│           └── style.css            # Minimal custom styles (Tailwind via CDN)
│
└── tests/
    ├── test_matching.py
    ├── test_sync.py
    └── test_api.py
```

---

## 🗄️ Database Schema

All state lives in a single SQLite file at `/data/earmark.db`.

```sql
-- ========================================
-- Users: each row is a paired HC + ABS account
-- ========================================
CREATE TABLE users (
    id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(8)))),
    name TEXT NOT NULL,                          -- Display name, eg "Toby"
    hardcover_token TEXT NOT NULL,               -- HC Bearer token
    hardcover_user_id INTEGER,                   -- Resolved on first sync
    abs_url TEXT NOT NULL,                        -- eg "https://abs.example.com"
    abs_api_key TEXT NOT NULL,                    -- ABS API key
    abs_user_id TEXT,                             -- Resolved on first sync
    abs_library_ids TEXT DEFAULT '[]',            -- JSON array of library IDs to sync (empty = all)
    enabled INTEGER DEFAULT 1,                   -- 0 = paused
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

-- ========================================
-- Sync rules: maps HC status → ABS target
-- ========================================
CREATE TABLE sync_rules (
    id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(8)))),
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    direction TEXT NOT NULL CHECK (direction IN ('hc_to_abs', 'abs_to_hc', 'bidirectional')),

    -- Hardcover side
    hc_status_id INTEGER,                        -- 1=Want to Read, 2=Reading, 3=Read, 5=DNF
    hc_list_id INTEGER,                          -- OR: specific HC list ID (nullable)

    -- Audiobookshelf side
    abs_target_type TEXT NOT NULL CHECK (abs_target_type IN ('collection', 'playlist')),
    abs_target_name TEXT NOT NULL,                -- eg "Want to Read", "Currently Listening"
    abs_target_id TEXT,                           -- Resolved/created on first sync
    abs_library_id TEXT NOT NULL,                 -- Which ABS library the collection/playlist belongs to

    enabled INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

-- ========================================
-- Book mapping cache: matched pairs
-- ========================================
CREATE TABLE book_mappings (
    id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(8)))),
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    hardcover_book_id INTEGER NOT NULL,
    hardcover_edition_id INTEGER,
    abs_library_item_id TEXT NOT NULL,
    match_method TEXT NOT NULL,                   -- 'isbn', 'asin', 'title_author'
    match_confidence REAL DEFAULT 1.0,            -- 0.0-1.0
    title TEXT,                                   -- For display/debugging
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(user_id, hardcover_book_id, abs_library_item_id)
);

-- ========================================
-- Sync state: tracks what's been synced
-- ========================================
CREATE TABLE sync_state (
    id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(8)))),
    rule_id TEXT NOT NULL REFERENCES sync_rules(id) ON DELETE CASCADE,
    book_mapping_id TEXT NOT NULL REFERENCES book_mappings(id) ON DELETE CASCADE,
    last_synced_at TEXT DEFAULT (datetime('now')),
    sync_direction TEXT NOT NULL,                 -- Which direction this entry was synced
    UNIQUE(rule_id, book_mapping_id)
);

-- ========================================
-- Progress sync state (ABS → HC direction)
-- ========================================
CREATE TABLE progress_state (
    id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(8)))),
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    abs_library_item_id TEXT NOT NULL,
    hardcover_book_id INTEGER NOT NULL,
    last_abs_progress REAL,                      -- 0.0-1.0
    last_abs_is_finished INTEGER DEFAULT 0,
    last_hc_status_id INTEGER,
    last_synced_at TEXT DEFAULT (datetime('now')),
    UNIQUE(user_id, abs_library_item_id)
);

-- ========================================
-- Sync log: audit trail
-- ========================================
CREATE TABLE sync_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
    rule_id TEXT REFERENCES sync_rules(id) ON DELETE SET NULL,
    action TEXT NOT NULL,                         -- 'added_to_collection', 'removed_from_collection',
                                                 -- 'progress_updated', 'status_updated', 'match_found',
                                                 -- 'match_failed', 'error'
    direction TEXT,                               -- 'hc_to_abs' or 'abs_to_hc'
    details TEXT,                                 -- JSON blob with context
    created_at TEXT DEFAULT (datetime('now'))
);

-- Index for log queries
CREATE INDEX idx_sync_log_user ON sync_log(user_id, created_at DESC);
CREATE INDEX idx_sync_log_action ON sync_log(action, created_at DESC);
```

---

## 🔌 External APIs

### Hardcover GraphQL API

- **Endpoint**: `https://api.hardcover.app/v1/graphql`
- **Auth**: `Authorization: Bearer <token>` (token from https://hardcover.app/account/api)
- **Rate limit**: 60 requests/minute
- **Token expiry**: Annual (January 1st), manual renewal required

#### Key Queries

```graphql
# Get current user ID
{ me { id username } }

# Get all books with a specific status (eg status_id=1 for "Want to Read")
{
  me {
    user_books(where: {status_id: {_eq: 1}}) {
      id
      status_id
      rating
      book {
        id
        title
        slug
        cached_contributors
        editions {
          id
          isbn_13
          isbn_10
          asin
          format
        }
      }
    }
  }
}

# Get all user books (all statuses)
{
  me {
    user_books {
      id
      status_id
      book {
        id
        title
        cached_contributors
        editions { isbn_13 isbn_10 asin }
      }
    }
  }
}

# Get user lists
{
  me {
    lists {
      id
      name
      list_books {
        book_id
        book { title }
      }
    }
  }
}

# Search for a book by ISBN
{
  editions(where: {isbn_13: {_eq: "9780553418026"}}) {
    id
    book { id title slug }
  }
}

# Search for a book by ASIN
{
  editions(where: {asin: {_eq: "B002V0QK4C"}}) {
    id
    book { id title slug }
  }
}

# Text search
{
  search(query: "Project Hail Mary", query_type: "books", per_page: 5, page: 1) {
    results
  }
}
```

#### Key Mutations

```graphql
# Set book status
mutation { insert_user_book(object: {book_id: 123, status_id: 2}) { id } }

# Update book status
mutation { update_user_book(where: {book_id: {_eq: 123}}, _set: {status_id: 3}) { returning { id } } }
```

#### Hardcover Status IDs

| status_id | Meaning |
|-----------|---------|
| 1 | Want to Read |
| 2 | Currently Reading |
| 3 | Read |
| 5 | Did Not Finish |

### Audiobookshelf REST API

- **Endpoint**: User-configured (self-hosted), eg `https://abs.example.com`
- **Auth**: `Authorization: Bearer <api_key>` (Settings → Users → API Keys)
- **Rate limit**: None (self-hosted)
- **Docs**: https://api.audiobookshelf.org/

#### Key Endpoints

```
# User info + all progress
GET /api/me
→ { id, username, mediaProgress: [...] }

# List libraries
GET /api/libraries
→ { libraries: [{ id, name, mediaType }] }

# List library items (paginated)
GET /api/libraries/:libId/items?limit=100&page=0&sort=media.metadata.title
→ { results: [{ id, media: { metadata: { title, authors, isbn, asin } } }] }

# Search library
GET /api/libraries/:libId/search?q=<query>
→ { book: [{ libraryItem: { id, ... } }] }

# Get item details
GET /api/items/:itemId?expanded=1&include=progress
→ { id, media: { metadata: { title, authorName, isbn, asin } }, userMediaProgress: { ... } }

# --- Collections ---

# List collections
GET /api/libraries/:libId/collections
→ { results: [{ id, name, books: [...] }] }

# Create collection
POST /api/collections
Body: { libraryId: "lib_xxx", name: "Want to Read" }
→ { id, name, ... }

# Add book to collection
POST /api/collections/:colId/book
Body: { id: "li_xxx" }
→ { ... }

# Remove book from collection
DELETE /api/collections/:colId/book/:bookId
→ { ... }

# Batch add books to collection
POST /api/collections/:colId/batch/add
Body: { books: ["li_xxx", "li_yyy"] }
→ { ... }

# Batch remove books from collection
POST /api/collections/:colId/batch/remove
Body: { books: ["li_xxx", "li_yyy"] }
→ { ... }

# --- Playlists ---

# List playlists
GET /api/playlists
→ { playlists: [{ id, name, items: [...] }] }

# Create playlist
POST /api/playlists
Body: { libraryId: "lib_xxx", name: "Want to Read" }
→ { id, name, ... }

# Add item to playlist
POST /api/playlists/:plId/item
Body: { libraryItemId: "li_xxx" }
→ { ... }

# Remove item from playlist
DELETE /api/playlists/:plId/item/:itemId
→ { ... }

# Batch add to playlist
POST /api/playlists/:plId/batch/add
Body: { items: [{ libraryItemId: "li_xxx" }] }
→ { ... }

# Batch remove from playlist
POST /api/playlists/:plId/batch/remove
Body: { items: [{ libraryItemId: "li_xxx" }] }
→ { ... }

# --- Progress ---

# Update progress
PATCH /api/me/progress/:libraryItemId
Body: { progress: 0.75, currentTime: 9000, isFinished: false, duration: 12000 }
```

#### ABS Notes

- **Collections** are library-scoped and visible to all users (require admin token to create)
- **Playlists** are user-scoped and private to the authenticated user
- Items are identified by `libraryItemId` (prefixed `li_`)
- Progress is a float 0.0–1.0; `isFinished: true` marks complete
- Metadata fields: `title`, `authorName`, `isbn`, `asin`, `narrator`, `series`, `publishedYear`

---

## 🔄 Sync Logic

### Sync Cycle (runs on cron, eg every 15 min)

For **each enabled user**:

#### Phase 1: HC → ABS (List Sync)

For each `sync_rule` with direction `hc_to_abs` or `bidirectional`:

1. **Fetch HC books** matching the rule's `hc_status_id` (or `hc_list_id`)
2. **For each HC book**, attempt to match to an ABS library item:
   - **Tier 1 — ASIN**: Query ABS library items, match on `asin` field
   - **Tier 2 — ISBN**: Match on `isbn` (try isbn_13, then isbn_10, across all HC editions)
   - **Tier 3 — Fuzzy title+author**: Search ABS by title, score by Levenshtein similarity on title AND author. Accept if confidence ≥ 0.85. Log and skip below threshold.
   - Cache successful matches in `book_mappings` table
3. **Ensure ABS target exists**: Find or create the collection/playlist named in the rule
4. **Compute diff**:
   - Books in HC list but NOT in ABS target → **add** to ABS target
   - Books in ABS target but NOT in HC list → **remove** from ABS target (configurable: `remove_stale` flag on the rule, default `true`)
5. **Execute changes** via ABS batch add/remove endpoints
6. **Log** all actions to `sync_log`

#### Phase 2: ABS → HC (Progress Sync)

For each `sync_rule` with direction `abs_to_hc` or `bidirectional`:

1. **Fetch ABS progress** via `GET /api/me` → `mediaProgress[]`
2. **For each item with progress > 0**, check `progress_state`:
   - If progress has **increased** since last sync (never decrease / highest-value-wins):
     - `progress > 0 && !isFinished` → Set HC status to 2 (Currently Reading)
     - `isFinished === true` → Set HC status to 3 (Read)
   - If item has no HC mapping yet, attempt book match (same 3-tier strategy)
3. **Update HC** via `insert_user_book` / `update_user_book` mutations
4. **Update** `progress_state` with new values
5. **Log** all actions

#### Conflict Resolution

- **Status conflicts**: HC status takes precedence for list membership; ABS progress takes precedence for reading position
- **Progress**: Highest-value-wins — never decrease progress in either direction
- **Deletions**: Removing from HC "Want to Read" removes from synced ABS collection (if `remove_stale=true`). Removing from ABS collection does NOT update HC status (ABS collections are the "follower")
- **DNF (status 5)**: Preserved on Hardcover, never overwritten by ABS progress sync
- **Loop prevention**: After writing to a target, immediately update sync state so the change isn't detected as "new" on the next cycle

### Rate Limiting

- Hardcover: Max 60 req/min. Implement a token bucket rate limiter in `hardcover.py`. Use exponential backoff on 429 responses (via `tenacity`).
- ABS: No rate limit, but be courteous — batch operations where possible, max 10 req/sec self-imposed.

---

## 🖥️ Web GUI

### Pages / Views

The GUI is a single-page app served by FastAPI at port **8787**.

#### 1. Dashboard (`/`)

- Overview cards: total users, total synced books, last sync time, next sync time
- Recent sync log (last 20 entries) with status indicators (✅ synced, ⚠️ partial, ❌ error)
- "Sync Now" button to trigger an immediate sync cycle

#### 2. Users (`/users`)

- List of configured user pairs
- For each user: name, HC username (resolved), ABS username (resolved), enabled toggle, last sync time
- Add/edit user form:
  - Display name
  - Hardcover API token (password field)
  - ABS server URL
  - ABS API key (password field)
  - ABS library filter (multi-select, populated after connection test)
  - "Test Connection" button that validates both APIs and shows the resolved usernames
- Delete user (with confirmation)

#### 3. Sync Rules (`/rules`)

- List of rules per user
- Add/edit rule form:
  - User (dropdown)
  - Direction: HC→ABS / ABS→HC / Bidirectional
  - HC source: Status dropdown (Want to Read, Reading, Read, DNF) OR specific list (fetched from HC API)
  - ABS target type: Collection / Playlist
  - ABS target name: text input (auto-created if doesn't exist)
  - ABS library: dropdown (from user's configured libraries)
  - Remove stale: checkbox (default on)
  - Enabled: toggle
- Delete rule (with confirmation)

#### 4. Book Mappings (`/mappings`)

- Table of matched book pairs per user
- Columns: HC title, ABS title, match method (ISBN/ASIN/fuzzy), confidence score, synced rules
- Filter by: user, match method, unmatched only
- Action: manually link/unlink books

#### 5. Sync Log (`/log`)

- Filterable table of all sync events
- Filters: user, action type, direction, date range
- Paginated, newest first

#### 6. Settings (`/settings`)

- Global sync interval (cron expression, default `*/15 * * * *`)
- Dry run mode (log changes but don't execute)
- Log retention (days, default 30)
- Fuzzy match threshold (default 0.85)
- "Export config" / "Import config" (JSON backup of users + rules, tokens redacted)

### REST API (used by GUI and available for external use)

```
GET    /api/health                     → { status, last_sync, next_sync, version }
GET    /api/users                      → list users
POST   /api/users                      → create user
PUT    /api/users/:id                  → update user
DELETE /api/users/:id                  → delete user
POST   /api/users/:id/test             → test connections, return resolved usernames + libraries

GET    /api/rules                      → list rules (optionally filter by user_id)
POST   /api/rules                      → create rule
PUT    /api/rules/:id                  → update rule
DELETE /api/rules/:id                  → delete rule

GET    /api/mappings                   → list book mappings (filter by user_id, method, etc.)
POST   /api/mappings                   → manually create a mapping
DELETE /api/mappings/:id               → delete a mapping

GET    /api/log                        → paginated sync log (filter by user, action, date)
DELETE /api/log                        → clear log (with optional date cutoff)

POST   /api/sync                       → trigger immediate sync for all users
POST   /api/sync/:userId               → trigger immediate sync for one user

GET    /api/settings                   → get global settings
PUT    /api/settings                   → update global settings

GET    /api/hardcover/:userId/statuses  → proxy: get HC user books grouped by status
GET    /api/hardcover/:userId/lists     → proxy: get HC user lists
GET    /api/abs/:userId/libraries       → proxy: get ABS libraries
GET    /api/abs/:userId/collections     → proxy: get ABS collections for a library
GET    /api/abs/:userId/playlists       → proxy: get ABS playlists
```

---

## 🐳 Docker Deployment

### docker-compose.yml

```yaml
services:
  earmark:
    build: .
    container_name: earmark
    restart: unless-stopped
    ports:
      - "8787:8787"
    volumes:
      - earmark_data:/data
    environment:
      - TZ=Europe/Berlin
      - LOG_LEVEL=info
      # All other config is done via the web GUI
    healthcheck:
      test: ["CMD", "python", "-c", "import httpx; r = httpx.get('http://localhost:8787/api/health'); r.raise_for_status()"]
      interval: 60s
      timeout: 10s
      retries: 3

volumes:
  earmark_data:
```

### Dockerfile

```dockerfile
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

# Data directory (mount as volume)
RUN mkdir -p /data && chown -R earmark:earmark /app /data

USER earmark

EXPOSE 8787

# Entrypoint: run both the web server and the cron scheduler
# Use a small shell script or supervisord-lite approach
COPY entrypoint.sh /app/entrypoint.sh
CMD ["/app/entrypoint.sh"]
```

### entrypoint.sh

```bash
#!/bin/bash
set -e

# Start the web GUI in the background
python -m uvicorn src.main:app --host 0.0.0.0 --port 8787 &

# Start the cron scheduler in the foreground
exec supercronic /app/crontab
```

### crontab

```cron
# Default: sync every 15 minutes (overridable via GUI settings stored in DB)
*/15 * * * * cd /app && python -m src.sync_engine 2>&1
```

### requirements.txt

```
fastapi>=0.115
uvicorn[standard]>=0.32
httpx>=0.28
gql[httpx]>=3.5
tenacity>=9.0
pydantic>=2.10
pydantic-settings>=2.6
python-rapidfuzz>=0.1      # For fuzzy title matching (rapidfuzz wrapper)
rapidfuzz>=3.10
jinja2>=3.1                 # For HTML template rendering
```

---

## 🔐 Security Considerations

- **API tokens** are stored in SQLite. The DB file should be on an encrypted volume in production, or use Docker secrets.
- The web GUI has **no authentication by default** (intended for LAN/VPN use behind a reverse proxy). Add a `GUI_PASSWORD` env var that enables HTTP Basic Auth if set.
- Hardcover tokens are **never exposed** in GET responses — the API returns `"***"` for token fields. They are only accepted on POST/PUT.
- ABS API keys follow the same masking pattern.
- All external API calls use HTTPS.

---

## ⚙️ Configuration (Environment Variables)

These are for bootstrap/override only — primary configuration happens via the web GUI.

| Variable | Default | Description |
|----------|---------|-------------|
| `TZ` | `UTC` | Timezone for cron and log timestamps |
| `LOG_LEVEL` | `info` | Python logging level (debug, info, warning, error) |
| `DATA_DIR` | `/data` | Path to SQLite DB and any cache files |
| `GUI_PASSWORD` | *(none)* | If set, enables HTTP Basic Auth on the web GUI (username: `admin`) |
| `DRY_RUN` | `false` | If true, log all changes but don't execute writes |
| `PORT` | `8787` | Web GUI port |

---

## ✅ Implementation Order

Build in this order to get value early and iterate:

### Phase 1: Core Foundation
1. Project scaffolding (Dockerfile, docker-compose, entrypoint)
2. SQLite database schema + migration logic (`db.py`)
3. Pydantic models (`models.py`)
4. Config loading (`config.py`)

### Phase 2: Platform Clients
5. Hardcover GraphQL client (`platforms/hardcover.py`) — queries + mutations + rate limiting
6. ABS REST client (`platforms/audiobookshelf.py`) — all CRUD endpoints for items, collections, playlists, progress
7. Connection test logic for both platforms

### Phase 3: Matching + Sync Engine
8. Book matcher (`matching/book_matcher.py`) — 3-tier ASIN → ISBN → fuzzy
9. Sync engine (`sync_engine.py`) — HC→ABS list sync + ABS→HC progress sync
10. Sync logging to `sync_log` table

### Phase 4: Web GUI
11. FastAPI routes (`web/routes.py`) — full REST API
12. Frontend SPA (`web/static/`) — dashboard, users, rules, mappings, log, settings
13. "Test Connection" and "Sync Now" features

### Phase 5: Polish
14. HTTP Basic Auth (optional `GUI_PASSWORD`)
15. Log retention cleanup (cron job)
16. Error handling, retry logic, edge cases
17. README with setup instructions

---

## 🧪 Testing Strategy

- **Unit tests**: Book matching logic (various ISBN formats, missing data, fuzzy edge cases)
- **Integration tests**: Mock HTTP responses from both APIs, verify sync state transitions
- **Manual testing**: Docker Compose up, configure via GUI, verify syncs
- No CI/CD initially — manual `docker build` and test

---

## 📝 Edge Cases to Handle

1. **Book exists on HC but not in ABS library** → Log as "unmatched", skip, show in GUI mappings view
2. **Multiple ABS libraries** → Each sync rule is scoped to one library; user can create multiple rules
3. **Duplicate ISBNs** → Use first match; log if ambiguous
4. **HC token expiry** → Detect 401, mark user as "needs token refresh", show alert in GUI
5. **ABS server unreachable** → Retry with backoff, log error, continue to next user
6. **Empty HC list** → If `remove_stale=true`, this removes all books from ABS target — add a safety check: if HC returns 0 books AND previous sync had >0, log warning and skip removal (require manual confirmation)
7. **Rate limit hit** → Exponential backoff via tenacity, max 3 retries, then skip and log
8. **Book on multiple HC lists** → Each sync rule operates independently; same book can be in multiple ABS collections
9. **ABS collection vs playlist permissions** → Collections need admin; if user's ABS token isn't admin, fall back to playlists with a warning