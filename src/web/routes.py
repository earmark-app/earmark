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
    ConnectionTestResult,
    HealthResponse,
    SettingsResponse,
    SettingsUpdate,
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
    """Test both connections and store resolved info on the user record."""
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

    # Test ABS
    abs_client = AudiobookshelfClient(
        base_url=user["abs_url"], api_key=user["abs_api_key"]
    )
    try:
        test_info = await abs_client.test_connection()
        result.abs_ok = True
        result.abs_username = test_info["user"].get("username")
        result.abs_user_id = test_info["user"].get("id")
        result.abs_is_admin = test_info.get("is_admin", False)
        result.abs_libraries = test_info.get("libraries", [])
        db.update_user(user_id, {
            "abs_user_id": test_info["user"].get("id"),
            "abs_username": test_info["user"].get("username"),
            "abs_is_admin": test_info.get("is_admin", False),
        })
    except ABSError as e:
        result.errors.append(f"Audiobookshelf: {e}")
    finally:
        await abs_client.close()

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
    if update_data.get("abs_api_key") == "***":
        del update_data["abs_api_key"]
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
# Proxy: Audiobookshelf
# ---------------------------------------------------------------------------


@router.get("/abs/{user_id}/libraries")
async def proxy_abs_libraries(user_id: str, db: Database = Depends(get_db)):
    user = db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    abs_client = AudiobookshelfClient(
        base_url=user["abs_url"], api_key=user["abs_api_key"]
    )
    try:
        libraries = await abs_client.get_libraries()
        return [{"id": lib.id, "name": lib.name, "mediaType": lib.mediaType}
                for lib in libraries]
    except ABSError as e:
        raise HTTPException(status_code=502, detail=str(e))
    finally:
        await abs_client.close()


@router.get("/abs/{user_id}/collections")
async def proxy_abs_collections(
    user_id: str,
    library_id: str = Query(...),
    db: Database = Depends(get_db),
):
    user = db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    abs_client = AudiobookshelfClient(
        base_url=user["abs_url"], api_key=user["abs_api_key"]
    )
    try:
        collections = await abs_client.get_collections(library_id)
        return [{"id": c.id, "name": c.name, "libraryId": c.libraryId}
                for c in collections]
    except ABSError as e:
        raise HTTPException(status_code=502, detail=str(e))
    finally:
        await abs_client.close()


@router.get("/abs/{user_id}/playlists")
async def proxy_abs_playlists(user_id: str, db: Database = Depends(get_db)):
    user = db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    abs_client = AudiobookshelfClient(
        base_url=user["abs_url"], api_key=user["abs_api_key"]
    )
    try:
        playlists = await abs_client.get_playlists()
        return [{"id": p.id, "name": p.name, "libraryId": p.libraryId}
                for p in playlists]
    except ABSError as e:
        raise HTTPException(status_code=502, detail=str(e))
    finally:
        await abs_client.close()


# ---------------------------------------------------------------------------
# Export / Import
# ---------------------------------------------------------------------------


@router.get("/export")
async def export_config(db: Database = Depends(get_db)):
    """Export users + rules with tokens redacted."""
    users = db.list_users()
    rules = db.list_sync_rules()
    export_users = []
    for u in users:
        export_users.append({
            "name": u["name"],
            "abs_url": u["abs_url"],
            "hardcover_token": "REDACTED",
            "abs_api_key": "REDACTED",
            "abs_library_ids": u.get("abs_library_ids", []),
            "enabled": u.get("enabled", True),
        })
    return {
        "version": "0.1.0",
        "users": export_users,
        "rules": [SyncRuleResponse(**r).model_dump() for r in rules],
    }


@router.post("/import")
async def import_config(request: Request, db: Database = Depends(get_db)):
    """Import users + rules. Tokens accepted if provided, otherwise preserved for matched users."""
    body = await request.json()
    imported_users = 0
    imported_rules = 0

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
            if user_data.get("abs_api_key") != "REDACTED":
                update["abs_api_key"] = user_data["abs_api_key"]
            if user_data.get("abs_url"):
                update["abs_url"] = user_data["abs_url"]
            if update:
                db.update_user(matched["id"], update)
            imported_users += 1
        else:
            if user_data.get("abs_api_key") == "REDACTED":
                continue
            db.create_user({
                "name": user_data["name"],
                "hardcover_token": user_data["hardcover_token"],
                "abs_url": user_data["abs_url"],
                "abs_api_key": user_data["abs_api_key"],
                "abs_library_ids": user_data.get("abs_library_ids", []),
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
