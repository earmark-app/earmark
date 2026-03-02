from __future__ import annotations

import os
import tempfile

import pytest

# Set test env before importing app modules
os.environ["DATA_DIR"] = tempfile.mkdtemp()

from src.db import Database
from src.models import (
    ABSLibrary,
    ABSLibraryItem,
    ABSMedia,
    ABSMediaMetadata,
    ABSMediaProgress,
    HardcoverBook,
    HardcoverEdition,
    HardcoverUserBook,
)


@pytest.fixture
def db(tmp_path):
    """Create an in-memory-like SQLite database for testing."""
    db_path = str(tmp_path / "test.db")
    database = Database(db_path)
    database.init_schema()
    return database


@pytest.fixture
def sample_user(db):
    """Create a sample user in the test database."""
    return db.create_user(
        {
            "name": "Test User",
            "hardcover_token": "hc_test_token",
            "abs_user_id": "abs_user_001",
        }
    )


@pytest.fixture
def sample_sync_rule(db, sample_user):
    """Create a sample sync rule."""
    return db.create_sync_rule(
        {
            "user_id": sample_user["id"],
            "direction": "hc_to_abs",
            "hc_status_id": 1,
            "abs_target_type": "collection",
            "abs_target_name": "Want to Read",
            "abs_library_id": "lib_001",
        }
    )


def make_hc_book(
    book_id: int = 1,
    title: str = "Project Hail Mary",
    author: str = "Andy Weir",
    isbn_13: str | None = "9780593135204",
    isbn_10: str | None = None,
    asin: str | None = "B08FHBV4ZX",
) -> HardcoverBook:
    """Helper to create a HardcoverBook with editions."""
    editions = []
    if isbn_13 or isbn_10 or asin:
        editions.append(
            HardcoverEdition(
                id=book_id * 10,
                isbn_13=isbn_13,
                isbn_10=isbn_10,
                asin=asin,
            )
        )
    contributors = [{"author": {"name": author}}] if author else []
    return HardcoverBook(
        id=book_id,
        title=title,
        cached_contributors=contributors,
        editions=editions,
    )


def make_abs_item(
    item_id: str = "li_001",
    title: str = "Project Hail Mary",
    author: str = "Andy Weir",
    isbn: str | None = "9780593135204",
    asin: str | None = "B08FHBV4ZX",
) -> ABSLibraryItem:
    """Helper to create an ABSLibraryItem."""
    return ABSLibraryItem(
        id=item_id,
        media=ABSMedia(
            metadata=ABSMediaMetadata(
                title=title,
                authorName=author,
                isbn=isbn,
                asin=asin,
            )
        ),
    )


def make_abs_progress(
    item_id: str = "li_001",
    progress: float = 0.5,
    is_finished: bool = False,
    duration: float = 36000.0,
) -> ABSMediaProgress:
    """Helper to create an ABSMediaProgress."""
    return ABSMediaProgress(
        libraryItemId=item_id,
        progress=progress,
        currentTime=progress * duration,
        isFinished=is_finished,
        duration=duration,
    )
