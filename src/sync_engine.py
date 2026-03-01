"""Earmark sync engine — orchestrates bidirectional sync between Hardcover and Audiobookshelf."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
from pathlib import Path
from typing import Optional

from src.config import Settings, settings
from src.db import Database
from src.matching.book_matcher import BookMatcher
from src.models import HardcoverUserBook
from src.platforms.audiobookshelf import AudiobookshelfClient, ABSError
from src.platforms.hardcover import HardcoverClient, HardcoverAuthError, HardcoverError

logger = logging.getLogger(__name__)


class SyncLock:
    """File-based sync lock with PID-based stale detection."""

    def __init__(self, lock_path: str):
        self.lock_path = lock_path

    def acquire(self) -> bool:
        """Try to acquire the lock. Returns True if acquired, False if already locked."""
        lock_file = Path(self.lock_path)
        if lock_file.exists():
            try:
                stored_pid = int(lock_file.read_text().strip())
                # Check if the process is still alive
                try:
                    os.kill(stored_pid, 0)
                    # Process exists — lock is held
                    return False
                except OSError:
                    # Process is dead — stale lock
                    logger.warning("Removing stale lock (PID %d no longer running)", stored_pid)
                    lock_file.unlink(missing_ok=True)
            except (ValueError, OSError):
                # Corrupt lock file — remove it
                logger.warning("Removing corrupt lock file")
                lock_file.unlink(missing_ok=True)

        try:
            lock_file.parent.mkdir(parents=True, exist_ok=True)
            lock_file.write_text(str(os.getpid()))
            return True
        except OSError as e:
            logger.error("Failed to create lock file: %s", e)
            return False

    def release(self):
        """Release the lock."""
        lock_file = Path(self.lock_path)
        try:
            if lock_file.exists():
                stored_pid = int(lock_file.read_text().strip())
                if stored_pid == os.getpid():
                    lock_file.unlink(missing_ok=True)
        except (ValueError, OSError):
            lock_file.unlink(missing_ok=True)


class SyncEngine:
    def __init__(self, db: Database, config: Settings):
        self.db = db
        self.config = config
        self.lock = SyncLock(config.lock_path)

    @classmethod
    def from_config(cls) -> SyncEngine:
        db = Database(settings.db_path)
        db.init_schema()
        return cls(db, settings)

    async def run_all(self) -> dict:
        """Run sync for all enabled users. Returns summary dict."""
        if not self.lock.acquire():
            logger.info("Sync already in progress, skipping")
            return {"status": "locked", "message": "Sync already in progress"}

        try:
            self._cleanup_old_logs()
            users = self.db.list_users()
            enabled_users = [u for u in users if u.get("enabled")]

            if not enabled_users:
                logger.info("No enabled users, nothing to sync")
                return {"status": "ok", "users_synced": 0}

            results = {}
            for user in enabled_users:
                try:
                    result = await self.run_user(user["id"])
                    results[user["id"]] = result
                except Exception:
                    logger.exception("Error syncing user %s (%s)", user["name"], user["id"])
                    self.db.add_sync_log(
                        action="error",
                        user_id=user["id"],
                        details={"error": f"Sync failed for user {user['name']}"},
                    )
                    results[user["id"]] = {"status": "error"}

            return {"status": "ok", "users_synced": len(results), "results": results}
        finally:
            self.lock.release()

    async def run_user(self, user_id: str) -> dict:
        """Run sync for a single user."""
        user = self.db.get_user(user_id)
        if not user:
            logger.error("User %s not found", user_id)
            return {"status": "error", "message": "User not found"}

        if not user.get("enabled"):
            logger.info("User %s is disabled, skipping", user["name"])
            return {"status": "skipped", "message": "User disabled"}

        logger.info("Starting sync for user: %s", user["name"])

        hc_client = HardcoverClient(user["hardcover_token"])
        abs_client = AudiobookshelfClient(user["abs_url"], user["abs_api_key"])

        try:
            rules = self.db.list_sync_rules(user_id=user["id"])
            enabled_rules = [r for r in rules if r.get("enabled")]

            hc_to_abs_rules = [
                r for r in enabled_rules
                if r["direction"] in ("hc_to_abs", "bidirectional")
            ]
            abs_to_hc_rules = [
                r for r in enabled_rules
                if r["direction"] in ("abs_to_hc", "bidirectional")
            ]

            hc_results = await self._run_hc_to_abs(user, hc_client, abs_client, hc_to_abs_rules)
            abs_results = await self._run_abs_to_hc(user, hc_client, abs_client, abs_to_hc_rules)

            return {
                "status": "ok",
                "hc_to_abs": hc_results,
                "abs_to_hc": abs_results,
            }
        except HardcoverAuthError as e:
            logger.error("Hardcover auth failed for user %s: %s", user["name"], e)
            self.db.update_user(user["id"], {"needs_token_refresh": True})
            self.db.add_sync_log(
                action="error", user_id=user["id"],
                details={"error": str(e), "platform": "hardcover", "auth_failed": True},
            )
            return {"status": "error", "message": str(e)}
        except HardcoverError as e:
            logger.error("Hardcover API error for user %s: %s", user["name"], e)
            self.db.add_sync_log(
                action="error", user_id=user["id"],
                details={"error": str(e), "platform": "hardcover"},
            )
            return {"status": "error", "message": str(e)}
        except ABSError as e:
            logger.error("ABS API error for user %s: %s", user["name"], e)
            self.db.add_sync_log(
                action="error", user_id=user["id"],
                details={"error": str(e), "platform": "audiobookshelf"},
            )
            return {"status": "error", "message": str(e)}
        finally:
            await hc_client.close()
            await abs_client.close()

    async def _run_hc_to_abs(
        self,
        user: dict,
        hc_client: HardcoverClient,
        abs_client: AudiobookshelfClient,
        rules: list[dict],
    ) -> dict:
        """Phase 1: Sync Hardcover lists/statuses to ABS collections/playlists."""
        if not rules:
            return {"rules_processed": 0}

        results = {"rules_processed": 0, "added": 0, "removed": 0}

        for rule in rules:
            try:
                await self._sync_rule_hc_to_abs(user, hc_client, abs_client, rule)
                results["rules_processed"] += 1
            except Exception:
                logger.exception(
                    "Error processing HC->ABS rule %s for user %s",
                    rule["id"], user["name"],
                )
                self.db.add_sync_log(
                    action="error", user_id=user["id"], rule_id=rule["id"],
                    direction="hc_to_abs",
                    details={"error": f"Rule {rule['id']} failed"},
                )

        return results

    async def _sync_rule_hc_to_abs(
        self,
        user: dict,
        hc_client: HardcoverClient,
        abs_client: AudiobookshelfClient,
        rule: dict,
    ):
        """Sync a single HC->ABS rule."""
        rule_id = rule["id"]
        logger.info("Processing HC->ABS rule %s: %s", rule_id, rule["abs_target_name"])

        # 1. Fetch HC books
        if rule.get("hc_status_id"):
            hc_user_books = await hc_client.get_user_books(status_id=rule["hc_status_id"])
        elif rule.get("hc_list_id"):
            list_books = await hc_client.get_list_books(rule["hc_list_id"])
            hc_user_books = list_books
        else:
            logger.warning("Rule %s has no hc_status_id or hc_list_id, skipping", rule_id)
            return

        hc_books = []
        for ub in hc_user_books:
            if isinstance(ub, HardcoverUserBook):
                hc_books.append(ub.book)
            elif isinstance(ub, dict) and "book" in ub:
                from src.models import HardcoverBook
                hc_books.append(HardcoverBook(**ub["book"]) if isinstance(ub["book"], dict) else ub["book"])
            else:
                hc_books.append(ub)

        # Empty list safety check
        previous_count = self.db.count_sync_state_for_rule(rule_id)
        if len(hc_books) == 0 and previous_count > 0:
            logger.warning(
                "HC returned 0 books for rule %s but previous sync had %d — "
                "skipping removal to prevent mass delete",
                rule_id, previous_count,
            )
            self.db.add_sync_log(
                action="error", user_id=user["id"], rule_id=rule_id,
                direction="hc_to_abs",
                details={
                    "warning": "Empty HC list safety check triggered",
                    "previous_count": previous_count,
                },
            )
            return

        # 2. Get ABS library items for matching
        abs_items_raw = await abs_client.get_all_library_items(rule["abs_library_id"])
        from src.models import ABSLibraryItem
        abs_items = []
        for item in abs_items_raw:
            if isinstance(item, ABSLibraryItem):
                abs_items.append(item)
            elif isinstance(item, dict):
                abs_items.append(ABSLibraryItem(**item))

        # 3. Match HC books to ABS items
        matcher = BookMatcher(db=self.db, threshold=self._get_fuzzy_threshold())
        matched = {}  # hc_book_id -> abs_item_id
        for hc_book in hc_books:
            result = matcher.match(hc_book, abs_items, user_id=user["id"])
            if result:
                matched[hc_book.id] = result.abs_library_item_id
                self.db.add_sync_log(
                    action="match_found", user_id=user["id"], rule_id=rule_id,
                    direction="hc_to_abs",
                    details={
                        "hc_book_id": hc_book.id, "title": hc_book.title,
                        "abs_item_id": result.abs_library_item_id,
                        "method": result.method, "confidence": result.confidence,
                    },
                )
            else:
                self.db.add_sync_log(
                    action="match_failed", user_id=user["id"], rule_id=rule_id,
                    direction="hc_to_abs",
                    details={"hc_book_id": hc_book.id, "title": hc_book.title},
                )

        if not matched:
            logger.info("No matches found for rule %s", rule_id)
            return

        # 4. Ensure ABS target exists
        target_id = rule.get("abs_target_id")
        if not target_id:
            target_id = await self._ensure_abs_target(abs_client, rule)
            if target_id:
                self.db.update_sync_rule(rule_id, {"abs_target_id": target_id})

        if not target_id:
            logger.error("Could not find or create ABS target for rule %s", rule_id)
            return

        # 5. Get current items in ABS target
        current_abs_ids = await self._get_target_items(abs_client, rule, target_id)

        # 6. Compute diff
        desired_abs_ids = set(matched.values())
        to_add = desired_abs_ids - current_abs_ids
        to_remove = current_abs_ids - desired_abs_ids if rule.get("remove_stale", True) else set()

        # 7. Execute changes
        if not self.config.dry_run:
            if to_add:
                await self._add_to_target(abs_client, rule, target_id, list(to_add))
            if to_remove:
                await self._remove_from_target(abs_client, rule, target_id, list(to_remove))
        else:
            if to_add:
                logger.info("[DRY RUN] Would add %d items to %s", len(to_add), rule["abs_target_name"])
            if to_remove:
                logger.info("[DRY RUN] Would remove %d items from %s", len(to_remove), rule["abs_target_name"])

        # 8. Update sync state + log
        for hc_book_id, abs_item_id in matched.items():
            mapping = self.db.find_mapping_by_hc_book(user["id"], hc_book_id)
            if mapping:
                self.db.upsert_sync_state(rule_id, mapping["id"], "hc_to_abs")

        for item_id in to_add:
            self.db.add_sync_log(
                action="added_to_collection", user_id=user["id"], rule_id=rule_id,
                direction="hc_to_abs",
                details={"abs_item_id": item_id, "target": rule["abs_target_name"]},
            )

        for item_id in to_remove:
            # Remove sync state for removed items
            mapping = self.db.find_mapping_by_abs_item(user["id"], item_id)
            if mapping:
                self.db.delete_sync_state(rule_id, mapping["id"])
            self.db.add_sync_log(
                action="removed_from_collection", user_id=user["id"], rule_id=rule_id,
                direction="hc_to_abs",
                details={"abs_item_id": item_id, "target": rule["abs_target_name"]},
            )

    async def _run_abs_to_hc(
        self,
        user: dict,
        hc_client: HardcoverClient,
        abs_client: AudiobookshelfClient,
        rules: list[dict],
    ) -> dict:
        """Phase 2: Sync ABS progress to Hardcover statuses."""
        if not rules:
            return {"rules_processed": 0}

        results = {"rules_processed": 0, "status_updates": 0}

        try:
            # Fetch all ABS progress in one call
            me_data = await abs_client.get_me()
            progress_list = me_data.get("mediaProgress", [])
        except ABSError as e:
            logger.error("Failed to fetch ABS progress for user %s: %s", user["name"], e)
            return {"rules_processed": 0, "error": str(e)}

        if not progress_list:
            return {"rules_processed": len(rules), "status_updates": 0}

        matcher = BookMatcher(db=self.db, threshold=self._get_fuzzy_threshold())

        for progress_item in progress_list:
            abs_item_id = progress_item.get("libraryItemId", "")
            progress = progress_item.get("progress", 0.0)
            is_finished = progress_item.get("isFinished", False)

            if progress <= 0 and not is_finished:
                continue

            # Check progress state — only sync if progress increased
            prev_state = self.db.get_progress_state(user["id"], abs_item_id)
            if prev_state:
                prev_progress = prev_state.get("last_abs_progress", 0.0) or 0.0
                prev_finished = prev_state.get("last_abs_is_finished", False)
                if progress <= prev_progress and not (is_finished and not prev_finished):
                    continue

            # Find matching HC book
            mapping = self.db.find_mapping_by_abs_item(user["id"], abs_item_id)
            hc_book_id = None
            if mapping:
                hc_book_id = mapping["hardcover_book_id"]
            else:
                # Try to match via ABS library items
                # We'd need to fetch the item details and match — skip if no existing mapping
                logger.debug("No mapping found for ABS item %s, skipping progress sync", abs_item_id)
                continue

            # Determine target HC status
            if is_finished:
                target_status = 3  # Read
            elif progress > 0:
                target_status = 2  # Currently Reading

            # Highest-value-wins: never downgrade status
            if prev_state and prev_state.get("last_hc_status_id"):
                current_hc_status = prev_state["last_hc_status_id"]
                # DNF (5) is never overwritten
                if current_hc_status == 5:
                    logger.debug(
                        "Skipping progress sync for HC book %d — marked DNF",
                        hc_book_id,
                    )
                    continue
                # Read (3) should not be downgraded to Reading (2)
                if current_hc_status == 3 and target_status == 2:
                    continue

            # Execute update
            if not self.config.dry_run:
                try:
                    # Try update first, then insert if needed
                    await hc_client.update_book_status(hc_book_id, target_status)
                except HardcoverError:
                    try:
                        await hc_client.set_book_status(hc_book_id, target_status)
                    except HardcoverError:
                        logger.exception(
                            "Failed to update HC status for book %d", hc_book_id
                        )
                        continue
            else:
                logger.info(
                    "[DRY RUN] Would set HC book %d to status %d",
                    hc_book_id, target_status,
                )

            # Update progress state (loop prevention)
            self.db.upsert_progress_state(
                user_id=user["id"],
                abs_library_item_id=abs_item_id,
                hardcover_book_id=hc_book_id,
                progress=progress,
                is_finished=is_finished,
                hc_status_id=target_status,
            )

            self.db.add_sync_log(
                action="status_updated", user_id=user["id"],
                direction="abs_to_hc",
                details={
                    "hc_book_id": hc_book_id,
                    "abs_item_id": abs_item_id,
                    "progress": progress,
                    "is_finished": is_finished,
                    "target_status": target_status,
                },
            )
            results["status_updates"] += 1

        results["rules_processed"] = len(rules)
        return results

    # --- Helpers ---

    async def _ensure_abs_target(
        self, abs_client: AudiobookshelfClient, rule: dict
    ) -> Optional[str]:
        """Find or create the ABS collection/playlist for a rule."""
        target_type = rule["abs_target_type"]
        target_name = rule["abs_target_name"]
        library_id = rule["abs_library_id"]

        try:
            if target_type == "collection":
                collections = await abs_client.get_collections(library_id)
                for col in collections:
                    name = col.name if hasattr(col, "name") else col.get("name", "")
                    col_id = col.id if hasattr(col, "id") else col.get("id", "")
                    if name == target_name:
                        return col_id
                # Create new
                new_col = await abs_client.create_collection(library_id, target_name)
                return new_col.id if hasattr(new_col, "id") else new_col.get("id")
            else:  # playlist
                playlists = await abs_client.get_playlists()
                for pl in playlists:
                    name = pl.name if hasattr(pl, "name") else pl.get("name", "")
                    pl_id = pl.id if hasattr(pl, "id") else pl.get("id", "")
                    if name == target_name:
                        return pl_id
                new_pl = await abs_client.create_playlist(library_id, target_name)
                return new_pl.id if hasattr(new_pl, "id") else new_pl.get("id")
        except ABSError as e:
            logger.error("Failed to ensure ABS target: %s", e)
            return None

    async def _get_target_items(
        self, abs_client: AudiobookshelfClient, rule: dict, target_id: str
    ) -> set[str]:
        """Get current item IDs in an ABS collection/playlist."""
        try:
            if rule["abs_target_type"] == "collection":
                collections = await abs_client.get_collections(rule["abs_library_id"])
                for col in collections:
                    col_id = col.id if hasattr(col, "id") else col.get("id", "")
                    if col_id == target_id:
                        books = col.books if hasattr(col, "books") else col.get("books", [])
                        ids = set()
                        for b in books:
                            if isinstance(b, str):
                                ids.add(b)
                            elif isinstance(b, dict):
                                ids.add(b.get("id", b.get("libraryItemId", "")))
                            elif hasattr(b, "id"):
                                ids.add(b.id)
                        return ids
            else:
                playlists = await abs_client.get_playlists()
                for pl in playlists:
                    pl_id = pl.id if hasattr(pl, "id") else pl.get("id", "")
                    if pl_id == target_id:
                        items = pl.items if hasattr(pl, "items") else pl.get("items", [])
                        ids = set()
                        for i in items:
                            if isinstance(i, str):
                                ids.add(i)
                            elif isinstance(i, dict):
                                ids.add(i.get("libraryItemId", i.get("id", "")))
                            elif hasattr(i, "id"):
                                ids.add(i.id)
                        return ids
        except ABSError as e:
            logger.error("Failed to get target items: %s", e)
        return set()

    async def _add_to_target(
        self,
        abs_client: AudiobookshelfClient,
        rule: dict,
        target_id: str,
        item_ids: list[str],
    ):
        """Add items to an ABS collection/playlist."""
        try:
            if rule["abs_target_type"] == "collection":
                await abs_client.batch_add_to_collection(target_id, item_ids)
            else:
                await abs_client.batch_add_to_playlist(target_id, item_ids)
            logger.info("Added %d items to %s '%s'", len(item_ids), rule["abs_target_type"], rule["abs_target_name"])
        except ABSError as e:
            logger.error("Failed to add items to target: %s", e)

    async def _remove_from_target(
        self,
        abs_client: AudiobookshelfClient,
        rule: dict,
        target_id: str,
        item_ids: list[str],
    ):
        """Remove items from an ABS collection/playlist."""
        try:
            if rule["abs_target_type"] == "collection":
                await abs_client.batch_remove_from_collection(target_id, item_ids)
            else:
                await abs_client.batch_remove_from_playlist(target_id, item_ids)
            logger.info("Removed %d items from %s '%s'", len(item_ids), rule["abs_target_type"], rule["abs_target_name"])
        except ABSError as e:
            logger.error("Failed to remove items from target: %s", e)

    def _get_fuzzy_threshold(self) -> float:
        """Get fuzzy match threshold from settings."""
        val = self.db.get_setting("fuzzy_match_threshold")
        try:
            return float(val) if val else 0.85
        except ValueError:
            return 0.85

    def _cleanup_old_logs(self):
        """Delete sync log entries older than the configured retention period."""
        val = self.db.get_setting("log_retention_days")
        try:
            days = int(val) if val else 30
        except ValueError:
            days = 30
        if days > 0:
            from datetime import datetime, timedelta, timezone
            cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
            deleted = self.db.delete_sync_log(before_date=cutoff)
            if deleted:
                logger.info("Cleaned up %d old sync log entries", deleted)


# CLI entry point
if __name__ == "__main__":
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger.info("Starting sync engine")
    engine = SyncEngine.from_config()
    result = asyncio.run(engine.run_all())
    logger.info("Sync complete: %s", result.get("status", "unknown"))
