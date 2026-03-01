<p align="center">
  <img src="https://img.shields.io/badge/python-3.12+-blue?logo=python&logoColor=white" alt="Python 3.12+">
  <img src="https://img.shields.io/badge/docker-ready-2496ED?logo=docker&logoColor=white" alt="Docker">
  <img src="https://img.shields.io/badge/license-GPL--3.0-green" alt="License">
  <img src="https://img.shields.io/badge/PRs-welcome-brightgreen" alt="PRs Welcome">
</p>

<h1 align="center">🔖 Earmark</h1>

<p align="center">
  <strong>Bidirectional sync between <a href="https://hardcover.app">Hardcover</a> and <a href="https://www.audiobookshelf.org">Audiobookshelf</a></strong>
  <br />
  <em>Keep your reading lists and progress in sync — automatically.</em>
</p>

<p align="center">
  <a href="#-quick-start">Quick Start</a> ·
  <a href="#-how-it-works">How It Works</a> ·
  <a href="#%EF%B8%8F-configuration">Configuration</a> ·
  <a href="#-api">API</a> ·
  <a href="#-development">Development</a>
</p>

---

## 🚀 Quick Start

```bash
docker compose up -d
```

Open **[http://localhost:8780](http://localhost:8780)** → Add a user → Create a sync rule → Hit **Sync Now**. That's it.

## ✨ What It Does

| Direction | What syncs | Example |
|-----------|-----------|---------|
| **Hardcover → ABS** | Reading lists by status or custom list | "Want to Read" → ABS playlist `hardcover-wtr` |
| **ABS → Hardcover** | Listening progress & finished status | Finished audiobook → HC status "Read" ✅ |

- 👥 **Multi-user** — each user pairs their own HC + ABS accounts
- 🎯 **Flexible rules** — map any HC status/list to any ABS collection or playlist
- 🛡️ **Safe by design** — empty list protection, highest-value-wins, DNF preserved
- 🔄 **Automatic** — cron-based sync every 15 minutes (configurable)
- 🖥️ **Web GUI** — configure everything from the browser, no YAML editing

## 🔍 How It Works

```
         Hardcover                              Audiobookshelf
      ┌──────────────┐                      ┌──────────────────┐
      │ Want to Read │──── ASIN/ISBN/Fuzzy ──▶│  📚 Collection   │
      │ Reading      │     Book Matching     │  🎵 Playlist     │
      │ Read ✅      │◀── Progress Sync ─────│  ▶ Now Playing   │
      └──────────────┘                      └──────────────────┘
                          ┌──────────┐
                          │ 🔖       │
                          │ Earmark  │
                          │ SQLite   │
                          └──────────┘
```

### HC → ABS (List Sync)

1. Fetches books from Hardcover matching the rule's status or list
2. Matches each book to an ABS library item via **ASIN → ISBN → fuzzy title+author**
3. Adds matched books to the target ABS collection or playlist
4. Optionally removes stale books no longer on the HC list

### ABS → HC (Progress Sync)

1. Fetches listening progress from ABS for all mapped books
2. Started audiobooks → HC status **"Currently Reading"**
3. Finished audiobooks → HC status **"Read"**
4. Never decreases progress. Never overwrites **"Did Not Finish"**

### 🛡️ Safety

| Protection | What it prevents |
|-----------|-----------------|
| **Empty list guard** | HC returns 0 books but last sync had >0 → skip removal |
| **Highest-value-wins** | Status/progress never decreases |
| **DNF preserved** | "Did Not Finish" is never overwritten by progress |
| **Sync lock** | Prevents concurrent sync runs from conflicting |
| **Dry run mode** | Log everything without writing to either platform |

## ⚙️ Configuration

All configuration happens in the **web GUI**. Environment variables handle bootstrap only:

| Variable | Default | Description |
|----------|---------|-------------|
| `TZ` | `UTC` | Timezone for cron and logs |
| `LOG_LEVEL` | `info` | `debug` · `info` · `warning` · `error` |
| `DATA_DIR` | `/data` | SQLite DB location |
| `GUI_PASSWORD` | — | Set to enable HTTP Basic Auth (user: `admin`) |
| `DRY_RUN` | `false` | Log changes without executing writes |
| `PORT` | `8780` | Web GUI port |

### Docker Compose

```yaml
services:
  earmark:
    image: ghcr.io/earmark-app/earmark:latest  # or build: .
    container_name: earmark
    restart: unless-stopped
    ports:
      - "8780:8780"
    volumes:
      - ${PATH_CONFIG}/earmark:/data
    environment:
      - TZ=Europe/Berlin
      - GUI_PASSWORD=changeme  # optional
```

### Getting Your Credentials

| Platform | Where to get it |
|----------|----------------|
| **Hardcover** | [hardcover.app/account/api](https://hardcover.app/account/api) → Copy the Bearer token |
| **Audiobookshelf** | ABS Settings → Users → your user → API Keys → Create |

> 💡 Earmark accepts the Hardcover token with or without the `Bearer ` prefix.

## 📡 API

Full REST API at `/api/*` — used by the GUI and available for integrations.

<details>
<summary><strong>Endpoint Reference</strong></summary>

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/health` | Health check + last sync time |
| `GET` | `/api/users` | List users (tokens masked) |
| `POST` | `/api/users` | Create user + auto-test connections |
| `PUT` | `/api/users/:id` | Update user |
| `DELETE` | `/api/users/:id` | Delete user |
| `POST` | `/api/users/:id/test` | Test HC + ABS connections |
| `POST` | `/api/test/hardcover` | Test a HC token (no saved user needed) |
| `POST` | `/api/test/abs` | Test ABS credentials (no saved user needed) |
| `GET` | `/api/rules` | List sync rules |
| `POST` | `/api/rules` | Create rule |
| `PUT` | `/api/rules/:id` | Update rule |
| `DELETE` | `/api/rules/:id` | Delete rule |
| `GET` | `/api/mappings` | List book mappings |
| `POST` | `/api/mappings` | Create manual mapping |
| `DELETE` | `/api/mappings/:id` | Delete mapping |
| `GET` | `/api/log` | Paginated sync log (filterable) |
| `DELETE` | `/api/log` | Clear sync log |
| `POST` | `/api/sync` | Trigger sync (all users) |
| `POST` | `/api/sync/:userId` | Trigger sync (single user) |
| `GET` | `/api/settings` | Global settings |
| `PUT` | `/api/settings` | Update settings |
| `GET` | `/api/export` | Export config (tokens redacted) |
| `POST` | `/api/import` | Import config |

**Proxy endpoints** for live HC/ABS data: `/api/hardcover/:userId/*`, `/api/abs/:userId/*`

</details>

## 🧑‍💻 Development

```bash
# Setup
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt

# Run locally
DATA_DIR=./data LOG_LEVEL=debug uvicorn src.main:app --reload --port 8780

# Run tests
pytest tests/ -v

# Run a single test file
pytest tests/test_matching.py -v

# Build Docker image
docker build -t earmark .
```

### Project Structure

```
src/
├── main.py                 # FastAPI app + lifespan
├── config.py               # Pydantic Settings
├── models.py               # All Pydantic models
├── db.py                   # SQLite manager + CRUD
├── sync_engine.py          # Bidirectional sync orchestrator
├── platforms/
│   ├── hardcover.py        # HC GraphQL client + rate limiter
│   └── audiobookshelf.py   # ABS REST client
├── matching/
│   └── book_matcher.py     # ASIN → ISBN → fuzzy matching
└── web/
    ├── routes.py           # 25 REST API endpoints
    └── static/             # Vanilla JS SPA (Tailwind CSS)
```

### Book Matching

Earmark uses a 3-tier matching strategy to pair Hardcover books with ABS library items:

1. **ASIN** — exact match (confidence: 1.0)
2. **ISBN** — isbn_13/isbn_10 across all editions (confidence: 1.0)
3. **Fuzzy** — `rapidfuzz` token sort ratio on `"title author"` (threshold: 0.85)

Unmatched books are logged and visible in the Mappings view. You can also create manual mappings.

## ❓ Troubleshooting

| Issue | Solution |
|-------|----------|
| **HC 401 errors** | Token expired → get a new one at [hardcover.app/account/api](https://hardcover.app/account/api) |
| **Collection creation fails** | Collections require ABS admin keys → use playlists instead |
| **Sync not running** | Check sync log for errors. Verify both connections pass "Test Connection" |
| **Books not matching** | Check Mappings page. Low-confidence fuzzy matches (< 0.85) are rejected. Create manual mappings if needed |
| **Sync already in progress** | Wait for the current sync to finish, or restart the container |

## 📄 License

[GPL-3.0](LICENSE) — because audiobook lovers deserve open source.

---

<p align="center">
  <sub>Built with ❤️ for the audiobook community</sub>
</p>
