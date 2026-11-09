"""Tests for the persisted server-side session store.

The console restarts often while developing; a purely in-memory store signs the
operator out on every restart, which reads as "refresh logs me out".  These
tests cover the persistence path without touching the real data directory.
"""

from __future__ import annotations

import json
import os
import stat
import time
from pathlib import Path

import pytest

from app.auth import SessionStore


def test_session_survives_store_recreation(tmp_path: Path):
    path = tmp_path / "sessions.json"
    first = SessionStore(3600, path)
    session = first.create("admin")

    restarted = SessionStore(3600, path)
    restored = restarted.get(session.token)
    assert restored is not None
    assert restored.username == "admin"
    assert restored.csrf_token == session.csrf_token


def test_expired_session_is_not_restored(tmp_path: Path):
    path = tmp_path / "sessions.json"
    first = SessionStore(60, path)
    session = first.create("admin")
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload[0]["created_at"] = time.time() - 120
    path.write_text(json.dumps(payload), encoding="utf-8")

    restarted = SessionStore(60, path)
    assert restarted.get(session.token) is None
    # The stale entry is pruned from the file as well.
    assert json.loads(path.read_text(encoding="utf-8")) == []


def test_delete_is_persisted(tmp_path: Path):
    path = tmp_path / "sessions.json"
    store = SessionStore(3600, path)
    session = store.create("admin")
    store.delete(session.token)
    assert SessionStore(3600, path).get(session.token) is None


def test_revoke_others_keeps_the_current_session(tmp_path: Path):
    path = tmp_path / "sessions.json"
    store = SessionStore(3600, path)
    keep = store.create("admin")
    other = store.create("admin")

    assert store.revoke_others("admin", keep.token) == 1
    restarted = SessionStore(3600, path)
    assert restarted.get(keep.token) is not None
    assert restarted.get(other.token) is None


def test_session_file_is_private(tmp_path: Path):
    path = tmp_path / "sessions.json"
    SessionStore(3600, path).create("admin")
    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode == 0o600


def test_corrupt_file_does_not_block_login(tmp_path: Path):
    path = tmp_path / "sessions.json"
    path.write_text("{not json", encoding="utf-8")
    store = SessionStore(3600, path)
    session = store.create("admin")
    assert store.get(session.token) is not None
    assert json.loads(path.read_text(encoding="utf-8"))[0]["username"] == "admin"


def test_no_path_keeps_store_in_memory(tmp_path: Path):
    store = SessionStore(3600)
    session = store.create("admin")
    assert store.get(session.token) is not None
    assert list(tmp_path.iterdir()) == []
