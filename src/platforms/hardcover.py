from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from src.models import HardcoverBook, HardcoverEdition, HardcoverUserBook

logger = logging.getLogger(__name__)

HARDCOVER_GRAPHQL_URL = "https://api.hardcover.app/v1/graphql"
RATE_LIMIT_REQUESTS = 60
RATE_LIMIT_WINDOW = 60.0  # seconds


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class HardcoverError(Exception):
    pass


class HardcoverAuthError(HardcoverError):
    pass


class HardcoverRateLimitError(HardcoverError):
    pass


# ---------------------------------------------------------------------------
# Retry predicate
# ---------------------------------------------------------------------------


def _should_retry(exc: BaseException) -> bool:
    """Retry on HTTP 429 or 5xx responses (not auth errors)."""
    if isinstance(exc, HardcoverRateLimitError):
        return True
    if isinstance(exc, HardcoverError) and "5" in str(exc)[:3]:
        return True
    # Catch raw httpx status errors for 429/5xx before we convert them
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429 or exc.response.status_code >= 500
    return False


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class HardcoverClient:
    def __init__(
        self,
        token: str,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        # Support both "Bearer ey..." and bare "ey..." token formats
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        self._token = token
        self._owns_client = http_client is None
        if http_client is None:
            self._http = httpx.AsyncClient(
                base_url=HARDCOVER_GRAPHQL_URL,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                timeout=30.0,
            )
        else:
            self._http = http_client

        # Rate limiter state
        self._rate_lock = asyncio.Lock()
        self._request_timestamps: list[float] = []

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def close(self) -> None:
        if self._owns_client:
            await self._http.aclose()

    # ------------------------------------------------------------------
    # Rate limiting (sliding window token bucket)
    # ------------------------------------------------------------------

    async def _acquire_rate_slot(self) -> None:
        async with self._rate_lock:
            now = time.monotonic()
            window_start = now - RATE_LIMIT_WINDOW
            # Drop timestamps outside the current window
            self._request_timestamps = [
                ts for ts in self._request_timestamps if ts > window_start
            ]
            if len(self._request_timestamps) >= RATE_LIMIT_REQUESTS:
                oldest = self._request_timestamps[0]
                sleep_for = RATE_LIMIT_WINDOW - (now - oldest) + 0.05
                if sleep_for > 0:
                    logger.debug("Rate limit reached; sleeping %.2fs", sleep_for)
                    await asyncio.sleep(sleep_for)
                # Re-prune after sleep
                now = time.monotonic()
                window_start = now - RATE_LIMIT_WINDOW
                self._request_timestamps = [
                    ts for ts in self._request_timestamps if ts > window_start
                ]
            self._request_timestamps.append(time.monotonic())

    # ------------------------------------------------------------------
    # Core request
    # ------------------------------------------------------------------

    @retry(
        retry=retry_if_exception(_should_retry),
        wait=wait_exponential(multiplier=1, min=1, max=4),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    async def _request(
        self, query: str, variables: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        await self._acquire_rate_slot()

        payload: dict[str, Any] = {"query": query}
        if variables:
            payload["variables"] = variables

        logger.debug("Hardcover GraphQL request: %s", query[:120])
        response = await self._http.post("", json=payload)

        if response.status_code == 401:
            raise HardcoverAuthError("Hardcover authentication failed (401)")
        if response.status_code == 429:
            raise HardcoverRateLimitError("Hardcover rate limit exceeded (429)")
        if response.status_code >= 500:
            raise HardcoverError(f"Hardcover server error ({response.status_code})")

        response.raise_for_status()

        data = response.json()
        if "errors" in data:
            errors = data["errors"]
            msg = "; ".join(e.get("message", str(e)) for e in errors)
            logger.warning("GraphQL errors: %s", msg)
            raise HardcoverError(f"GraphQL error: {msg}")

        return data.get("data", {})

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    async def get_me(self) -> dict[str, Any]:
        """Return a dict with ``id`` and ``username`` for the authenticated user."""
        query = "{ me { id username } }"
        data = await self._request(query)
        me_list = data.get("me") or []
        if not me_list:
            raise HardcoverError("get_me returned empty result")
        return me_list[0] if isinstance(me_list, list) else me_list

    async def get_user_books(
        self, status_id: int | None = None
    ) -> list[HardcoverUserBook]:
        """Fetch the authenticated user's books, optionally filtered by status_id."""
        if status_id is not None:
            where_clause = f"(where: {{status_id: {{_eq: {status_id}}}}}) "
        else:
            where_clause = ""

        query = (
            "{ me { user_books"
            + (f"(where: {{status_id: {{_eq: {status_id}}}}}) " if status_id is not None else " ")
            + "{ id status_id rating book { id title slug cached_contributors"
            + " editions { id isbn_13 isbn_10 asin } } } } }"
        )
        data = await self._request(query)
        me_list = data.get("me") or []
        me = me_list[0] if isinstance(me_list, list) and me_list else me_list
        raw_books: list[dict] = (me or {}).get("user_books", [])

        results: list[HardcoverUserBook] = []
        for ub in raw_books:
            try:
                results.append(_parse_user_book(ub))
            except Exception as exc:
                logger.warning("Skipping malformed user_book %s: %s", ub.get("id"), exc)
        return results

    async def get_lists(self) -> list[dict[str, Any]]:
        """Return the user's lists with basic book info."""
        query = (
            "{ me { lists { id name list_books { book_id book { title } } } } }"
        )
        data = await self._request(query)
        me_list = data.get("me") or []
        me = me_list[0] if isinstance(me_list, list) and me_list else me_list
        return (me or {}).get("lists", [])

    async def get_list_books(self, list_id: int) -> list[dict[str, Any]]:
        """Return books belonging to a specific list."""
        lists = await self.get_lists()
        for lst in lists:
            if lst.get("id") == list_id:
                return lst.get("list_books", [])
        return []

    async def search_by_isbn(self, isbn: str) -> list[dict[str, Any]]:
        """Search for editions matching an ISBN-13."""
        query = (
            '{ editions(where: {isbn_13: {_eq: "' + isbn + '"}}) '
            "{ id book { id title slug } } }"
        )
        data = await self._request(query)
        return data.get("editions", [])

    async def search_by_asin(self, asin: str) -> list[dict[str, Any]]:
        """Search for editions matching an ASIN."""
        query = (
            '{ editions(where: {asin: {_eq: "' + asin + '"}}) '
            "{ id book { id title slug } } }"
        )
        data = await self._request(query)
        return data.get("editions", [])

    async def search_text(self, query_text: str) -> list[dict[str, Any]]:
        """Full-text book search; returns up to 5 results."""
        escaped = query_text.replace('"', '\\"')
        query = (
            '{ search(query: "' + escaped + '", query_type: "books", per_page: 5, page: 1)'
            " { results } }"
        )
        data = await self._request(query)
        results = data.get("search", {}).get("results", [])
        if isinstance(results, list):
            return results
        # results may be a JSON scalar — return as-is wrapped in a list
        return [results] if results else []

    async def set_book_status(self, book_id: int, status_id: int) -> dict[str, Any]:
        """Insert a new user_book record (first time tracking a book)."""
        mutation = (
            "mutation { insert_user_book(object: {book_id: "
            + str(book_id)
            + ", status_id: "
            + str(status_id)
            + "}) { id } }"
        )
        data = await self._request(mutation)
        return data.get("insert_user_book", {})

    async def update_book_status(self, book_id: int, status_id: int) -> dict[str, Any]:
        """Update the status of an existing user_book."""
        mutation = (
            "mutation { update_user_book(where: {book_id: {_eq: "
            + str(book_id)
            + "}}, _set: {status_id: "
            + str(status_id)
            + "}) { returning { id } } }"
        )
        data = await self._request(mutation)
        return data.get("update_user_book", {})

    async def test_connection(self) -> dict[str, Any]:
        """Verify credentials by calling get_me and returning the user info."""
        return await self.get_me()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_edition(raw: dict[str, Any]) -> HardcoverEdition:
    return HardcoverEdition(
        id=raw["id"],
        isbn_13=raw.get("isbn_13"),
        isbn_10=raw.get("isbn_10"),
        asin=raw.get("asin"),
        format=raw.get("format"),
    )


def _parse_book(raw: dict[str, Any]) -> HardcoverBook:
    editions = [_parse_edition(e) for e in raw.get("editions", [])]
    return HardcoverBook(
        id=raw["id"],
        title=raw["title"],
        slug=raw.get("slug"),
        cached_contributors=raw.get("cached_contributors"),
        editions=editions,
    )


def _parse_user_book(raw: dict[str, Any]) -> HardcoverUserBook:
    return HardcoverUserBook(
        id=raw["id"],
        status_id=raw["status_id"],
        rating=raw.get("rating"),
        book=_parse_book(raw["book"]),
    )
