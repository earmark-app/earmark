from __future__ import annotations

import logging
import re
from typing import Optional

from rapidfuzz import fuzz

from src.models import (
    ABSLibraryItem,
    BookMappingCreate,
    HardcoverBook,
    MatchResult,
)

logger = logging.getLogger(__name__)


def _normalize(text: str) -> str:
    """Lowercase, strip, and remove punctuation for comparison."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text


class BookMatcher:
    def __init__(self, db=None, threshold: float = 0.85):
        self.db = db
        self.threshold = threshold

    def match(
        self,
        hc_book: HardcoverBook,
        abs_items: list[ABSLibraryItem],
        user_id: str | None = None,
    ) -> Optional[MatchResult]:
        """Match a Hardcover book to an ABS library item using 3-tier strategy.

        Tier 1: ASIN exact match
        Tier 2: ISBN exact match (isbn_13 and isbn_10)
        Tier 3: Fuzzy title+author matching
        """
        if not abs_items:
            return None

        # Check cache first
        if self.db and user_id:
            cached = self.db.find_mapping_by_hc_book(user_id, hc_book.id)
            if cached:
                return MatchResult(
                    abs_library_item_id=cached["abs_library_item_id"],
                    hardcover_book_id=hc_book.id,
                    hardcover_edition_id=cached.get("hardcover_edition_id"),
                    method=cached["match_method"],
                    confidence=cached["match_confidence"],
                    title=cached.get("title"),
                )

        # Tier 1: ASIN match
        result = self._match_asin(hc_book, abs_items)
        if result:
            self._cache_result(result, user_id)
            return result

        # Tier 2: ISBN match
        result = self._match_isbn(hc_book, abs_items)
        if result:
            self._cache_result(result, user_id)
            return result

        # Tier 3: Fuzzy title+author
        result = self._match_fuzzy(hc_book, abs_items)
        if result:
            self._cache_result(result, user_id)
            return result

        return None

    def match_batch(
        self,
        hc_books: list[HardcoverBook],
        abs_items: list[ABSLibraryItem],
        user_id: str | None = None,
    ) -> list[tuple[HardcoverBook, Optional[MatchResult]]]:
        """Match a list of Hardcover books to ABS items."""
        results = []
        for hc_book in hc_books:
            result = self.match(hc_book, abs_items, user_id)
            results.append((hc_book, result))
        return results

    def _match_asin(
        self, hc_book: HardcoverBook, abs_items: list[ABSLibraryItem]
    ) -> Optional[MatchResult]:
        hc_asins = set()
        edition_map: dict[str, int] = {}
        for edition in hc_book.editions:
            if edition.asin:
                asin = edition.asin.strip().upper()
                hc_asins.add(asin)
                edition_map[asin] = edition.id

        if not hc_asins:
            return None

        matches = []
        for item in abs_items:
            if item.asin:
                abs_asin = item.asin.strip().upper()
                if abs_asin in hc_asins:
                    matches.append((item, edition_map.get(abs_asin)))

        if not matches:
            return None

        if len(matches) > 1:
            logger.warning(
                "Multiple ASIN matches for '%s' (HC ID %d), using first",
                hc_book.title, hc_book.id,
            )

        item, edition_id = matches[0]
        return MatchResult(
            abs_library_item_id=item.id,
            hardcover_book_id=hc_book.id,
            hardcover_edition_id=edition_id,
            method="asin",
            confidence=1.0,
            title=hc_book.title,
        )

    def _match_isbn(
        self, hc_book: HardcoverBook, abs_items: list[ABSLibraryItem]
    ) -> Optional[MatchResult]:
        hc_isbns = set()
        edition_map: dict[str, int] = {}
        for edition in hc_book.editions:
            for isbn_field in (edition.isbn_13, edition.isbn_10):
                if isbn_field:
                    isbn = isbn_field.strip().replace("-", "")
                    hc_isbns.add(isbn)
                    edition_map[isbn] = edition.id

        if not hc_isbns:
            return None

        matches = []
        for item in abs_items:
            if item.isbn:
                abs_isbn = item.isbn.strip().replace("-", "")
                if abs_isbn in hc_isbns:
                    matches.append((item, edition_map.get(abs_isbn)))

        if not matches:
            return None

        if len(matches) > 1:
            logger.warning(
                "Multiple ISBN matches for '%s' (HC ID %d), using first",
                hc_book.title, hc_book.id,
            )

        item, edition_id = matches[0]
        return MatchResult(
            abs_library_item_id=item.id,
            hardcover_book_id=hc_book.id,
            hardcover_edition_id=edition_id,
            method="isbn",
            confidence=1.0,
            title=hc_book.title,
        )

    def _match_fuzzy(
        self, hc_book: HardcoverBook, abs_items: list[ABSLibraryItem]
    ) -> Optional[MatchResult]:
        hc_str = _normalize(f"{hc_book.title} {hc_book.author}")

        best_score = 0.0
        best_item = None

        for item in abs_items:
            abs_str = _normalize(f"{item.title} {item.author}")
            score = fuzz.token_sort_ratio(hc_str, abs_str) / 100.0

            if score > best_score:
                best_score = score
                best_item = item

        if best_item and best_score >= self.threshold:
            return MatchResult(
                abs_library_item_id=best_item.id,
                hardcover_book_id=hc_book.id,
                method="title_author",
                confidence=round(best_score, 4),
                title=hc_book.title,
            )

        if best_item and best_score > 0:
            logger.debug(
                "Fuzzy match below threshold for '%s': best=%.2f (threshold=%.2f)",
                hc_book.title, best_score, self.threshold,
            )

        return None

    def _cache_result(self, result: MatchResult, user_id: str | None):
        if not self.db or not user_id:
            return
        try:
            existing = self.db.get_book_mapping_by_books(
                user_id, result.hardcover_book_id, result.abs_library_item_id
            )
            if not existing:
                self.db.create_book_mapping(
                    BookMappingCreate(
                        user_id=user_id,
                        hardcover_book_id=result.hardcover_book_id,
                        hardcover_edition_id=result.hardcover_edition_id,
                        abs_library_item_id=result.abs_library_item_id,
                        match_method=result.method,
                        match_confidence=result.confidence,
                        title=result.title,
                    ).model_dump()
                )
        except Exception:
            logger.exception("Failed to cache match result")
