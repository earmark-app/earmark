"""Earmark REST API routes — 22+ endpoints for GUI and external use."""
from __future__ import annotations

import asyncio
import json
import logging
import secrets
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from src.config import settings
from src.db import Database
from src.models import (
    BookMappingCreate,
    BookMappingResponse,
    BookRatingCreate,
    BookRatingResponse,
    ConnectionTestResult,
    HealthResponse,
    ReadingDatesResponse,
    SettingsResponse,
    SettingsUpdate,
    StatsResponse,
    SyncLogEntry,
    SyncLogResponse,
    SyncRuleCreate,
    SyncRuleResponse,
    SyncRuleUpdate,
    UserCreate,
    UserDB,
    UserResponse,
    UserUpdate,
)
from src.platforms.audiobookshelf import AudiobookshelfClient, ABSError
from src.platforms.hardcover import HardcoverClient, HardcoverError
from src.sync_engine import SyncEngine

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")
security = HTTPBasic(auto_error=False)


# ---------------------------------------------------------------------------
# Dependency: database
# ---------------------------------------------------------------------------

_db: Optional[Database] = None


def set_db(db: Database):
    global _db
    _db = db


def get_db() -> Database:
    if _db is None:
        raise RuntimeError("Database not initialized")
    return _db


# ---------------------------------------------------------------------------
# Auth middleware (HTTP Basic when GUI_PASSWORD is set)
# ---------------------------------------------------------------------------


async def check_auth(
    request: Request,
    credentials: Optional[HTTPBasicCredentials] = Depends(security),
):
    if not settings.gui_password:
        return
    # Allow health check without auth
    if request.url.path == "/api/health":
        return
    if credentials is None:
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Basic"},
        )
    if not (
        secrets.compare_digest(credentials.username.encode(), b"admin")
        and secrets.compare_digest(
            credentials.password.encode(), settings.gui_password.encode()
        )
    ):
        raise HTTPException(
            status_code=401,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )


# ---------------------------------------------------------------------------
# Sync state tracking (for background sync)
# ---------------------------------------------------------------------------

_sync_running = False


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------


async def _auto_test_connections(user_id: str, db: Database) -> ConnectionTestResult:
    """Test Hardcover connection and store resolved info on the user record."""
    user = db.get_user(user_id)
    if not user:
        return ConnectionTestResult()

    result = ConnectionTestResult()

    # Test Hardcover
    hc = HardcoverClient(token=user["hardcover_token"])
    try:
        me = await hc.test_connection()
        result.hardcover_ok = True
        result.hardcover_username = me.get("username")
        result.hardcover_user_id = me.get("id")
        db.update_user(user_id, {
            "hardcover_user_id": me.get("id"),
            "hardcover_username": me.get("username"),
            "needs_token_refresh": False,
        })
    except HardcoverError as e:
        result.errors.append(f"Hardcover: {e}")
    finally:
        await hc.close()

    return result


@router.get("/users", response_model=list[UserResponse])
async def list_users(db: Database = Depends(get_db)):
    users = db.list_users()
    return [UserDB(**u).to_response() for u in users]


@router.post("/users", response_model=UserResponse, status_code=201)
async def create_user(data: UserCreate, db: Database = Depends(get_db)):
    user = db.create_user(data.model_dump())
    return UserDB(**user).to_response()


@router.put("/users/{user_id}", response_model=UserResponse)
async def update_user(user_id: str, data: UserUpdate, db: Database = Depends(get_db)):
    existing = db.get_user(user_id)
    if not existing:
        raise HTTPException(status_code=404, detail="User not found")
    # Skip masked token values
    update_data = data.model_dump(exclude_none=True)
    if update_data.get("hardcover_token") == "***":
        del update_data["hardcover_token"]
    user = db.update_user(user_id, update_data)
    return UserDB(**user).to_response()


@router.delete("/users/{user_id}")
async def delete_user(user_id: str, db: Database = Depends(get_db)):
    if not db.delete_user(user_id):
        raise HTTPException(status_code=404, detail="User not found")
    return {"ok": True}


@router.post("/test/hardcover")
async def test_hardcover_token(request: Request):
    """Test a Hardcover token without requiring a saved user."""
    body = await request.json()
    token = body.get("token", "").strip()
    if not token:
        raise HTTPException(status_code=400, detail="Token is required")
    # Support both "Bearer ey..." and bare "ey..." formats
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    hc = HardcoverClient(token=token)
    try:
        me = await hc.test_connection()
        return {
            "ok": True,
            "username": me.get("username"),
            "user_id": me.get("id"),
        }
    except HardcoverError as e:
        return {"ok": False, "error": str(e)}
    finally:
        await hc.close()


@router.post("/test/abs")
async def test_abs_connection(request: Request):
    """Test an ABS connection without requiring a saved user."""
    body = await request.json()
    url = body.get("url", "").strip()
    api_key = body.get("api_key", "").strip()
    if not url or not api_key:
        raise HTTPException(status_code=400, detail="URL and API key are required")
    abs_client = AudiobookshelfClient(base_url=url, api_key=api_key)
    try:
        test_info = await abs_client.test_connection()
        return {
            "ok": True,
            "username": test_info["user"].get("username"),
            "user_id": test_info["user"].get("id"),
            "is_admin": test_info.get("is_admin", False),
            "libraries": test_info.get("libraries", []),
        }
    except ABSError as e:
        return {"ok": False, "error": str(e)}
    finally:
        await abs_client.close()


@router.post("/users/{user_id}/test", response_model=ConnectionTestResult)
async def test_user_connection(user_id: str, db: Database = Depends(get_db)):
    if not db.get_user(user_id):
        raise HTTPException(status_code=404, detail="User not found")
    return await _auto_test_connections(user_id, db)


# ---------------------------------------------------------------------------
# Sync Rules
# ---------------------------------------------------------------------------


@router.get("/rules", response_model=list[SyncRuleResponse])
async def list_rules(
    user_id: Optional[str] = Query(None), db: Database = Depends(get_db)
):
    rules = db.list_sync_rules(user_id=user_id)
    return [SyncRuleResponse(**r) for r in rules]


@router.post("/rules", response_model=SyncRuleResponse, status_code=201)
async def create_rule(data: SyncRuleCreate, db: Database = Depends(get_db)):
    # Validate user exists
    if not db.get_user(data.user_id):
        raise HTTPException(status_code=404, detail="User not found")
    rule = db.create_sync_rule(data.model_dump())
    return SyncRuleResponse(**rule)


@router.put("/rules/{rule_id}", response_model=SyncRuleResponse)
async def update_rule(
    rule_id: str, data: SyncRuleUpdate, db: Database = Depends(get_db)
):
    existing = db.get_sync_rule(rule_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Rule not found")
    rule = db.update_sync_rule(rule_id, data.model_dump(exclude_none=True))
    return SyncRuleResponse(**rule)


@router.delete("/rules/{rule_id}")
async def delete_rule(rule_id: str, db: Database = Depends(get_db)):
    if not db.delete_sync_rule(rule_id):
        raise HTTPException(status_code=404, detail="Rule not found")
    return {"ok": True}


# ---------------------------------------------------------------------------
# Book Mappings
# ---------------------------------------------------------------------------


@router.get("/mappings", response_model=list[BookMappingResponse])
async def list_mappings(
    user_id: Optional[str] = Query(None),
    method: Optional[str] = Query(None),
    db: Database = Depends(get_db),
):
    mappings = db.list_book_mappings(user_id=user_id, method=method)
    return [BookMappingResponse(**m) for m in mappings]


@router.post("/mappings", response_model=BookMappingResponse, status_code=201)
async def create_mapping(data: BookMappingCreate, db: Database = Depends(get_db)):
    if not db.get_user(data.user_id):
        raise HTTPException(status_code=404, detail="User not found")
    mapping = db.create_book_mapping(data.model_dump())
    return BookMappingResponse(**mapping)


@router.delete("/mappings/{mapping_id}")
async def delete_mapping(mapping_id: str, db: Database = Depends(get_db)):
    if not db.delete_book_mapping(mapping_id):
        raise HTTPException(status_code=404, detail="Mapping not found")
    return {"ok": True}


# ---------------------------------------------------------------------------
# Sync Log
# ---------------------------------------------------------------------------


@router.get("/log", response_model=SyncLogResponse)
async def list_log(
    user_id: Optional[str] = Query(None),
    action: Optional[str] = Query(None),
    direction: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Database = Depends(get_db),
):
    entries, total = db.list_sync_log(
        user_id=user_id, action=action, direction=direction,
        limit=limit, offset=offset,
    )
    return SyncLogResponse(
        entries=[SyncLogEntry(**e) for e in entries],
        total=total,
        page=offset // limit,
        limit=limit,
    )


@router.delete("/log")
async def clear_log(
    before: Optional[str] = Query(None),
    db: Database = Depends(get_db),
):
    deleted = db.delete_sync_log(before_date=before)
    return {"deleted": deleted}


# ---------------------------------------------------------------------------
# Sync Trigger
# ---------------------------------------------------------------------------


async def _run_sync(db: Database, user_id: Optional[str] = None):
    """Run sync in background task."""
    global _sync_running
    try:
        engine = SyncEngine(db, settings)
        if user_id:
            await engine.run_user(user_id)
        else:
            await engine.run_all()
    except Exception:
        logger.exception("Background sync failed")
    finally:
        _sync_running = False


@router.post("/sync")
async def trigger_sync(
    background_tasks: BackgroundTasks, db: Database = Depends(get_db)
):
    global _sync_running
    if _sync_running:
        raise HTTPException(status_code=409, detail="Sync already in progress")
    _sync_running = True
    background_tasks.add_task(_run_sync, db)
    return {"status": "started", "message": "Sync started for all users"}


@router.post("/sync/{user_id}")
async def trigger_user_sync(
    user_id: str, background_tasks: BackgroundTasks, db: Database = Depends(get_db)
):
    if not db.get_user(user_id):
        raise HTTPException(status_code=404, detail="User not found")
    global _sync_running
    if _sync_running:
        raise HTTPException(status_code=409, detail="Sync already in progress")
    _sync_running = True
    background_tasks.add_task(_run_sync, db, user_id)
    return {"status": "started", "message": f"Sync started for user {user_id}"}


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


@router.get("/settings", response_model=SettingsResponse)
async def get_settings(db: Database = Depends(get_db)):
    raw = db.get_all_settings()
    return SettingsResponse(
        sync_interval=raw.get("sync_interval", "*/15 * * * *"),
        dry_run=raw.get("dry_run", "false").lower() == "true",
        log_retention_days=int(raw.get("log_retention_days", "30")),
        fuzzy_match_threshold=float(raw.get("fuzzy_match_threshold", "0.85")),
        sync_ratings_to_abs_tags=raw.get("sync_ratings_to_abs_tags", "false").lower() == "true",
        abs_url=raw.get("abs_url", ""),
        abs_api_key="***" if raw.get("abs_api_key") else "",
    )


@router.put("/settings", response_model=SettingsResponse)
async def update_settings(data: SettingsUpdate, db: Database = Depends(get_db)):
    updates = {}
    if data.dry_run is not None:
        updates["dry_run"] = str(data.dry_run).lower()
    if data.log_retention_days is not None:
        updates["log_retention_days"] = str(data.log_retention_days)
    if data.fuzzy_match_threshold is not None:
        updates["fuzzy_match_threshold"] = str(data.fuzzy_match_threshold)
    if data.sync_ratings_to_abs_tags is not None:
        updates["sync_ratings_to_abs_tags"] = str(data.sync_ratings_to_abs_tags).lower()
    if data.abs_url is not None:
        updates["abs_url"] = data.abs_url
    if data.abs_api_key is not None and data.abs_api_key != "***":
        updates["abs_api_key"] = data.abs_api_key
    # sync_interval is read-only in v1 (requires container restart)
    if updates:
        db.update_settings(updates)
    return await get_settings(db=db)


# ---------------------------------------------------------------------------
# Proxy: Hardcover
# ---------------------------------------------------------------------------


@router.get("/hardcover/{user_id}/statuses")
async def proxy_hc_statuses(user_id: str, db: Database = Depends(get_db)):
    user = db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    hc = HardcoverClient(token=user["hardcover_token"])
    try:
        all_books = await hc.get_user_books()
        grouped: dict[int, list] = {}
        for ub in all_books:
            grouped.setdefault(ub.status_id, []).append({
                "id": ub.id,
                "status_id": ub.status_id,
                "book_id": ub.book.id,
                "title": ub.book.title,
                "author": ub.book.author,
            })
        return grouped
    except HardcoverError as e:
        raise HTTPException(status_code=502, detail=str(e))
    finally:
        await hc.close()


@router.get("/hardcover/{user_id}/lists")
async def proxy_hc_lists(user_id: str, db: Database = Depends(get_db)):
    user = db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    hc = HardcoverClient(token=user["hardcover_token"])
    try:
        lists = await hc.get_lists()
        return lists
    except HardcoverError as e:
        raise HTTPException(status_code=502, detail=str(e))
    finally:
        await hc.close()


# ---------------------------------------------------------------------------
# Global ABS endpoints (use admin token from settings)
# ---------------------------------------------------------------------------


def _get_abs_client(db: Database) -> AudiobookshelfClient:
    """Create an ABS client from global settings."""
    abs_url = db.get_setting("abs_url") or ""
    abs_api_key = db.get_setting("abs_api_key") or ""
    if not abs_url or not abs_api_key:
        raise HTTPException(status_code=400, detail="ABS not configured in settings")
    return AudiobookshelfClient(base_url=abs_url, api_key=abs_api_key)


@router.get("/abs/libraries")
async def list_abs_libraries(db: Database = Depends(get_db)):
    """List ABS libraries using global admin token."""
    abs_client = _get_abs_client(db)
    try:
        libraries = await abs_client.get_libraries()
        return [{"id": lib.id, "name": lib.name, "mediaType": lib.mediaType}
                for lib in libraries]
    except ABSError as e:
        raise HTTPException(status_code=502, detail=str(e))
    finally:
        await abs_client.close()


@router.get("/abs/users")
async def list_abs_users(db: Database = Depends(get_db)):
    """List ABS users using global admin token (no tokens exposed)."""
    abs_client = _get_abs_client(db)
    try:
        users = await abs_client.get_users()
        return [
            {"id": u.get("id"), "username": u.get("username"), "type": u.get("type")}
            for u in users
        ]
    except ABSError as e:
        raise HTTPException(status_code=502, detail=str(e))
    finally:
        await abs_client.close()


# ---------------------------------------------------------------------------
# Ratings
# ---------------------------------------------------------------------------


@router.get("/ratings", response_model=list[BookRatingResponse])
async def list_ratings(
    user_id: Optional[str] = Query(None), db: Database = Depends(get_db)
):
    ratings = db.list_book_ratings(user_id=user_id)
    return [BookRatingResponse(**r) for r in ratings]


@router.post("/ratings", response_model=BookRatingResponse, status_code=201)
async def create_rating(data: BookRatingCreate, db: Database = Depends(get_db)):
    """Manually rate a book (pushes to HC on next sync)."""
    if not db.get_user(data.user_id):
        raise HTTPException(status_code=404, detail="User not found")
    result = db.upsert_book_rating(
        user_id=data.user_id,
        hardcover_book_id=data.hardcover_book_id,
        rating=data.rating,
        source="earmark",
    )
    return BookRatingResponse(**result)


@router.get("/ratings/summary")
async def ratings_summary(
    user_id: Optional[str] = Query(None), db: Database = Depends(get_db)
):
    ratings = db.list_book_ratings(user_id=user_id)
    if not ratings:
        return {"total": 0, "avg": None, "distribution": {}}
    values = [r["rating"] for r in ratings if r.get("rating")]
    distribution: dict[str, int] = {}
    for v in values:
        bucket = str(round(v * 2) / 2)  # round to 0.5 increments
        distribution[bucket] = distribution.get(bucket, 0) + 1
    return {
        "total": len(values),
        "avg": round(sum(values) / len(values), 2) if values else None,
        "distribution": distribution,
    }


# ---------------------------------------------------------------------------
# Reading Dates
# ---------------------------------------------------------------------------


@router.get("/dates", response_model=list[ReadingDatesResponse])
async def list_dates(
    user_id: Optional[str] = Query(None), db: Database = Depends(get_db)
):
    dates = db.list_reading_dates(user_id=user_id)
    return [ReadingDatesResponse(**d) for d in dates]


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


@router.get("/stats/{user_id}")
async def get_stats(user_id: str, db: Database = Depends(get_db)):
    user = db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Earmark DB stats
    mappings = db.list_book_mappings(user_id=user_id)
    ratings = db.list_book_ratings(user_id=user_id)
    dates = db.list_reading_dates(user_id=user_id)
    rating_values = [r["rating"] for r in ratings if r.get("rating")]

    # ABS stats (use global admin token)
    abs_url = db.get_setting("abs_url") or ""
    abs_api_key = db.get_setting("abs_api_key") or ""
    abs_client = AudiobookshelfClient(base_url=abs_url, api_key=abs_api_key) if abs_url and abs_api_key else None
    listening_time = 0.0
    abs_finished = 0
    abs_in_progress = 0
    hc_status_counts: dict[str, int] = {}

    if abs_client:
        try:
            me_data = await abs_client.get_me()
            for p in me_data.get("mediaProgress", []):
                if p.get("isFinished"):
                    abs_finished += 1
                elif p.get("progress", 0) > 0:
                    abs_in_progress += 1
                listening_time += p.get("currentTime", 0)
        except ABSError:
            pass
        finally:
            await abs_client.close()

    # HC status counts
    hc_client = HardcoverClient(token=user["hardcover_token"])
    try:
        all_books = await hc_client.get_user_books()
        for ub in all_books:
            status_name = {1: "Want to Read", 2: "Currently Reading", 3: "Read", 5: "DNF"}.get(
                ub.status_id, f"Status {ub.status_id}"
            )
            hc_status_counts[status_name] = hc_status_counts.get(status_name, 0) + 1
    except HardcoverError:
        pass
    finally:
        await hc_client.close()

    return StatsResponse(
        user_id=user_id,
        hc_status_counts=hc_status_counts,
        total_mapped_books=len(mappings),
        total_ratings=len(rating_values),
        avg_rating=round(sum(rating_values) / len(rating_values), 2) if rating_values else None,
        books_with_dates=len(dates),
        listening_time_hours=round(listening_time / 3600, 1),
        abs_books_finished=abs_finished,
        abs_books_in_progress=abs_in_progress,
    )


@router.get("/stats/{user_id}/sessions")
async def get_sessions(user_id: str, db: Database = Depends(get_db)):
    user = db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    abs_client = _get_abs_client(db)
    try:
        sessions = await abs_client.get_listening_sessions(limit=50)
        return sessions
    except ABSError as e:
        raise HTTPException(status_code=502, detail=str(e))
    finally:
        await abs_client.close()


# ---------------------------------------------------------------------------
# Export / Import
# ---------------------------------------------------------------------------


@router.get("/export")
async def export_config(db: Database = Depends(get_db)):
    """Export users + rules + settings with tokens redacted."""
    users = db.list_users()
    rules = db.list_sync_rules()
    raw_settings = db.get_all_settings()
    export_users = []
    for u in users:
        export_users.append({
            "name": u["name"],
            "hardcover_token": "REDACTED",
            "abs_user_id": u.get("abs_user_id"),
            "enabled": u.get("enabled", True),
        })
    export_settings = {k: v for k, v in raw_settings.items() if k != "abs_api_key"}
    export_settings["abs_api_key"] = "REDACTED"
    return {
        "version": "0.2.0",
        "users": export_users,
        "rules": [SyncRuleResponse(**r).model_dump() for r in rules],
        "settings": export_settings,
    }


@router.post("/import")
async def import_config(request: Request, db: Database = Depends(get_db)):
    """Import users + rules + settings. Tokens accepted if provided, otherwise preserved."""
    body = await request.json()
    imported_users = 0
    imported_rules = 0

    # Import settings (if present)
    if "settings" in body:
        settings_updates = {}
        for k, v in body["settings"].items():
            if k == "abs_api_key" and v == "REDACTED":
                continue
            settings_updates[k] = v
        if settings_updates:
            db.update_settings(settings_updates)

    for user_data in body.get("users", []):
        # Skip if tokens are redacted and no existing user matches
        if user_data.get("hardcover_token") == "REDACTED":
            continue
        existing_users = db.list_users()
        matched = next(
            (u for u in existing_users if u["name"] == user_data["name"]),
            None,
        )
        if matched:
            update = {}
            if user_data.get("hardcover_token") != "REDACTED":
                update["hardcover_token"] = user_data["hardcover_token"]
            if user_data.get("abs_user_id"):
                update["abs_user_id"] = user_data["abs_user_id"]
            if update:
                db.update_user(matched["id"], update)
            imported_users += 1
        else:
            db.create_user({
                "name": user_data["name"],
                "hardcover_token": user_data["hardcover_token"],
                "abs_user_id": user_data.get("abs_user_id"),
                "enabled": user_data.get("enabled", True),
            })
            imported_users += 1

    for rule_data in body.get("rules", []):
        if "user_id" in rule_data:
            try:
                db.create_sync_rule(rule_data)
                imported_rules += 1
            except Exception:
                logger.warning("Failed to import rule: %s", rule_data)

    return {"imported_users": imported_users, "imported_rules": imported_rules}
