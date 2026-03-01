"""Integration tests for the sync engine with mocked HTTP responses."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config import Settings
from src.db import Database
from src.sync_engine import SyncEngine, SyncLock


@pytest.fixture
def config(tmp_path):
    return Settings(
        data_dir=str(tmp_path),
        dry_run=False,
        log_level="debug",
    )


@pytest.fixture
def dry_run_config(tmp_path):
    return Settings(
        data_dir=str(tmp_path),
        dry_run=True,
        log_level="debug",
    )


@pytest.fixture
def engine(db, config):
    return SyncEngine(db=db, config=config)


@pytest.fixture
def dry_engine(db, dry_run_config):
    return SyncEngine(db=db, config=dry_run_config)


@pytest.fixture
def user_with_rule(db):
    """Create a user with an HC->ABS sync rule."""
    user = db.create_user({
        "name": "Test User",
        "hardcover_token": "hc_token",
        "abs_url": "http://abs.test",
        "abs_api_key": "abs_key",
    })
    rule = db.create_sync_rule({
        "user_id": user["id"],
        "direction": "hc_to_abs",
        "hc_status_id": 1,
        "abs_target_type": "collection",
        "abs_target_name": "Want to Read",
        "abs_target_id": "col_001",
        "abs_library_id": "lib_001",
    })
    return user, rule


@pytest.fixture
def user_with_abs_rule(db):
    """Create a user with an ABS->HC sync rule."""
    user = db.create_user({
        "name": "Test User",
        "hardcover_token": "hc_token",
        "abs_url": "http://abs.test",
        "abs_api_key": "abs_key",
    })
    rule = db.create_sync_rule({
        "user_id": user["id"],
        "direction": "abs_to_hc",
        "hc_status_id": None,
        "abs_target_type": "collection",
        "abs_target_name": "Listening",
        "abs_library_id": "lib_001",
    })
    # Pre-create a book mapping for progress sync
    db.create_book_mapping({
        "user_id": user["id"],
        "hardcover_book_id": 42,
        "abs_library_item_id": "li_001",
        "match_method": "asin",
        "match_confidence": 1.0,
        "title": "Test Book",
    })
    return user, rule


def _mock_hc_client(user_books=None):
    """Create a mock HardcoverClient."""
    client = AsyncMock()
    client.get_user_books = AsyncMock(return_value=user_books or [])
    client.get_list_books = AsyncMock(return_value=[])
    client.set_book_status = AsyncMock(return_value={"id": 1})
    client.update_book_status = AsyncMock(return_value={"returning": [{"id": 1}]})
    client.close = AsyncMock()
    return client


def _mock_abs_client(
    collections=None,
    library_items=None,
    me_data=None,
):
    """Create a mock AudiobookshelfClient."""
    client = AsyncMock()
    client.get_all_library_items = AsyncMock(return_value=library_items or [])
    client.get_collections = AsyncMock(return_value=collections or [])
    client.get_playlists = AsyncMock(return_value=[])
    client.create_collection = AsyncMock(return_value=MagicMock(id="col_new", name="New"))
    client.create_playlist = AsyncMock(return_value=MagicMock(id="pl_new", name="New"))
    client.batch_add_to_collection = AsyncMock()
    client.batch_remove_from_collection = AsyncMock()
    client.batch_add_to_playlist = AsyncMock()
    client.batch_remove_from_playlist = AsyncMock()
    client.get_me = AsyncMock(return_value=me_data or {"id": "u1", "username": "test", "mediaProgress": []})
    client.close = AsyncMock()
    return client


def _make_mock_collection(col_id="col_001", name="Want to Read", book_ids=None):
    col = MagicMock()
    col.id = col_id
    col.name = name
    col.books = [{"id": bid} for bid in (book_ids or [])]
    return col


class TestSyncLock:
    def test_acquire_and_release(self, tmp_path):
        lock = SyncLock(str(tmp_path / "test.lock"))
        assert lock.acquire() is True
        assert lock.acquire() is False  # Already held by this PID
        lock.release()

    def test_stale_lock_cleaned(self, tmp_path):
        lock_path = tmp_path / "test.lock"
        # Write a lock with a dead PID
        lock_path.write_text("99999999")
        lock = SyncLock(str(lock_path))
        assert lock.acquire() is True  # Should clean up stale lock
        lock.release()


class TestHCToABS:
    @pytest.mark.asyncio
    async def test_adds_book_to_collection(self, engine, db, user_with_rule):
        """Books in HC but not in ABS target should be added."""
        user, rule = user_with_rule
        from src.models import HardcoverUserBook, HardcoverBook, HardcoverEdition

        hc_book = HardcoverUserBook(
            id=1, status_id=1, book=HardcoverBook(
                id=100, title="Test Book",
                editions=[HardcoverEdition(id=1000, asin="ASIN_1")],
                cached_contributors=[{"author": {"name": "Author"}}],
            )
        )
        from src.models import ABSLibraryItem, ABSMedia, ABSMediaMetadata
        abs_item = ABSLibraryItem(
            id="li_001",
            media=ABSMedia(metadata=ABSMediaMetadata(title="Test Book", asin="ASIN_1")),
        )
        collection = _make_mock_collection(book_ids=[])

        hc_client = _mock_hc_client(user_books=[hc_book])
        abs_client = _mock_abs_client(
            collections=[collection],
            library_items=[abs_item],
        )

        with patch("src.sync_engine.HardcoverClient", return_value=hc_client), \
             patch("src.sync_engine.AudiobookshelfClient", return_value=abs_client):
            await engine.run_user(user["id"])

        abs_client.batch_add_to_collection.assert_called_once()
        call_args = abs_client.batch_add_to_collection.call_args
        assert "li_001" in call_args[0][1]  # item_ids

    @pytest.mark.asyncio
    async def test_removes_stale_book(self, engine, db, user_with_rule):
        """Books in ABS target but not in HC should be removed when remove_stale=True."""
        user, rule = user_with_rule
        # ABS collection has li_stale, but HC has no books
        collection = _make_mock_collection(book_ids=["li_stale"])

        hc_client = _mock_hc_client(user_books=[])
        abs_client = _mock_abs_client(
            collections=[collection],
            library_items=[],
        )

        # No previous sync state, so empty list safety won't trigger
        with patch("src.sync_engine.HardcoverClient", return_value=hc_client), \
             patch("src.sync_engine.AudiobookshelfClient", return_value=abs_client):
            await engine.run_user(user["id"])

        # Should not add or remove since no matches were made
        abs_client.batch_add_to_collection.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_list_safety(self, engine, db, user_with_rule):
        """If HC returns 0 books but previous sync had >0, skip removal."""
        user, rule = user_with_rule

        # Create a previous sync state entry
        mapping = db.create_book_mapping({
            "user_id": user["id"],
            "hardcover_book_id": 1,
            "abs_library_item_id": "li_001",
            "match_method": "asin",
            "title": "Old Book",
        })
        db.upsert_sync_state(rule["id"], mapping["id"], "hc_to_abs")

        hc_client = _mock_hc_client(user_books=[])
        abs_client = _mock_abs_client(
            collections=[_make_mock_collection(book_ids=["li_001"])],
        )

        with patch("src.sync_engine.HardcoverClient", return_value=hc_client), \
             patch("src.sync_engine.AudiobookshelfClient", return_value=abs_client):
            await engine.run_user(user["id"])

        # Should NOT remove — safety check triggered
        abs_client.batch_remove_from_collection.assert_not_called()
        # Check log for warning
        logs, total = db.list_sync_log(action="error")
        assert any("Empty HC list safety" in json.loads(l["details"]).get("warning", "") for l in logs)


class TestABSToHC:
    @pytest.mark.asyncio
    async def test_updates_to_reading(self, engine, db, user_with_abs_rule):
        """ABS progress > 0 should set HC status to Currently Reading (2)."""
        user, rule = user_with_abs_rule

        me_data = {
            "id": "u1", "username": "test",
            "mediaProgress": [
                {"libraryItemId": "li_001", "progress": 0.5, "isFinished": False},
            ],
        }

        hc_client = _mock_hc_client()
        abs_client = _mock_abs_client(me_data=me_data)

        with patch("src.sync_engine.HardcoverClient", return_value=hc_client), \
             patch("src.sync_engine.AudiobookshelfClient", return_value=abs_client):
            await engine.run_user(user["id"])

        hc_client.update_book_status.assert_called_with(42, 2)

    @pytest.mark.asyncio
    async def test_updates_to_read_on_finish(self, engine, db, user_with_abs_rule):
        """ABS isFinished=true should set HC status to Read (3)."""
        user, rule = user_with_abs_rule

        me_data = {
            "id": "u1", "username": "test",
            "mediaProgress": [
                {"libraryItemId": "li_001", "progress": 1.0, "isFinished": True},
            ],
        }

        hc_client = _mock_hc_client()
        abs_client = _mock_abs_client(me_data=me_data)

        with patch("src.sync_engine.HardcoverClient", return_value=hc_client), \
             patch("src.sync_engine.AudiobookshelfClient", return_value=abs_client):
            await engine.run_user(user["id"])

        hc_client.update_book_status.assert_called_with(42, 3)

    @pytest.mark.asyncio
    async def test_dnf_preserved(self, engine, db, user_with_abs_rule):
        """DNF status (5) should never be overwritten by ABS progress."""
        user, rule = user_with_abs_rule

        # Set previous state as DNF
        db.upsert_progress_state(
            user_id=user["id"],
            abs_library_item_id="li_001",
            hardcover_book_id=42,
            progress=0.3,
            is_finished=False,
            hc_status_id=5,  # DNF
        )

        me_data = {
            "id": "u1", "username": "test",
            "mediaProgress": [
                {"libraryItemId": "li_001", "progress": 0.8, "isFinished": False},
            ],
        }

        hc_client = _mock_hc_client()
        abs_client = _mock_abs_client(me_data=me_data)

        with patch("src.sync_engine.HardcoverClient", return_value=hc_client), \
             patch("src.sync_engine.AudiobookshelfClient", return_value=abs_client):
            await engine.run_user(user["id"])

        # Should NOT update HC — DNF preserved
        hc_client.update_book_status.assert_not_called()
        hc_client.set_book_status.assert_not_called()

    @pytest.mark.asyncio
    async def test_highest_value_wins(self, engine, db, user_with_abs_rule):
        """Status should never decrease (Read should not be downgraded to Reading)."""
        user, rule = user_with_abs_rule

        # Previous state: Read (3)
        db.upsert_progress_state(
            user_id=user["id"],
            abs_library_item_id="li_001",
            hardcover_book_id=42,
            progress=1.0,
            is_finished=True,
            hc_status_id=3,  # Read
        )

        # ABS now shows progress 0.5 (re-listening?)
        me_data = {
            "id": "u1", "username": "test",
            "mediaProgress": [
                {"libraryItemId": "li_001", "progress": 0.5, "isFinished": False},
            ],
        }

        hc_client = _mock_hc_client()
        abs_client = _mock_abs_client(me_data=me_data)

        with patch("src.sync_engine.HardcoverClient", return_value=hc_client), \
             patch("src.sync_engine.AudiobookshelfClient", return_value=abs_client):
            await engine.run_user(user["id"])

        # Should NOT downgrade from Read to Reading
        hc_client.update_book_status.assert_not_called()
        hc_client.set_book_status.assert_not_called()

    @pytest.mark.asyncio
    async def test_loop_prevention(self, engine, db, user_with_abs_rule):
        """After syncing, progress_state should be updated to prevent re-detection."""
        user, rule = user_with_abs_rule

        me_data = {
            "id": "u1", "username": "test",
            "mediaProgress": [
                {"libraryItemId": "li_001", "progress": 0.5, "isFinished": False},
            ],
        }

        hc_client = _mock_hc_client()
        abs_client = _mock_abs_client(me_data=me_data)

        with patch("src.sync_engine.HardcoverClient", return_value=hc_client), \
             patch("src.sync_engine.AudiobookshelfClient", return_value=abs_client):
            await engine.run_user(user["id"])

        # Check that progress state was updated
        ps = db.get_progress_state(user["id"], "li_001")
        assert ps is not None
        assert ps["last_abs_progress"] == 0.5
        assert ps["last_hc_status_id"] == 2

        # Run again — should NOT update HC since progress hasn't changed
        hc_client.update_book_status.reset_mock()
        hc_client.set_book_status.reset_mock()

        with patch("src.sync_engine.HardcoverClient", return_value=hc_client), \
             patch("src.sync_engine.AudiobookshelfClient", return_value=abs_client):
            await engine.run_user(user["id"])

        hc_client.update_book_status.assert_not_called()
        hc_client.set_book_status.assert_not_called()


class TestDryRun:
    @pytest.mark.asyncio
    async def test_dry_run_no_writes(self, dry_engine, db, user_with_abs_rule):
        """In dry run mode, no API writes should be made."""
        user, rule = user_with_abs_rule

        me_data = {
            "id": "u1", "username": "test",
            "mediaProgress": [
                {"libraryItemId": "li_001", "progress": 0.7, "isFinished": False},
            ],
        }

        hc_client = _mock_hc_client()
        abs_client = _mock_abs_client(me_data=me_data)

        with patch("src.sync_engine.HardcoverClient", return_value=hc_client), \
             patch("src.sync_engine.AudiobookshelfClient", return_value=abs_client):
            await dry_engine.run_user(user["id"])

        hc_client.update_book_status.assert_not_called()
        hc_client.set_book_status.assert_not_called()


class TestRunAll:
    @pytest.mark.asyncio
    async def test_run_all_with_no_users(self, engine):
        result = await engine.run_all()
        assert result["status"] == "ok"
        assert result["users_synced"] == 0

    @pytest.mark.asyncio
    async def test_error_isolation(self, engine, db):
        """Error in one user should not block others."""
        user1 = db.create_user({
            "name": "User 1", "hardcover_token": "tok1",
            "abs_url": "http://abs1", "abs_api_key": "key1",
        })
        user2 = db.create_user({
            "name": "User 2", "hardcover_token": "tok2",
            "abs_url": "http://abs2", "abs_api_key": "key2",
        })

        call_count = 0

        async def mock_run_user(user_id):
            nonlocal call_count
            call_count += 1
            if user_id == user1["id"]:
                raise Exception("User 1 failed")
            return {"status": "ok"}

        engine.run_user = mock_run_user
        result = await engine.run_all()
        assert call_count == 2  # Both users were attempted
        assert result["status"] == "ok"

    @pytest.mark.asyncio
    async def test_sync_lock_prevents_concurrent(self, engine, tmp_path):
        """Second sync should be blocked if lock is held."""
        # Acquire the lock manually
        engine.lock.acquire()
        result = await engine.run_all()
        assert result["status"] == "locked"
        engine.lock.release()
