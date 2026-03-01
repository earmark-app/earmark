from __future__ import annotations

import logging

import pytest

from src.matching.book_matcher import BookMatcher
from tests.conftest import make_abs_item, make_hc_book


@pytest.fixture
def matcher():
    return BookMatcher(threshold=0.85)


@pytest.fixture
def matcher_with_db(db):
    return BookMatcher(db=db, threshold=0.85)


class TestASINMatch:
    def test_exact_asin_match(self, matcher):
        hc = make_hc_book(asin="B08FHBV4ZX", isbn_13=None)
        abs_items = [make_abs_item(asin="B08FHBV4ZX", isbn=None)]
        result = matcher.match(hc, abs_items)
        assert result is not None
        assert result.method == "asin"
        assert result.confidence == 1.0
        assert result.abs_library_item_id == "li_001"

    def test_asin_case_insensitive(self, matcher):
        hc = make_hc_book(asin="b08fhbv4zx", isbn_13=None)
        abs_items = [make_abs_item(asin="B08FHBV4ZX", isbn=None)]
        result = matcher.match(hc, abs_items)
        assert result is not None
        assert result.method == "asin"

    def test_asin_no_match(self, matcher):
        hc = make_hc_book(asin="B08FHBV4ZX", isbn_13=None)
        abs_items = [make_abs_item(asin="BXXXXXXXXX", isbn=None, title="Dune", author="Frank Herbert")]
        result = matcher.match(hc, abs_items)
        assert result is None

    def test_asin_none_on_hc(self, matcher):
        hc = make_hc_book(asin=None, isbn_13=None)
        abs_items = [make_abs_item(asin="B08FHBV4ZX", isbn=None, title="Dune", author="Frank Herbert")]
        result = matcher.match(hc, abs_items)
        assert result is None

    def test_asin_none_on_abs(self, matcher):
        hc = make_hc_book(asin="B08FHBV4ZX", isbn_13=None)
        abs_items = [make_abs_item(asin=None, isbn=None, title="Dune", author="Frank Herbert")]
        result = matcher.match(hc, abs_items)
        assert result is None


class TestISBNMatch:
    def test_isbn13_exact_match(self, matcher):
        hc = make_hc_book(isbn_13="9780593135204", asin=None)
        abs_items = [make_abs_item(isbn="9780593135204", asin=None)]
        result = matcher.match(hc, abs_items)
        assert result is not None
        assert result.method == "isbn"
        assert result.confidence == 1.0

    def test_isbn10_match(self, matcher):
        hc = make_hc_book(isbn_13=None, isbn_10="0593135202", asin=None)
        abs_items = [make_abs_item(isbn="0593135202", asin=None)]
        result = matcher.match(hc, abs_items)
        assert result is not None
        assert result.method == "isbn"

    def test_isbn_with_dashes(self, matcher):
        hc = make_hc_book(isbn_13="978-0-593-13520-4", asin=None)
        abs_items = [make_abs_item(isbn="9780593135204", asin=None)]
        result = matcher.match(hc, abs_items)
        assert result is not None
        assert result.method == "isbn"

    def test_isbn_no_match(self, matcher):
        hc = make_hc_book(isbn_13="9780593135204", asin=None)
        abs_items = [make_abs_item(isbn="9781234567890", asin=None, title="Dune", author="Frank Herbert")]
        result = matcher.match(hc, abs_items)
        assert result is None

    def test_isbn_none(self, matcher):
        hc = make_hc_book(isbn_13=None, isbn_10=None, asin=None)
        abs_items = [make_abs_item(isbn="9780593135204", asin=None)]
        result = matcher.match(hc, abs_items)
        # Should fall through to fuzzy
        assert result is None or result.method == "title_author"


class TestFuzzyMatch:
    def test_exact_title_author(self, matcher):
        hc = make_hc_book(
            title="Project Hail Mary", author="Andy Weir",
            isbn_13=None, asin=None,
        )
        abs_items = [
            make_abs_item(
                title="Project Hail Mary", author="Andy Weir",
                isbn=None, asin=None,
            )
        ]
        result = matcher.match(hc, abs_items)
        assert result is not None
        assert result.method == "title_author"
        assert result.confidence >= 0.85

    def test_similar_title_above_threshold(self, matcher):
        hc = make_hc_book(
            title="The Martian", author="Andy Weir",
            isbn_13=None, asin=None,
        )
        abs_items = [
            make_abs_item(
                title="The Martian", author="Andy Weir",
                isbn=None, asin=None,
            )
        ]
        result = matcher.match(hc, abs_items)
        assert result is not None
        assert result.confidence >= 0.85

    def test_different_books_below_threshold(self, matcher):
        hc = make_hc_book(
            title="Project Hail Mary", author="Andy Weir",
            isbn_13=None, asin=None,
        )
        abs_items = [
            make_abs_item(
                title="Dune", author="Frank Herbert",
                isbn=None, asin=None,
            )
        ]
        result = matcher.match(hc, abs_items)
        assert result is None

    def test_fuzzy_picks_best_match(self, matcher):
        hc = make_hc_book(
            title="Project Hail Mary", author="Andy Weir",
            isbn_13=None, asin=None,
        )
        abs_items = [
            make_abs_item(
                item_id="li_wrong", title="Dune", author="Frank Herbert",
                isbn=None, asin=None,
            ),
            make_abs_item(
                item_id="li_right", title="Project Hail Mary", author="Andy Weir",
                isbn=None, asin=None,
            ),
        ]
        result = matcher.match(hc, abs_items)
        assert result is not None
        assert result.abs_library_item_id == "li_right"

    def test_unicode_handling(self, matcher):
        hc = make_hc_book(
            title="Müller's Café", author="José García",
            isbn_13=None, asin=None,
        )
        abs_items = [
            make_abs_item(
                title="Müller's Café", author="José García",
                isbn=None, asin=None,
            )
        ]
        result = matcher.match(hc, abs_items)
        assert result is not None
        assert result.confidence >= 0.85


class TestMatchPriority:
    """ASIN should be tried first, then ISBN, then fuzzy."""

    def test_asin_preferred_over_isbn(self, matcher):
        hc = make_hc_book(
            isbn_13="9780593135204", asin="B08FHBV4ZX",
        )
        abs_items = [
            make_abs_item(
                item_id="li_isbn", isbn="9780593135204", asin=None,
            ),
            make_abs_item(
                item_id="li_asin", isbn=None, asin="B08FHBV4ZX",
            ),
        ]
        result = matcher.match(hc, abs_items)
        assert result is not None
        assert result.method == "asin"
        assert result.abs_library_item_id == "li_asin"

    def test_isbn_preferred_over_fuzzy(self, matcher):
        hc = make_hc_book(
            title="Project Hail Mary", author="Andy Weir",
            isbn_13="9780593135204", asin=None,
        )
        abs_items = [
            make_abs_item(
                item_id="li_fuzzy", title="Project Hail Mary", author="Andy Weir",
                isbn=None, asin=None,
            ),
            make_abs_item(
                item_id="li_isbn", title="Different Title", author="Different",
                isbn="9780593135204", asin=None,
            ),
        ]
        result = matcher.match(hc, abs_items)
        assert result is not None
        assert result.method == "isbn"
        assert result.abs_library_item_id == "li_isbn"


class TestEdgeCases:
    def test_empty_abs_items(self, matcher):
        hc = make_hc_book()
        result = matcher.match(hc, [])
        assert result is None

    def test_no_editions(self, matcher):
        hc = make_hc_book(isbn_13=None, isbn_10=None, asin=None)
        abs_items = [make_abs_item(isbn=None, asin=None, title="Dune", author="Frank Herbert")]
        result = matcher.match(hc, abs_items)
        assert result is None  # different book, fuzzy won't match

    def test_missing_author(self, matcher):
        hc = make_hc_book(author="", isbn_13=None, asin=None)
        abs_items = [
            make_abs_item(
                title="Project Hail Mary", author="",
                isbn=None, asin=None,
            )
        ]
        result = matcher.match(hc, abs_items)
        assert result is not None  # title alone should be enough
        assert result.method == "title_author"

    def test_duplicate_isbn_uses_first(self, matcher, caplog):
        hc = make_hc_book(isbn_13="9780593135204", asin=None)
        abs_items = [
            make_abs_item(item_id="li_first", isbn="9780593135204", asin=None),
            make_abs_item(item_id="li_second", isbn="9780593135204", asin=None),
        ]
        with caplog.at_level(logging.WARNING):
            result = matcher.match(hc, abs_items)
        assert result is not None
        assert result.abs_library_item_id == "li_first"
        assert "Multiple ISBN matches" in caplog.text


class TestBatchMatching:
    def test_batch_match(self, matcher):
        books = [
            make_hc_book(book_id=1, title="Book Alpha", author="Author One", asin="ASIN_A", isbn_13=None),
            make_hc_book(book_id=2, title="Book Beta", author="Author Two", asin="ASIN_B", isbn_13=None),
            make_hc_book(book_id=3, title="Completely Different Title", author="Unknown Writer", asin=None, isbn_13=None),
        ]
        abs_items = [
            make_abs_item(item_id="li_a", title="Book Alpha", author="Author One", asin="ASIN_A", isbn=None),
            make_abs_item(item_id="li_b", title="Book Beta", author="Author Two", asin="ASIN_B", isbn=None),
        ]
        results = matcher.match_batch(books, abs_items)
        assert len(results) == 3
        assert results[0][1] is not None  # Book A matched
        assert results[0][1].abs_library_item_id == "li_a"
        assert results[1][1] is not None  # Book B matched
        assert results[1][1].abs_library_item_id == "li_b"
        assert results[2][1] is None  # Book C no match


class TestCaching:
    def test_caches_match_in_db(self, matcher_with_db, db, sample_user):
        hc = make_hc_book(asin="B08FHBV4ZX", isbn_13=None)
        abs_items = [make_abs_item(asin="B08FHBV4ZX", isbn=None)]

        result = matcher_with_db.match(hc, abs_items, user_id=sample_user["id"])
        assert result is not None

        # Check DB has the mapping
        mappings = db.list_book_mappings(user_id=sample_user["id"])
        assert len(mappings) == 1
        assert mappings[0]["match_method"] == "asin"

    def test_returns_cached_match(self, matcher_with_db, db, sample_user):
        # Pre-populate cache
        db.create_book_mapping(
            {
                "user_id": sample_user["id"],
                "hardcover_book_id": 1,
                "abs_library_item_id": "li_cached",
                "match_method": "isbn",
                "match_confidence": 1.0,
                "title": "Cached Book",
            }
        )

        hc = make_hc_book(book_id=1, asin="DIFFERENT", isbn_13=None)
        abs_items = [make_abs_item(item_id="li_other", asin=None, isbn=None)]

        result = matcher_with_db.match(hc, abs_items, user_id=sample_user["id"])
        assert result is not None
        assert result.abs_library_item_id == "li_cached"
        assert result.method == "isbn"
