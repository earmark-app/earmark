from __future__ import annotations

"""
Tests for platform HTTP clients.

HardcoverClient  — GraphQL, tenacity retry, 401/429 error handling
AudiobookshelfClient — REST, semaphore rate-limiter, 401/connection error handling

respx patches httpx globally when the `respx_mock` fixture is active; do NOT
pass `respx_mock` as an httpx transport. Instead build plain AsyncClient
instances and let respx intercept them.
"""

import json

import httpx
import pytest
import respx

from src.platforms.hardcover import (
    HARDCOVER_GRAPHQL_URL,
    HardcoverAuthError,
    HardcoverClient,
    HardcoverRateLimitError,
)
from src.platforms.audiobookshelf import (
    ABSAuthError,
    ABSConnectionError,
    AudiobookshelfClient,
)
from src.models import (
    ABSCollection,
    ABSLibrary,
    ABSLibraryItem,
    ABSPlaylist,
    HardcoverUserBook,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ABS_BASE = "http://abs.test.local"
# httpx normalises base_url by appending a trailing slash; post("") resolves to
# "https://api.hardcover.app/v1/graphql/" — the mock must match that exact URL.
HC_URL = HARDCOVER_GRAPHQL_URL  # "https://api.hardcover.app/v1/graphql"
HC_URL_MOCK = HC_URL.rstrip("/") + "/"  # trailing-slash form httpx actually sends


# ---------------------------------------------------------------------------
# Helpers — build clients without custom transports so respx can patch them
# ---------------------------------------------------------------------------


def _hc_client() -> HardcoverClient:
    """HardcoverClient that uses a plain AsyncClient (patched by respx_mock)."""
    http = httpx.AsyncClient(
        base_url=HC_URL,
        headers={"Authorization": "Bearer test_token", "Content-Type": "application/json"},
        timeout=30.0,
    )
    return HardcoverClient(token="test_token", http_client=http)


def _abs_client() -> AudiobookshelfClient:
    """AudiobookshelfClient that uses a plain AsyncClient (patched by respx_mock)."""
    http = httpx.AsyncClient(
        base_url=ABS_BASE,
        headers={"Authorization": "Bearer abs_test_key"},
        timeout=30.0,
    )
    return AudiobookshelfClient(
        base_url=ABS_BASE,
        api_key="abs_test_key",
        http_client=http,
    )


# ===========================================================================
# HardcoverClient Tests
# ===========================================================================


class TestHardcoverGetMe:
    @pytest.mark.asyncio
    async def test_get_me_returns_dict_with_id_and_username(self, respx_mock):
        respx_mock.post(HC_URL_MOCK).mock(
            return_value=httpx.Response(
                200,
                json={"data": {"me": [{"id": 42, "username": "bookworm"}]}},
            )
        )
        client = _hc_client()
        result = await client.get_me()

        assert result["id"] == 42
        assert result["username"] == "bookworm"

    @pytest.mark.asyncio
    async def test_get_me_raises_when_me_list_is_empty(self, respx_mock):
        respx_mock.post(HC_URL_MOCK).mock(
            return_value=httpx.Response(200, json={"data": {"me": []}})
        )
        client = _hc_client()

        from src.platforms.hardcover import HardcoverError
        with pytest.raises(HardcoverError, match="empty result"):
            await client.get_me()


class TestHardcoverGetUserBooks:
    @pytest.mark.asyncio
    async def test_get_user_books_by_status_returns_list_of_hardcover_user_book(
        self, respx_mock
    ):
        payload = {
            "data": {
                "me": [
                    {
                        "user_books": [
                            {
                                "id": 1,
                                "status_id": 2,
                                "rating": 4.5,
                                "book": {
                                    "id": 100,
                                    "title": "Project Hail Mary",
                                    "slug": "project-hail-mary",
                                    "cached_contributors": None,
                                    "editions": [
                                        {
                                            "id": 1000,
                                            "isbn_13": "9780593135204",
                                            "isbn_10": None,
                                            "asin": "B08FHBV4ZX",
                                            "format": "Hardcover",
                                        }
                                    ],
                                },
                            }
                        ]
                    }
                ]
            }
        }
        respx_mock.post(HC_URL_MOCK).mock(return_value=httpx.Response(200, json=payload))
        client = _hc_client()

        results = await client.get_user_books(status_id=2)

        assert len(results) == 1
        ub = results[0]
        assert isinstance(ub, HardcoverUserBook)
        assert ub.id == 1
        assert ub.status_id == 2
        assert ub.rating == 4.5
        assert ub.book.title == "Project Hail Mary"
        assert ub.book.editions[0].isbn_13 == "9780593135204"

    @pytest.mark.asyncio
    async def test_get_user_books_all_without_status_filter_returns_all_books(
        self, respx_mock
    ):
        payload = {
            "data": {
                "me": [
                    {
                        "user_books": [
                            {
                                "id": 1,
                                "status_id": 1,
                                "rating": None,
                                "book": {
                                    "id": 10,
                                    "title": "Dune",
                                    "slug": "dune",
                                    "cached_contributors": None,
                                    "editions": [],
                                },
                            },
                            {
                                "id": 2,
                                "status_id": 3,
                                "rating": 5.0,
                                "book": {
                                    "id": 20,
                                    "title": "The Martian",
                                    "slug": "the-martian",
                                    "cached_contributors": None,
                                    "editions": [],
                                },
                            },
                        ]
                    }
                ]
            }
        }
        respx_mock.post(HC_URL_MOCK).mock(return_value=httpx.Response(200, json=payload))
        client = _hc_client()

        results = await client.get_user_books()

        assert len(results) == 2
        assert all(isinstance(r, HardcoverUserBook) for r in results)
        titles = {r.book.title for r in results}
        assert "Dune" in titles
        assert "The Martian" in titles

    @pytest.mark.asyncio
    async def test_get_user_books_skips_malformed_entries(self, respx_mock):
        payload = {
            "data": {
                "me": [
                    {
                        "user_books": [
                            # Missing required 'book' key — should be skipped
                            {"id": 99, "status_id": 1, "rating": None},
                            {
                                "id": 2,
                                "status_id": 2,
                                "rating": None,
                                "book": {
                                    "id": 20,
                                    "title": "Valid Book",
                                    "slug": None,
                                    "cached_contributors": None,
                                    "editions": [],
                                },
                            },
                        ]
                    }
                ]
            }
        }
        respx_mock.post(HC_URL_MOCK).mock(return_value=httpx.Response(200, json=payload))
        client = _hc_client()

        results = await client.get_user_books()

        assert len(results) == 1
        assert results[0].book.title == "Valid Book"


class TestHardcoverSearchByIsbn:
    @pytest.mark.asyncio
    async def test_search_by_isbn_returns_edition_list(self, respx_mock):
        payload = {
            "data": {
                "editions": [
                    {
                        "id": 500,
                        "book": {
                            "id": 50,
                            "title": "Fahrenheit 451",
                            "slug": "fahrenheit-451",
                        },
                    }
                ]
            }
        }
        respx_mock.post(HC_URL_MOCK).mock(return_value=httpx.Response(200, json=payload))
        client = _hc_client()

        results = await client.search_by_isbn("9781451673319")

        assert len(results) == 1
        assert results[0]["id"] == 500
        assert results[0]["book"]["title"] == "Fahrenheit 451"

    @pytest.mark.asyncio
    async def test_search_by_isbn_returns_empty_list_when_no_match(self, respx_mock):
        respx_mock.post(HC_URL_MOCK).mock(
            return_value=httpx.Response(200, json={"data": {"editions": []}})
        )
        client = _hc_client()

        results = await client.search_by_isbn("0000000000000")

        assert results == []


class TestHardcoverSearchByAsin:
    @pytest.mark.asyncio
    async def test_search_by_asin_returns_edition_list(self, respx_mock):
        payload = {
            "data": {
                "editions": [
                    {
                        "id": 600,
                        "book": {
                            "id": 60,
                            "title": "The Martian",
                            "slug": "the-martian",
                        },
                    }
                ]
            }
        }
        respx_mock.post(HC_URL_MOCK).mock(return_value=httpx.Response(200, json=payload))
        client = _hc_client()

        results = await client.search_by_asin("B00B5DCPEY")

        assert len(results) == 1
        assert results[0]["id"] == 600
        assert results[0]["book"]["title"] == "The Martian"

    @pytest.mark.asyncio
    async def test_search_by_asin_returns_empty_list_when_no_match(self, respx_mock):
        respx_mock.post(HC_URL_MOCK).mock(
            return_value=httpx.Response(200, json={"data": {"editions": []}})
        )
        client = _hc_client()

        results = await client.search_by_asin("BXXXXXXXX")

        assert results == []


class TestHardcoverSetBookStatus:
    @pytest.mark.asyncio
    async def test_set_book_status_returns_inserted_record(self, respx_mock):
        payload = {"data": {"insert_user_book": {"id": 999}}}
        respx_mock.post(HC_URL_MOCK).mock(return_value=httpx.Response(200, json=payload))
        client = _hc_client()

        result = await client.set_book_status(book_id=42, status_id=1)

        assert result == {"id": 999}

    @pytest.mark.asyncio
    async def test_set_book_status_returns_empty_dict_when_server_omits_key(
        self, respx_mock
    ):
        respx_mock.post(HC_URL_MOCK).mock(
            return_value=httpx.Response(200, json={"data": {}})
        )
        client = _hc_client()

        result = await client.set_book_status(book_id=1, status_id=2)

        assert result == {}


class TestHardcoverAuthError:
    @pytest.mark.asyncio
    async def test_auth_error_raised_on_401_response(self, respx_mock):
        respx_mock.post(HC_URL_MOCK).mock(return_value=httpx.Response(401))
        client = _hc_client()

        with pytest.raises(HardcoverAuthError):
            await client.get_me()


class TestHardcoverRetryOn429:
    @pytest.mark.asyncio
    async def test_retries_on_429_and_succeeds_on_subsequent_200(self, respx_mock):
        """Tenacity retries after 429 and returns the successful 200 result."""
        success_payload = {"data": {"me": [{"id": 1, "username": "retried"}]}}
        call_count = 0

        def side_effect(request):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return httpx.Response(429)
            return httpx.Response(200, json=success_payload)

        respx_mock.post(HC_URL_MOCK).mock(side_effect=side_effect)

        import tenacity
        original_wait = HardcoverClient._request.retry.wait
        HardcoverClient._request.retry.wait = tenacity.wait_none()
        try:
            client = _hc_client()
            result = await client.get_me()
        finally:
            HardcoverClient._request.retry.wait = original_wait

        assert result["username"] == "retried"
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_raises_rate_limit_error_after_all_retries_exhausted(
        self, respx_mock
    ):
        respx_mock.post(HC_URL_MOCK).mock(return_value=httpx.Response(429))

        import tenacity
        original_wait = HardcoverClient._request.retry.wait
        HardcoverClient._request.retry.wait = tenacity.wait_none()
        try:
            client = _hc_client()
            with pytest.raises(HardcoverRateLimitError):
                await client.get_me()
        finally:
            HardcoverClient._request.retry.wait = original_wait


class TestHardcoverConnectionTest:
    @pytest.mark.asyncio
    async def test_connection_test_returns_user_info_dict(self, respx_mock):
        payload = {"data": {"me": [{"id": 7, "username": "testuser"}]}}
        respx_mock.post(HC_URL_MOCK).mock(return_value=httpx.Response(200, json=payload))
        client = _hc_client()

        result = await client.test_connection()

        assert result["id"] == 7
        assert result["username"] == "testuser"


# ===========================================================================
# AudiobookshelfClient Tests
# ===========================================================================


class TestABSGetMe:
    @pytest.mark.asyncio
    async def test_get_me_returns_user_dict(self, respx_mock):
        respx_mock.get(f"{ABS_BASE}/api/me").mock(
            return_value=httpx.Response(
                200,
                json={"id": "u1", "username": "absuser", "type": "admin"},
            )
        )
        client = _abs_client()

        result = await client.get_me()

        assert result["id"] == "u1"
        assert result["username"] == "absuser"
        assert result["type"] == "admin"


class TestABSGetLibraries:
    @pytest.mark.asyncio
    async def test_get_libraries_returns_list_of_abs_library(self, respx_mock):
        respx_mock.get(f"{ABS_BASE}/api/libraries").mock(
            return_value=httpx.Response(
                200,
                json={
                    "libraries": [
                        {"id": "lib1", "name": "Audiobooks", "mediaType": "book"},
                        {"id": "lib2", "name": "Podcasts", "mediaType": "podcast"},
                    ]
                },
            )
        )
        client = _abs_client()

        results = await client.get_libraries()

        assert len(results) == 2
        assert all(isinstance(lib, ABSLibrary) for lib in results)
        assert results[0].id == "lib1"
        assert results[0].name == "Audiobooks"
        assert results[1].id == "lib2"

    @pytest.mark.asyncio
    async def test_get_libraries_returns_empty_list_when_none_exist(
        self, respx_mock
    ):
        respx_mock.get(f"{ABS_BASE}/api/libraries").mock(
            return_value=httpx.Response(200, json={"libraries": []})
        )
        client = _abs_client()

        results = await client.get_libraries()

        assert results == []


class TestABSGetLibraryItems:
    @pytest.mark.asyncio
    async def test_get_library_items_returns_list_of_abs_library_item(
        self, respx_mock
    ):
        respx_mock.get(f"{ABS_BASE}/api/libraries/lib1/items").mock(
            return_value=httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "id": "li_001",
                            "media": {
                                "metadata": {
                                    "title": "Project Hail Mary",
                                    "authorName": "Andy Weir",
                                    "isbn": "9780593135204",
                                    "asin": "B08FHBV4ZX",
                                }
                            },
                        }
                    ]
                },
            )
        )
        client = _abs_client()

        results = await client.get_library_items("lib1")

        assert len(results) == 1
        item = results[0]
        assert isinstance(item, ABSLibraryItem)
        assert item.id == "li_001"
        assert item.title == "Project Hail Mary"
        assert item.author == "Andy Weir"
        assert item.isbn == "9780593135204"
        assert item.asin == "B08FHBV4ZX"

    @pytest.mark.asyncio
    async def test_get_library_items_passes_pagination_params(self, respx_mock):
        route = respx_mock.get(f"{ABS_BASE}/api/libraries/lib1/items").mock(
            return_value=httpx.Response(200, json={"results": []})
        )
        client = _abs_client()

        await client.get_library_items("lib1", limit=50, page=2)

        called_url = str(route.calls[0].request.url)
        assert "limit=50" in called_url
        assert "page=2" in called_url


class TestABSGetCollections:
    @pytest.mark.asyncio
    async def test_get_collections_returns_list_of_abs_collection(
        self, respx_mock
    ):
        respx_mock.get(f"{ABS_BASE}/api/libraries/lib1/collections").mock(
            return_value=httpx.Response(
                200,
                json={
                    "collections": [
                        {
                            "id": "col1",
                            "name": "Sci-Fi",
                            "libraryId": "lib1",
                            "books": [],
                        },
                        {
                            "id": "col2",
                            "name": "Fantasy",
                            "libraryId": "lib1",
                            "books": [],
                        },
                    ]
                },
            )
        )
        client = _abs_client()

        results = await client.get_collections("lib1")

        assert len(results) == 2
        assert all(isinstance(c, ABSCollection) for c in results)
        assert results[0].id == "col1"
        assert results[0].name == "Sci-Fi"
        assert results[1].name == "Fantasy"


class TestABSCreateCollection:
    @pytest.mark.asyncio
    async def test_create_collection_returns_new_abs_collection(self, respx_mock):
        respx_mock.post(f"{ABS_BASE}/api/collections").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "col_new",
                    "name": "My New Collection",
                    "libraryId": "lib1",
                    "books": [],
                },
            )
        )
        client = _abs_client()

        result = await client.create_collection(
            library_id="lib1", name="My New Collection"
        )

        assert isinstance(result, ABSCollection)
        assert result.id == "col_new"
        assert result.name == "My New Collection"
        assert result.libraryId == "lib1"

    @pytest.mark.asyncio
    async def test_create_collection_sends_correct_request_body(self, respx_mock):
        route = respx_mock.post(f"{ABS_BASE}/api/collections").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "col_x",
                    "name": "Reads",
                    "libraryId": "lib2",
                    "books": [],
                },
            )
        )
        client = _abs_client()

        await client.create_collection(library_id="lib2", name="Reads")

        body = json.loads(route.calls[0].request.content)
        assert body["libraryId"] == "lib2"
        assert body["name"] == "Reads"
        assert "books" not in body

    @pytest.mark.asyncio
    async def test_create_collection_with_books(self, respx_mock):
        route = respx_mock.post(f"{ABS_BASE}/api/collections").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "col_y",
                    "name": "Want to Read",
                    "libraryId": "lib1",
                    "books": [{"id": "item1"}, {"id": "item2"}],
                },
            )
        )
        client = _abs_client()

        await client.create_collection(
            library_id="lib1", name="Want to Read", books=["item1", "item2"]
        )

        body = json.loads(route.calls[0].request.content)
        assert body["libraryId"] == "lib1"
        assert body["name"] == "Want to Read"
        assert body["books"] == ["item1", "item2"]


class TestABSBatchAddToCollection:
    @pytest.mark.asyncio
    async def test_batch_add_to_collection_returns_response_dict(
        self, respx_mock
    ):
        respx_mock.post(f"{ABS_BASE}/api/collections/col1/batch/add").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "col1",
                    "name": "Sci-Fi",
                    "libraryId": "lib1",
                    "books": [],
                },
            )
        )
        client = _abs_client()

        result = await client.batch_add_to_collection(
            collection_id="col1", item_ids=["li_001", "li_002"]
        )

        assert isinstance(result, dict)
        assert result["id"] == "col1"

    @pytest.mark.asyncio
    async def test_batch_add_to_collection_sends_books_in_request_body(
        self, respx_mock
    ):
        route = respx_mock.post(
            f"{ABS_BASE}/api/collections/col1/batch/add"
        ).mock(
            return_value=httpx.Response(
                200, json={"id": "col1", "name": "x", "books": []}
            )
        )
        client = _abs_client()

        await client.batch_add_to_collection("col1", ["li_001", "li_002"])

        body = json.loads(route.calls[0].request.content)
        assert body["books"] == ["li_001", "li_002"]


class TestABSGetPlaylists:
    @pytest.mark.asyncio
    async def test_get_playlists_returns_list_of_abs_playlist(self, respx_mock):
        respx_mock.get(f"{ABS_BASE}/api/playlists").mock(
            return_value=httpx.Response(
                200,
                json={
                    "playlists": [
                        {
                            "id": "pl1",
                            "name": "Road Trip",
                            "libraryId": "lib1",
                            "items": [],
                        }
                    ]
                },
            )
        )
        client = _abs_client()

        results = await client.get_playlists()

        assert len(results) == 1
        pl = results[0]
        assert isinstance(pl, ABSPlaylist)
        assert pl.id == "pl1"
        assert pl.name == "Road Trip"

    @pytest.mark.asyncio
    async def test_get_playlists_returns_empty_list_when_none_exist(
        self, respx_mock
    ):
        respx_mock.get(f"{ABS_BASE}/api/playlists").mock(
            return_value=httpx.Response(200, json={"playlists": []})
        )
        client = _abs_client()

        results = await client.get_playlists()

        assert results == []


class TestABSUpdateProgress:
    @pytest.mark.asyncio
    async def test_update_progress_sends_patch_and_returns_dict(self, respx_mock):
        respx_mock.patch(f"{ABS_BASE}/api/me/progress/li_001").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        client = _abs_client()

        result = await client.update_progress(
            item_id="li_001",
            progress=0.75,
            current_time=2700.0,
            is_finished=False,
            duration=3600.0,
        )

        assert result == {"ok": True}

    @pytest.mark.asyncio
    async def test_update_progress_sends_correct_fields_in_body(self, respx_mock):
        route = respx_mock.patch(f"{ABS_BASE}/api/me/progress/li_001").mock(
            return_value=httpx.Response(200, json={})
        )
        client = _abs_client()

        await client.update_progress(
            item_id="li_001",
            progress=1.0,
            current_time=3600.0,
            is_finished=True,
            duration=3600.0,
        )

        body = json.loads(route.calls[0].request.content)
        assert body["progress"] == 1.0
        assert body["currentTime"] == 3600.0
        assert body["isFinished"] is True
        assert body["duration"] == 3600.0

    @pytest.mark.asyncio
    async def test_update_progress_returns_empty_dict_on_empty_response_body(
        self, respx_mock
    ):
        respx_mock.patch(f"{ABS_BASE}/api/me/progress/li_001").mock(
            return_value=httpx.Response(200, content=b"")
        )
        client = _abs_client()

        result = await client.update_progress("li_001", 0.5)

        assert result == {}


class TestABSAuthError:
    @pytest.mark.asyncio
    async def test_auth_error_raised_on_401_response(self, respx_mock):
        respx_mock.get(f"{ABS_BASE}/api/me").mock(
            return_value=httpx.Response(401, text="Unauthorized")
        )
        client = _abs_client()

        with pytest.raises(ABSAuthError):
            await client.get_me()

    @pytest.mark.asyncio
    async def test_auth_error_raised_on_403_response(self, respx_mock):
        respx_mock.get(f"{ABS_BASE}/api/me").mock(
            return_value=httpx.Response(403, text="Forbidden")
        )
        client = _abs_client()

        with pytest.raises(ABSAuthError):
            await client.get_me()


class TestABSConnectionError:
    @pytest.mark.asyncio
    async def test_connection_error_raised_when_server_unreachable(
        self, respx_mock
    ):
        respx_mock.get(f"{ABS_BASE}/api/me").mock(
            side_effect=httpx.ConnectError("Connection refused")
        )
        client = _abs_client()

        with pytest.raises(ABSConnectionError, match="Could not connect"):
            await client.get_me()

    @pytest.mark.asyncio
    async def test_connection_error_raised_on_generic_request_error(
        self, respx_mock
    ):
        respx_mock.get(f"{ABS_BASE}/api/me").mock(
            side_effect=httpx.TimeoutException("timed out")
        )
        client = _abs_client()

        with pytest.raises(ABSConnectionError):
            await client.get_me()


class TestABSConnectionTest:
    @pytest.mark.asyncio
    async def test_connection_test_returns_user_libraries_and_admin_flag(
        self, respx_mock
    ):
        respx_mock.get(f"{ABS_BASE}/api/me").mock(
            return_value=httpx.Response(
                200,
                json={"id": "u1", "username": "admin_user", "type": "admin"},
            )
        )
        respx_mock.get(f"{ABS_BASE}/api/libraries").mock(
            return_value=httpx.Response(
                200,
                json={
                    "libraries": [
                        {"id": "lib1", "name": "Audiobooks", "mediaType": "book"}
                    ]
                },
            )
        )
        client = _abs_client()

        result = await client.test_connection()

        assert result["user"]["username"] == "admin_user"
        assert result["is_admin"] is True
        assert len(result["libraries"]) == 1
        assert result["libraries"][0]["id"] == "lib1"

    @pytest.mark.asyncio
    async def test_connection_test_is_admin_false_for_non_admin_user(
        self, respx_mock
    ):
        respx_mock.get(f"{ABS_BASE}/api/me").mock(
            return_value=httpx.Response(
                200,
                json={"id": "u2", "username": "regular", "type": "user"},
            )
        )
        respx_mock.get(f"{ABS_BASE}/api/libraries").mock(
            return_value=httpx.Response(200, json={"libraries": []})
        )
        client = _abs_client()

        result = await client.test_connection()

        assert result["is_admin"] is False
