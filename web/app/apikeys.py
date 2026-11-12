"""API keys for the Qwen-Image generation service.

The console is the single writer of ``web/data/api_keys.json``; the image
service reads that file read-only.  Persistence follows the same rules as
:mod:`app.modelaccess`: mode 0600, a temp file plus ``os.replace`` for an
atomic swap and a ``threading.Lock`` around every read-modify-write cycle.

Call statistics live in a separate file (``api_keys_usage.json``) that the
service writes and this store only reads, so a broken or missing usage file
must never break the page — it is ignored silently.
"""

from __future__ import annotations

import json
import os
import secrets
import threading
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

#: Access modes reported by :meth:`ApiKeyStore.access_mode`.
MODE_OPEN = "open"  # key file missing: LAN callers need no credential
MODE_KEY = "key"  # at least one enabled key
MODE_LOCKED = "locked"  # key file present but no enabled key

KEY_PREFIX = "sk-"
#: 24 random bytes -> 48 hex characters, matching the agreed contract.
KEY_HEX_BYTES = 24
MAX_DESCRIPTION_LENGTH = 200
FILE_VERSION = 1


class ApiKeyError(ValueError):
    """Invalid API key input supplied by the operator (user facing)."""


@dataclass(frozen=True)
class ApiKey:
    id: int
    key: str
    description: str
    created_at: str
    enabled: bool

    @property
    def masked(self) -> str:
        """Show only the head and tail of the secret, never the middle."""
        if len(self.key) <= 10:
            return self.key
        return self.key[:6] + "****" + self.key[-4:]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def validate_description(value: str | None) -> str:
    description = (value or "").strip()
    if len(description) > MAX_DESCRIPTION_LENGTH:
        raise ApiKeyError(
            f"描述最多 {MAX_DESCRIPTION_LENGTH} 个字符，当前 {len(description)} 个"
        )
    return description


class ApiKeyStore:
    """Thread-safe JSON store for the service's API keys.

    The key file is re-read on every operation: a corrupt or externally
    rewritten file must be visible immediately (the page shows ``error`` as a
    warning bar, exactly like ``ModelAccessStore``) and a successful write
    rewrites the file with valid content, clearing the error.
    """

    def __init__(self, path: Path, usage_path: Path | None = None) -> None:
        self.path = Path(path)
        self.usage_path = Path(usage_path) if usage_path is not None else None
        self._lock = threading.Lock()
        #: Non-``None`` when the key file could not be read; shown on the page.
        self.error: str | None = None

    # ------------------------------------------------------------ persistence
    def _load(self) -> tuple[list[ApiKey], int]:
        """Read the key file; returns ``(keys, next_id)`` and sets ``error``."""
        if not self.path.exists():
            self.error = None
            return [], 1
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            self.error = f"读取 API Key 配置失败：{exc}"
            return [], 1
        if not isinstance(raw, dict) or not isinstance(raw.get("keys"), list):
            self.error = "API Key 配置格式无效（缺少 keys 数组）"
            return [], 1
        keys: list[ApiKey] = []
        for item in raw["keys"]:
            if not isinstance(item, dict):
                continue
            key_id = item.get("id")
            secret = item.get("key")
            if not isinstance(key_id, int) or isinstance(key_id, bool):
                continue
            if not isinstance(secret, str) or not secret:
                continue
            keys.append(
                ApiKey(
                    id=key_id,
                    key=secret,
                    description=str(item.get("description") or ""),
                    created_at=str(item.get("created_at") or ""),
                    enabled=bool(item.get("enabled", True)),
                )
            )
        keys.sort(key=lambda entry: entry.id)
        highest = max((entry.id for entry in keys), default=0)
        next_id = raw.get("next_id")
        # Never hand out an id twice, whatever the file claims.
        if not isinstance(next_id, int) or isinstance(next_id, bool) or next_id <= highest:
            next_id = highest + 1
        self.error = None
        return keys, next_id

    def _persist(self, keys: list[ApiKey], next_id: int) -> None:
        payload = {
            "version": FILE_VERSION,
            "next_id": next_id,
            "keys": [
                {
                    "id": entry.id,
                    "key": entry.key,
                    "description": entry.description,
                    "created_at": entry.created_at,
                    "enabled": entry.enabled,
                }
                for entry in sorted(keys, key=lambda item: item.id)
            ],
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(
                f".{self.path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp"
            )
            tmp.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        except OSError as exc:
            raise ApiKeyError(f"保存 API Key 配置失败：{exc}") from exc

    @staticmethod
    def _find(keys: list[ApiKey], key_id: int) -> ApiKey:
        for entry in keys:
            if entry.id == key_id:
                return entry
        raise ApiKeyError(f"API Key #{key_id} 不存在")

    # ------------------------------------------------------------------ read
    def list(self) -> list[ApiKey]:
        with self._lock:
            keys, _ = self._load()
            return keys

    def access_mode(self) -> tuple[str, int]:
        """Return ``(mode, enabled_count)`` derived from the file's presence."""

        with self._lock:
            exists = self.path.exists()
            keys, _ = self._load()
        if not exists:
            return MODE_OPEN, 0
        enabled = sum(1 for entry in keys if entry.enabled)
        if enabled:
            return MODE_KEY, enabled
        return MODE_LOCKED, 0

    def usage(self) -> dict[int, dict[str, object]]:
        """Read-only call statistics keyed by id; silently empty on failure."""

        if self.usage_path is None:
            return {}
        try:
            raw = json.loads(self.usage_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(raw, dict) or not isinstance(raw.get("usage"), dict):
            return {}
        result: dict[int, dict[str, object]] = {}
        for raw_id, entry in raw["usage"].items():
            try:
                key_id = int(raw_id)
            except (TypeError, ValueError):
                continue
            if not isinstance(entry, dict):
                continue
            calls = entry.get("calls")
            if not isinstance(calls, int) or isinstance(calls, bool) or calls < 0:
                calls = 0
            last_used = entry.get("last_used_at")
            result[key_id] = {
                "calls": calls,
                "last_used_at": last_used if isinstance(last_used, str) and last_used else None,
            }
        return result

    def first_enabled_key(self) -> str | None:
        """Lowest-id enabled key, or ``None`` (open mode / nothing usable).

        Wired into ``ImageClient`` as ``api_key_provider`` so the console keeps
        working when ``GEN_STUDIO_IMAGE_API_BASE`` points at the LAN address.
        """

        with self._lock:
            if not self.path.exists():
                return None
            keys, _ = self._load()
        for entry in sorted(
            (item for item in keys if item.enabled), key=lambda item: item.id
        ):
            return entry.key
        return None

    # ----------------------------------------------------------------- write
    def create(self, description: str | None = None) -> ApiKey:
        cleaned = validate_description(description)
        with self._lock:
            keys, next_id = self._load()
            created = ApiKey(
                id=next_id,
                key=KEY_PREFIX + secrets.token_hex(KEY_HEX_BYTES),
                description=cleaned,
                created_at=_now_iso(),
                enabled=True,
            )
            keys.append(created)
            self._persist(keys, next_id + 1)
            self.error = None
            return created

    def edit(self, key_id: int, description: str | None) -> ApiKey:
        cleaned = validate_description(description)
        with self._lock:
            keys, next_id = self._load()
            current = self._find(keys, key_id)
            updated = replace(current, description=cleaned)
            keys[keys.index(current)] = updated
            self._persist(keys, next_id)
            self.error = None
            return updated

    def toggle(self, key_id: int) -> ApiKey:
        with self._lock:
            keys, next_id = self._load()
            current = self._find(keys, key_id)
            updated = replace(current, enabled=not current.enabled)
            keys[keys.index(current)] = updated
            self._persist(keys, next_id)
            self.error = None
            return updated

    def reset(self, key_id: int) -> ApiKey:
        """Replace the secret; the old value stops working immediately."""

        with self._lock:
            keys, next_id = self._load()
            current = self._find(keys, key_id)
            updated = replace(current, key=KEY_PREFIX + secrets.token_hex(KEY_HEX_BYTES))
            keys[keys.index(current)] = updated
            self._persist(keys, next_id)
            self.error = None
            return updated

    def delete(self, key_id: int) -> None:
        with self._lock:
            keys, next_id = self._load()
            current = self._find(keys, key_id)
            keys.remove(current)
            # next_id stays monotonic: deleted ids are never reused.
            self._persist(keys, next_id)
            self.error = None
