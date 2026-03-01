from __future__ import annotations

import asyncio
import logging
from typing import Optional

import httpx

from src.models import (
    ABSCollection,
    ABSLibrary,
    ABSLibraryItem,
    ABSPlaylist,
)

logger = logging.getLogger(__name__)


# --- Exceptions ---


class ABSError(Exception):
    pass


class ABSConnectionError(ABSError):
    pass


class ABSAuthError(ABSError):
    pass


# --- Client ---


class AudiobookshelfClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._owns_client = http_client is None
        if http_client is not None:
            self._client = http_client
        else:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=30.0,
            )
        self._semaphore = asyncio.Semaphore(10)

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    # --- Internal helpers ---

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        await self._semaphore.acquire()
        try:
            response = await self._client.request(method, path, **kwargs)
        except httpx.ConnectError as exc:
            self._semaphore.release()
            raise ABSConnectionError(
                f"Could not connect to Audiobookshelf at {self.base_url}: {exc}"
            ) from exc
        except httpx.RequestError as exc:
            self._semaphore.release()
            raise ABSConnectionError(
                f"Request error while contacting Audiobookshelf: {exc}"
            ) from exc

        # Release the semaphore slot after 1 second to enforce 10 req/sec
        loop = asyncio.get_event_loop()
        loop.call_later(1.0, self._semaphore.release)

        if response.status_code in (401, 403):
            raise ABSAuthError(
                f"Authentication failed (HTTP {response.status_code}): {response.text}"
            )

        response.raise_for_status()

        if not response.content:
            return {}
        return response.json()

    # --- User & Libraries ---

    async def get_me(self) -> dict:
        """Return user info including mediaProgress array."""
        return await self._request("GET", "/api/me")

    async def get_libraries(self) -> list[ABSLibrary]:
        """Return all libraries."""
        data = await self._request("GET", "/api/libraries")
        libraries = data.get("libraries", [])
        return [ABSLibrary.model_validate(lib) for lib in libraries]

    async def get_library_items(
        self,
        library_id: str,
        limit: int = 100,
        page: int = 0,
    ) -> list[ABSLibraryItem]:
        """Return one page of library items."""
        params = {
            "limit": limit,
            "page": page,
            "sort": "media.metadata.title",
        }
        data = await self._request(
            "GET", f"/api/libraries/{library_id}/items", params=params
        )
        results = data.get("results", [])
        return [ABSLibraryItem.model_validate(item) for item in results]

    async def get_all_library_items(self, library_id: str) -> list[ABSLibraryItem]:
        """Auto-paginate through all pages and return every library item."""
        all_items: list[ABSLibraryItem] = []
        page = 0
        limit = 100
        while True:
            items = await self.get_library_items(library_id, limit=limit, page=page)
            all_items.extend(items)
            if len(items) < limit:
                break
            page += 1
        logger.debug(
            "Fetched %d items from library %s across %d page(s)",
            len(all_items),
            library_id,
            page + 1,
        )
        return all_items

    async def search_library(
        self, library_id: str, query: str
    ) -> list[ABSLibraryItem]:
        """Search a library by query string."""
        data = await self._request(
            "GET",
            f"/api/libraries/{library_id}/search",
            params={"q": query},
        )
        # Search response wraps results under a "book" key (list of {libraryItem: ...})
        raw_books = data.get("book", [])
        items: list[ABSLibraryItem] = []
        for entry in raw_books:
            raw_item = entry.get("libraryItem", entry)
            items.append(ABSLibraryItem.model_validate(raw_item))
        return items

    async def get_item(self, item_id: str) -> ABSLibraryItem:
        """Return a single library item with progress included."""
        data = await self._request(
            "GET",
            f"/api/items/{item_id}",
            params={"expanded": 1, "include": "progress"},
        )
        return ABSLibraryItem.model_validate(data)

    # --- Collections ---

    async def get_collections(self, library_id: str) -> list[ABSCollection]:
        """Return all collections for a library."""
        data = await self._request(
            "GET", f"/api/libraries/{library_id}/collections"
        )
        collections = data.get("collections", [])
        return [ABSCollection.model_validate(c) for c in collections]

    async def create_collection(
        self, library_id: str, name: str
    ) -> ABSCollection:
        """Create a new collection."""
        data = await self._request(
            "POST",
            "/api/collections",
            json={"libraryId": library_id, "name": name},
        )
        return ABSCollection.model_validate(data)

    async def add_to_collection(
        self, collection_id: str, item_id: str
    ) -> dict:
        """Add a single item to a collection."""
        return await self._request(
            "POST",
            f"/api/collections/{collection_id}/book",
            json={"id": item_id},
        )

    async def remove_from_collection(
        self, collection_id: str, item_id: str
    ) -> dict:
        """Remove a single item from a collection."""
        return await self._request(
            "DELETE",
            f"/api/collections/{collection_id}/book/{item_id}",
        )

    async def batch_add_to_collection(
        self, collection_id: str, item_ids: list[str]
    ) -> dict:
        """Add multiple items to a collection in one request."""
        return await self._request(
            "POST",
            f"/api/collections/{collection_id}/batch/add",
            json={"books": item_ids},
        )

    async def batch_remove_from_collection(
        self, collection_id: str, item_ids: list[str]
    ) -> dict:
        """Remove multiple items from a collection in one request."""
        return await self._request(
            "POST",
            f"/api/collections/{collection_id}/batch/remove",
            json={"books": item_ids},
        )

    # --- Playlists ---

    async def get_playlists(self) -> list[ABSPlaylist]:
        """Return all playlists for the current user."""
        data = await self._request("GET", "/api/playlists")
        playlists = data.get("playlists", [])
        return [ABSPlaylist.model_validate(p) for p in playlists]

    async def create_playlist(
        self, library_id: str, name: str
    ) -> ABSPlaylist:
        """Create a new playlist."""
        data = await self._request(
            "POST",
            "/api/playlists",
            json={"libraryId": library_id, "name": name},
        )
        return ABSPlaylist.model_validate(data)

    async def add_to_playlist(
        self, playlist_id: str, item_id: str
    ) -> dict:
        """Add a single item to a playlist."""
        return await self._request(
            "POST",
            f"/api/playlists/{playlist_id}/item",
            json={"libraryItemId": item_id},
        )

    async def remove_from_playlist(
        self, playlist_id: str, item_id: str
    ) -> dict:
        """Remove a single item from a playlist."""
        return await self._request(
            "DELETE",
            f"/api/playlists/{playlist_id}/item/{item_id}",
        )

    async def batch_add_to_playlist(
        self, playlist_id: str, item_ids: list[str]
    ) -> dict:
        """Add multiple items to a playlist in one request."""
        return await self._request(
            "POST",
            f"/api/playlists/{playlist_id}/batch/add",
            json={"items": [{"libraryItemId": iid} for iid in item_ids]},
        )

    async def batch_remove_from_playlist(
        self, playlist_id: str, item_ids: list[str]
    ) -> dict:
        """Remove multiple items from a playlist in one request."""
        return await self._request(
            "POST",
            f"/api/playlists/{playlist_id}/batch/remove",
            json={"items": [{"libraryItemId": iid} for iid in item_ids]},
        )

    # --- Progress ---

    async def update_progress(
        self,
        item_id: str,
        progress: float,
        current_time: float = 0,
        is_finished: bool = False,
        duration: float = 0,
    ) -> dict:
        """Patch listening progress for a library item."""
        return await self._request(
            "PATCH",
            f"/api/me/progress/{item_id}",
            json={
                "progress": progress,
                "currentTime": current_time,
                "isFinished": is_finished,
                "duration": duration,
            },
        )

    # --- Listening Stats ---

    async def get_listening_stats(self) -> dict:
        """Return aggregated listening stats for the current user."""
        return await self._request("GET", "/api/me/listening-stats")

    async def get_listening_sessions(self, limit: int = 50) -> list[dict]:
        """Return recent listening sessions."""
        data = await self._request(
            "GET", "/api/me/listening-sessions", params={"itemsPerPage": limit}
        )
        return data.get("sessions", [])

    # --- Tags ---

    async def update_item_tags(self, item_id: str, tags: list[str]) -> dict:
        """Update tags on a library item via media metadata."""
        return await self._request(
            "PATCH",
            f"/api/items/{item_id}/media",
            json={"metadata": {"tags": tags}},
        )

    async def get_item_tags(self, item_id: str) -> list[str]:
        """Get current tags for a library item."""
        data = await self._request("GET", f"/api/items/{item_id}")
        metadata = data.get("media", {}).get("metadata", {})
        return metadata.get("tags", [])

    # --- Connection test ---

    async def test_connection(self) -> dict:
        """Verify connectivity, return user info, libraries, and admin flag."""
        me = await self.get_me()
        libraries = await self.get_libraries()
        user_type = me.get("type", "")
        is_admin = user_type in ("root", "admin")
        logger.info(
            "ABS connection OK — user=%s type=%s libraries=%d",
            me.get("username"),
            user_type,
            len(libraries),
        )
        return {
            "user": me,
            "libraries": [lib.model_dump() for lib in libraries],
            "is_admin": is_admin,
        }
