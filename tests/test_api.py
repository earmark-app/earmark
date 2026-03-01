"""Comprehensive tests for the Earmark REST API."""
from __future__ import annotations

import os
import tempfile

# Set DATA_DIR before any app imports to avoid /data path issues
os.environ["DATA_DIR"] = tempfile.mkdtemp()

import pytest
from fastapi.testclient import TestClient

from src.db import Database
from src.main import app
from src.web.routes import get_db, set_db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db(tmp_path):
    """Create a temp SQLite database for testing."""
    db_path = str(tmp_path / "test_api.db")
    database = Database(db_path)
    database.init_schema()
    return database


@pytest.fixture
def client(db):
    """Create a FastAPI TestClient with the test DB wired in."""
    set_db(db)
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def sample_user_payload():
    return {
        "name": "Alice",
        "hardcover_token": "hc_token_secret",
        "abs_url": "http://abs.local:13378",
        "abs_api_key": "abs_key_secret",
        "abs_library_ids": ["lib1"],
        "enabled": True,
    }


@pytest.fixture
def created_user(client, sample_user_payload):
    """POST a user and return the response JSON (tokens masked)."""
    resp = client.post("/api/users", json=sample_user_payload)
    assert resp.status_code == 201
    return resp.json()


@pytest.fixture
def sample_rule_payload(created_user):
    return {
        "user_id": created_user["id"],
        "direction": "hc_to_abs",
        "hc_status_id": 1,
        "abs_target_type": "collection",
        "abs_target_name": "Want to Read",
        "abs_library_id": "lib1",
        "remove_stale": True,
        "enabled": True,
    }


@pytest.fixture
def created_rule(client, sample_rule_payload):
    resp = client.post("/api/rules", json=sample_rule_payload)
    assert resp.status_code == 201
    return resp.json()


# ---------------------------------------------------------------------------
# TestHealth
# ---------------------------------------------------------------------------


class TestHealth:
    def test_health_returns_ok(self, client):
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "version" in data


# ---------------------------------------------------------------------------
# TestUsersCRUD
# ---------------------------------------------------------------------------


class TestUsersCRUD:
    def test_create_user(self, client, sample_user_payload):
        resp = client.post("/api/users", json=sample_user_payload)
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "Alice"
        assert data["hardcover_token"] == "***"
        assert data["abs_api_key"] == "***"
        assert data["abs_url"] == "http://abs.local:13378"
        assert "id" in data

    def test_list_users_masks_tokens(self, client, created_user):
        resp = client.get("/api/users")
        assert resp.status_code == 200
        users = resp.json()
        assert len(users) >= 1
        found = next(u for u in users if u["id"] == created_user["id"])
        assert found["hardcover_token"] == "***"
        assert found["abs_api_key"] == "***"

    def test_update_user(self, client, created_user):
        user_id = created_user["id"]
        resp = client.put(f"/api/users/{user_id}", json={"name": "Bob"})
        assert resp.status_code == 200
        assert resp.json()["name"] == "Bob"

    def test_update_user_masked_token_not_overwritten(self, client, db, created_user):
        user_id = created_user["id"]
        # Send masked tokens -- the real tokens should be preserved
        resp = client.put(
            f"/api/users/{user_id}",
            json={"hardcover_token": "***", "abs_api_key": "***"},
        )
        assert resp.status_code == 200
        # Verify real tokens are untouched in the database
        raw = db.get_user(user_id)
        assert raw["hardcover_token"] == "hc_token_secret"
        assert raw["abs_api_key"] == "abs_key_secret"

    def test_delete_user(self, client, created_user):
        user_id = created_user["id"]
        resp = client.delete(f"/api/users/{user_id}")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        # Confirm gone
        resp = client.get("/api/users")
        ids = [u["id"] for u in resp.json()]
        assert user_id not in ids

    def test_delete_user_not_found(self, client):
        resp = client.delete("/api/users/nonexistent_id")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# TestRulesCRUD
# ---------------------------------------------------------------------------


class TestRulesCRUD:
    def test_create_rule(self, client, sample_rule_payload):
        resp = client.post("/api/rules", json=sample_rule_payload)
        assert resp.status_code == 201
        data = resp.json()
        assert data["direction"] == "hc_to_abs"
        assert data["abs_target_name"] == "Want to Read"
        assert "id" in data

    def test_create_rule_invalid_user(self, client):
        payload = {
            "user_id": "no_such_user",
            "direction": "hc_to_abs",
            "hc_status_id": 1,
            "abs_target_type": "collection",
            "abs_target_name": "My List",
            "abs_library_id": "lib1",
        }
        resp = client.post("/api/rules", json=payload)
        assert resp.status_code == 404

    def test_list_rules(self, client, created_rule):
        resp = client.get("/api/rules")
        assert resp.status_code == 200
        rules = resp.json()
        assert len(rules) >= 1

    def test_list_rules_filter_by_user(self, client, created_rule):
        user_id = created_rule["user_id"]
        resp = client.get(f"/api/rules?user_id={user_id}")
        assert resp.status_code == 200
        rules = resp.json()
        assert all(r["user_id"] == user_id for r in rules)

    def test_update_rule(self, client, created_rule):
        rule_id = created_rule["id"]
        resp = client.put(
            f"/api/rules/{rule_id}",
            json={"abs_target_name": "Currently Reading"},
        )
        assert resp.status_code == 200
        assert resp.json()["abs_target_name"] == "Currently Reading"

    def test_delete_rule(self, client, created_rule):
        rule_id = created_rule["id"]
        resp = client.delete(f"/api/rules/{rule_id}")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True


# ---------------------------------------------------------------------------
# TestMappings
# ---------------------------------------------------------------------------


class TestMappings:
    @pytest.fixture
    def mapping_payload(self, created_user):
        return {
            "user_id": created_user["id"],
            "hardcover_book_id": 42,
            "abs_library_item_id": "li_001",
            "match_method": "isbn",
            "match_confidence": 1.0,
            "title": "Project Hail Mary",
        }

    def test_create_mapping(self, client, mapping_payload):
        resp = client.post("/api/mappings", json=mapping_payload)
        assert resp.status_code == 201
        data = resp.json()
        assert data["hardcover_book_id"] == 42
        assert data["match_method"] == "isbn"

    def test_list_mappings_with_filter(self, client, mapping_payload):
        client.post("/api/mappings", json=mapping_payload)
        # Filter by user_id
        resp = client.get(f"/api/mappings?user_id={mapping_payload['user_id']}")
        assert resp.status_code == 200
        mappings = resp.json()
        assert len(mappings) >= 1
        assert all(m["user_id"] == mapping_payload["user_id"] for m in mappings)
        # Filter by method
        resp = client.get("/api/mappings?method=isbn")
        assert resp.status_code == 200
        assert all(m["match_method"] == "isbn" for m in resp.json())

    def test_delete_mapping(self, client, mapping_payload):
        create_resp = client.post("/api/mappings", json=mapping_payload)
        mapping_id = create_resp.json()["id"]
        resp = client.delete(f"/api/mappings/{mapping_id}")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True


# ---------------------------------------------------------------------------
# TestSyncLog
# ---------------------------------------------------------------------------


class TestSyncLog:
    def test_list_log_paginated(self, client, db):
        # Seed some log entries
        db.add_sync_log(action="sync_start", details={"msg": "Starting"})
        db.add_sync_log(action="sync_complete", details={"msg": "Done"})
        resp = client.get("/api/log?limit=10&offset=0")
        assert resp.status_code == 200
        data = resp.json()
        assert "entries" in data
        assert "total" in data
        assert data["total"] >= 2

    def test_clear_log(self, client, db):
        db.add_sync_log(action="sync_start")
        resp = client.delete("/api/log")
        assert resp.status_code == 200
        assert "deleted" in resp.json()
        # Verify empty
        resp = client.get("/api/log")
        assert resp.json()["total"] == 0


# ---------------------------------------------------------------------------
# TestSettings
# ---------------------------------------------------------------------------


class TestSettings:
    def test_get_settings_defaults(self, client):
        resp = client.get("/api/settings")
        assert resp.status_code == 200
        data = resp.json()
        assert data["sync_interval"] == "*/15 * * * *"
        assert data["dry_run"] is False
        assert data["log_retention_days"] == 30
        assert data["fuzzy_match_threshold"] == 0.85

    def test_update_settings(self, client):
        resp = client.put(
            "/api/settings",
            json={"dry_run": True, "log_retention_days": 7},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["dry_run"] is True
        assert data["log_retention_days"] == 7


# ---------------------------------------------------------------------------
# TestSyncTrigger
# ---------------------------------------------------------------------------


class TestSyncTrigger:
    def test_sync_returns_started(self, client):
        resp = client.post("/api/sync")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "started"

    def test_sync_conflict_when_running(self, client):
        # Manually flip the module-level flag to simulate a running sync
        import src.web.routes as routes_mod

        routes_mod._sync_running = True
        try:
            resp = client.post("/api/sync")
            assert resp.status_code == 409
        finally:
            routes_mod._sync_running = False


# ---------------------------------------------------------------------------
# TestExportImport
# ---------------------------------------------------------------------------


class TestExportImport:
    def test_export_redacts_tokens(self, client, created_user):
        resp = client.get("/api/export")
        assert resp.status_code == 200
        data = resp.json()
        assert "users" in data
        assert len(data["users"]) >= 1
        exported = data["users"][0]
        assert exported["hardcover_token"] == "REDACTED"
        assert exported["abs_api_key"] == "REDACTED"

    def test_import_creates_users(self, client, db):
        import_data = {
            "users": [
                {
                    "name": "Imported User",
                    "hardcover_token": "hc_imported_token",
                    "abs_url": "http://abs.imported.local",
                    "abs_api_key": "abs_imported_key",
                    "abs_library_ids": [],
                    "enabled": True,
                }
            ],
            "rules": [],
        }
        resp = client.post("/api/import", json=import_data)
        assert resp.status_code == 200
        data = resp.json()
        assert data["imported_users"] == 1
        # Verify user exists in DB
        users = db.list_users()
        names = [u["name"] for u in users]
        assert "Imported User" in names


# ---------------------------------------------------------------------------
# TestAuth
# ---------------------------------------------------------------------------


class TestAuth:
    def test_auth_required_when_password_set(self, client, monkeypatch):
        from src import config

        monkeypatch.setattr(config.settings, "gui_password", "secret123")
        resp = client.get("/api/users")
        assert resp.status_code == 401

    def test_auth_succeeds_with_correct_credentials(self, client, monkeypatch):
        from src import config

        monkeypatch.setattr(config.settings, "gui_password", "secret123")
        resp = client.get("/api/users", auth=("admin", "secret123"))
        assert resp.status_code == 200
