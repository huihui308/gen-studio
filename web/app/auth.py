"""Authentication primitives: password hashing, the single-user store and
server-side sessions.

Deliberately dependency free (stdlib only) and constant-time on comparisons.
Passwords are stored as PBKDF2-HMAC-SHA256 with a per-password random salt;
plaintext is never written anywhere.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import DEFAULT_PASSWORD, DEFAULT_USERNAME

PBKDF2_ITERATIONS = 260_000
PBKDF2_SALT_BYTES = 16
PBKDF2_DIGEST_BYTES = 32
PASSWORD_ALGORITHM = "pbkdf2_sha256"
PASSWORD_MIN_LENGTH = 6
PASSWORD_MAX_LENGTH = 64


class AuthError(Exception):
    """Base class for authentication failures (message is user-facing)."""


class InvalidCredentials(AuthError):
    pass


class PasswordPolicyError(AuthError):
    pass


def _b64e(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _b64d(value: str) -> bytes:
    return base64.b64decode(value.encode("ascii"), validate=True)


def hash_password(password: str, *, iterations: int = PBKDF2_ITERATIONS, salt: bytes | None = None) -> str:
    """Return an encoded PBKDF2 hash: ``pbkdf2_sha256$iter$salt$digest``."""
    if not password:
        raise PasswordPolicyError("密码不能为空")
    salt = salt if salt is not None else secrets.token_bytes(PBKDF2_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations, dklen=PBKDF2_DIGEST_BYTES)
    return f"{PASSWORD_ALGORITHM}${iterations}${_b64e(salt)}${_b64e(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    """Constant-time verification of ``password`` against an encoded hash."""
    try:
        algorithm, iterations_raw, salt_raw, digest_raw = encoded.split("$", 3)
        if algorithm != PASSWORD_ALGORITHM:
            return False
        iterations = int(iterations_raw)
        salt = _b64d(salt_raw)
        expected = _b64d(digest_raw)
    except (ValueError, TypeError):
        return False
    if iterations <= 0 or not salt or not expected:
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations, dklen=len(expected))
    return hmac.compare_digest(candidate, expected)


def validate_new_password(new_password: str, current_password: str, confirmation: str) -> None:
    """Raise :class:`PasswordPolicyError` when the requested password is not acceptable."""
    if new_password != confirmation:
        raise PasswordPolicyError("两次输入的新密码不一致")
    if not new_password:
        raise PasswordPolicyError("新密码不能为空")
    if len(new_password) < PASSWORD_MIN_LENGTH:
        raise PasswordPolicyError(f"新密码至少需要 {PASSWORD_MIN_LENGTH} 个字符")
    if len(new_password) > PASSWORD_MAX_LENGTH:
        raise PasswordPolicyError(f"新密码不能超过 {PASSWORD_MAX_LENGTH} 个字符")
    if new_password == current_password:
        raise PasswordPolicyError("新密码不能与当前密码相同")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _atomic_write_json(path: Path, payload: dict[str, Any], mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(tmp, mode)
    os.replace(tmp, path)


class UserStore:
    """Single-user credential store persisted as a JSON file.

    The first call bootstraps ``admin/admin`` exactly as required by the
    specification; any later change is persisted atomically with mode 0600.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self._ensure_bootstrap()

    def _ensure_bootstrap(self) -> None:
        with self._lock:
            if self.path.exists():
                return
            payload = {
                "version": 1,
                "users": {
                    DEFAULT_USERNAME: {
                        "password_hash": hash_password(DEFAULT_PASSWORD),
                        "created_at": _utc_now_iso(),
                        "updated_at": _utc_now_iso(),
                    }
                },
            }
            _atomic_write_json(self.path, payload)

    def _load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            self._ensure_bootstrap()
            data = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("users"), dict):
            raise AuthError("用户数据文件损坏，请检查 web/data/auth.json")
        return data

    def get_user(self, username: str) -> dict[str, Any] | None:
        with self._lock:
            return self._load()["users"].get(username)

    def verify(self, username: str, password: str) -> bool:
        user = self.get_user(username)
        if user is None:
            # Spend comparable time to avoid a trivial username oracle.
            hash_password(password or "-", iterations=PBKDF2_ITERATIONS)
            return False
        return verify_password(password, str(user.get("password_hash", "")))

    def change_password(self, username: str, current_password: str, new_password: str) -> None:
        with self._lock:
            data = self._load()
            user = data["users"].get(username)
            if user is None:
                raise InvalidCredentials("当前账号不存在")
            if not verify_password(current_password, str(user.get("password_hash", ""))):
                raise InvalidCredentials("当前密码不正确")
            if new_password == current_password:
                raise PasswordPolicyError("新密码不能与当前密码相同")
            user["password_hash"] = hash_password(new_password)
            user["updated_at"] = _utc_now_iso()
            _atomic_write_json(self.path, data)

    def is_default_password(self, username: str) -> bool:
        """True while the account still uses the shipped default password."""
        user = self.get_user(username)
        if user is None:
            return False
        return verify_password(DEFAULT_PASSWORD, str(user.get("password_hash", "")))

    def updated_at(self, username: str) -> str | None:
        user = self.get_user(username)
        return str(user.get("updated_at")) if user and user.get("updated_at") else None


@dataclass
class Session:
    token: str
    username: str
    csrf_token: str
    created_at: float = field(default_factory=time.time)

    def is_expired(self, ttl_seconds: int, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        return (now - self.created_at) > ttl_seconds


class SessionStore:
    """Server-side session store, optionally persisted to a JSON file.

    The console restarts often (code changes, systemd restarts) and a purely
    in-memory store signs the operator out every time, which looks like "the
    refresh logged me out".  When a ``path`` is given the sessions survive a
    restart; the file holds bearer tokens, so it is written atomically with
    0600 permissions, mirroring the credential file.
    """

    def __init__(self, ttl_seconds: int, path: Path | None = None) -> None:
        self.ttl_seconds = ttl_seconds
        self.path = Path(path) if path is not None else None
        self._lock = threading.RLock()
        self._sessions: dict[str, Session] = {}
        self._load()

    # ------------------------------------------------------------ persistence
    def _load(self) -> None:
        if self.path is None or not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # A corrupt file must not block login: start empty and overwrite.
            return
        items = raw if isinstance(raw, list) else []
        now = time.time()
        sessions: dict[str, Session] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            token = item.get("token")
            username = item.get("username")
            csrf = item.get("csrf_token")
            created = item.get("created_at")
            if not (isinstance(token, str) and isinstance(username, str)
                    and isinstance(csrf, str)):
                continue
            try:
                created_at = float(created)
            except (TypeError, ValueError):
                continue
            session = Session(token=token, username=username,
                              csrf_token=csrf, created_at=created_at)
            if session.is_expired(self.ttl_seconds, now):
                continue
            sessions[token] = session
        self._sessions = sessions
        if len(sessions) != len(items):
            self._write()

    def _write(self) -> None:
        if self.path is None:
            return
        payload = [
            {
                "token": session.token,
                "username": session.username,
                "csrf_token": session.csrf_token,
                "created_at": session.created_at,
            }
            for session in self._sessions.values()
        ]
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name(self.path.name + ".tmp")
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle)
            os.replace(temporary, self.path)
            os.chmod(self.path, 0o600)
        except OSError:
            # Persistence is best effort; a failed write must not break auth.
            pass

    # ------------------------------------------------------------------ ops
    def create(self, username: str) -> Session:
        session = Session(
            token=secrets.token_urlsafe(32),
            username=username,
            csrf_token=secrets.token_urlsafe(32),
        )
        with self._lock:
            self._sessions[session.token] = session
            self._write()
        return session

    def get(self, token: str | None) -> Session | None:
        if not token:
            return None
        with self._lock:
            session = self._sessions.get(token)
            if session is None:
                return None
            if session.is_expired(self.ttl_seconds):
                self._sessions.pop(token, None)
                self._write()
                return None
            return session

    def delete(self, token: str | None) -> None:
        if not token:
            return
        with self._lock:
            if self._sessions.pop(token, None) is not None:
                self._write()

    def revoke_others(self, username: str, keep_token: str) -> int:
        """Invalidate every session of ``username`` except ``keep_token``."""
        removed = 0
        with self._lock:
            for token, session in list(self._sessions.items()):
                if session.username == username and token != keep_token:
                    self._sessions.pop(token, None)
                    removed += 1
            if removed:
                self._write()
        return removed
