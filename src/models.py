from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


# --- Health ---


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str = "0.1.0"
    last_sync: Optional[str] = None
    next_sync: Optional[str] = None


# --- Users ---


class UserCreate(BaseModel):
    name: str
    hardcover_token: str
    abs_url: str
    abs_api_key: str
    abs_library_ids: list[str] = Field(default_factory=list)
    enabled: bool = True


class UserUpdate(BaseModel):
    name: Optional[str] = None
    hardcover_token: Optional[str] = None
    abs_url: Optional[str] = None
    abs_api_key: Optional[str] = None
    abs_library_ids: Optional[list[str]] = None
    enabled: Optional[bool] = None


class UserResponse(BaseModel):
    id: str
    name: str
    hardcover_token: str = "***"
    hardcover_user_id: Optional[int] = None
    hardcover_username: Optional[str] = None
    abs_url: str
    abs_api_key: str = "***"
    abs_user_id: Optional[str] = None
    abs_username: Optional[str] = None
    abs_library_ids: list[str] = Field(default_factory=list)
    abs_is_admin: bool = False
    needs_token_refresh: bool = False
    enabled: bool = True
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class UserDB(BaseModel):
    """Internal model with real tokens — never expose directly via API."""
    id: str
    name: str
    hardcover_token: str
    hardcover_user_id: Optional[int] = None
    hardcover_username: Optional[str] = None
    abs_url: str
    abs_api_key: str
    abs_user_id: Optional[str] = None
    abs_username: Optional[str] = None
    abs_library_ids: list[str] = Field(default_factory=list)
    abs_is_admin: bool = False
    needs_token_refresh: bool = False
    enabled: bool = True
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    def to_response(self) -> UserResponse:
        return UserResponse(
            id=self.id,
            name=self.name,
            hardcover_token="***",
            hardcover_user_id=self.hardcover_user_id,
            hardcover_username=self.hardcover_username,
            abs_url=self.abs_url,
            abs_api_key="***",
            abs_user_id=self.abs_user_id,
            abs_username=self.abs_username,
            abs_library_ids=self.abs_library_ids,
            abs_is_admin=self.abs_is_admin,
            needs_token_refresh=self.needs_token_refresh,
            enabled=self.enabled,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )


class ConnectionTestResult(BaseModel):
    hardcover_ok: bool = False
    hardcover_username: Optional[str] = None
    hardcover_user_id: Optional[int] = None
    abs_ok: bool = False
    abs_username: Optional[str] = None
    abs_user_id: Optional[str] = None
    abs_is_admin: bool = False
    abs_libraries: list[dict] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


# --- Sync Rules ---


class SyncRuleCreate(BaseModel):
    user_id: str
    direction: str  # 'hc_to_abs', 'abs_to_hc', 'bidirectional'
    hc_status_id: Optional[int] = None
    hc_list_id: Optional[int] = None
    abs_target_type: str  # 'collection' or 'playlist'
    abs_target_name: str
    abs_library_id: str
    remove_stale: bool = True
    enabled: bool = True


class SyncRuleUpdate(BaseModel):
    direction: Optional[str] = None
    hc_status_id: Optional[int] = None
    hc_list_id: Optional[int] = None
    abs_target_type: Optional[str] = None
    abs_target_name: Optional[str] = None
    abs_library_id: Optional[str] = None
    remove_stale: Optional[bool] = None
    enabled: Optional[bool] = None


class SyncRuleResponse(BaseModel):
    id: str
    user_id: str
    direction: str
    hc_status_id: Optional[int] = None
    hc_list_id: Optional[int] = None
    abs_target_type: str
    abs_target_name: str
    abs_target_id: Optional[str] = None
    abs_library_id: str
    remove_stale: bool = True
    enabled: bool = True
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


# --- Book Mappings ---


class BookMappingCreate(BaseModel):
    user_id: str
    hardcover_book_id: int
    hardcover_edition_id: Optional[int] = None
    abs_library_item_id: str
    match_method: str  # 'isbn', 'asin', 'title_author'
    match_confidence: float = 1.0
    title: Optional[str] = None


class BookMappingResponse(BaseModel):
    id: str
    user_id: str
    hardcover_book_id: int
    hardcover_edition_id: Optional[int] = None
    abs_library_item_id: str
    match_method: str
    match_confidence: float
    title: Optional[str] = None
    created_at: Optional[str] = None


# --- Sync State ---


class SyncStateResponse(BaseModel):
    id: str
    rule_id: str
    book_mapping_id: str
    last_synced_at: Optional[str] = None
    sync_direction: str


# --- Progress State ---


class ProgressStateResponse(BaseModel):
    id: str
    user_id: str
    abs_library_item_id: str
    hardcover_book_id: int
    last_abs_progress: Optional[float] = None
    last_abs_is_finished: bool = False
    last_hc_status_id: Optional[int] = None
    last_synced_at: Optional[str] = None


# --- Sync Log ---


class SyncLogEntry(BaseModel):
    id: int
    user_id: Optional[str] = None
    rule_id: Optional[str] = None
    action: str
    direction: Optional[str] = None
    details: Optional[str] = None
    created_at: Optional[str] = None


class SyncLogResponse(BaseModel):
    entries: list[SyncLogEntry]
    total: int
    page: int = 0
    limit: int = 50


# --- Settings ---


class SettingsResponse(BaseModel):
    sync_interval: str = "*/15 * * * *"
    dry_run: bool = False
    log_retention_days: int = 30
    fuzzy_match_threshold: float = 0.85


class SettingsUpdate(BaseModel):
    sync_interval: Optional[str] = None
    dry_run: Optional[bool] = None
    log_retention_days: Optional[int] = None
    fuzzy_match_threshold: Optional[float] = None


# --- Match Result (internal) ---


class MatchResult(BaseModel):
    abs_library_item_id: str
    hardcover_book_id: int
    hardcover_edition_id: Optional[int] = None
    method: str  # 'asin', 'isbn', 'title_author'
    confidence: float
    title: Optional[str] = None


# --- Hardcover book models (internal) ---


class HardcoverEdition(BaseModel):
    id: int
    isbn_13: Optional[str] = None
    isbn_10: Optional[str] = None
    asin: Optional[str] = None
    format: Optional[str] = None


class HardcoverBook(BaseModel):
    id: int
    title: str
    slug: Optional[str] = None
    cached_contributors: Optional[list] = None
    editions: list[HardcoverEdition] = Field(default_factory=list)

    @property
    def author(self) -> str:
        if self.cached_contributors:
            for c in self.cached_contributors:
                if isinstance(c, dict) and c.get("author"):
                    return c["author"].get("name", "")
        return ""


class HardcoverUserBook(BaseModel):
    id: int
    status_id: int
    rating: Optional[float] = None
    book: HardcoverBook


# --- ABS models (internal) ---


class ABSMediaMetadata(BaseModel):
    title: Optional[str] = None
    authorName: Optional[str] = None
    isbn: Optional[str] = None
    asin: Optional[str] = None
    narrator: Optional[str] = None
    series: Optional[str] = None
    publishedYear: Optional[str] = None


class ABSMedia(BaseModel):
    metadata: ABSMediaMetadata = Field(default_factory=ABSMediaMetadata)


class ABSLibraryItem(BaseModel):
    id: str
    media: ABSMedia = Field(default_factory=ABSMedia)

    @property
    def title(self) -> str:
        return self.media.metadata.title or ""

    @property
    def author(self) -> str:
        return self.media.metadata.authorName or ""

    @property
    def isbn(self) -> Optional[str]:
        return self.media.metadata.isbn

    @property
    def asin(self) -> Optional[str]:
        return self.media.metadata.asin


class ABSMediaProgress(BaseModel):
    libraryItemId: str
    progress: float = 0.0
    currentTime: float = 0.0
    isFinished: bool = False
    duration: float = 0.0


class ABSLibrary(BaseModel):
    id: str
    name: str
    mediaType: Optional[str] = None


class ABSCollection(BaseModel):
    id: str
    name: str
    libraryId: Optional[str] = None
    books: list[dict] = Field(default_factory=list)


class ABSPlaylist(BaseModel):
    id: str
    name: str
    libraryId: Optional[str] = None
    items: list[dict] = Field(default_factory=list)
