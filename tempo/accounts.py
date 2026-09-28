"""Users and bring-your-own-key.

Each user gets a Tempo API key (only its SHA-256 hash is stored). Users can add their own
provider keys; Tempo then calls providers with those keys, in separate free-quota buckets.
Provider keys are encrypted at rest with Fernet (AES-128-CBC + HMAC-SHA256), never logged,
and never sent back to a browser (only the last four characters are shown).

The encryption key comes from TEMPO_SECRET_KEY, or from ``secret.key`` in the data directory
(created on first use, readable only by its owner). Without a data directory an in-memory key
is used and stored keys do not survive a restart.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import secrets
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from tempo.store import Store

log = logging.getLogger(__name__)

LOCAL_USER = "local"  # the implicit user when no accounts exist and no API key is set
ADMIN_USER = "admin"  # the holder of TEMPO_API_KEY


def _fernet_from_secret(secret: str) -> Fernet:
    try:
        return Fernet(secret.encode())
    except ValueError:  # not a Fernet key: derive one from the passphrase
        digest = hashlib.sha256(secret.encode()).digest()
        return Fernet(base64.urlsafe_b64encode(digest))


def load_vault_key(secret: str | None, data_dir: Path | None) -> Fernet:
    if secret:
        return _fernet_from_secret(secret)
    if data_dir is None:
        log.warning(
            "TEMPO_DATA_DIR is 'memory' and TEMPO_SECRET_KEY is unset: provider keys stored "
            "now last only until this process ends"
        )
        return Fernet(Fernet.generate_key())
    path = data_dir / "secret.key"
    if not path.exists():
        data_dir.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(Fernet.generate_key())
    return Fernet(path.read_bytes().strip())


def hash_api_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode()).hexdigest()


@dataclass
class User:
    id: str
    name: str


class Accounts:
    def __init__(self, store: Store, vault: Fernet) -> None:
        self.store = store
        self.vault = vault

    # --- users ------------------------------------------------------------------------

    def has_users(self) -> bool:
        return bool(self.store.query("SELECT 1 FROM users LIMIT 1"))

    def create_user(self, name: str) -> tuple[User, str]:
        name = name.strip()
        if not name or len(name) > 64:
            raise ValueError("user name must be 1-64 characters")
        if name in (LOCAL_USER, ADMIN_USER):
            raise ValueError(f"{name!r} is reserved")
        if self.store.query("SELECT 1 FROM users WHERE name = ?", (name,)):
            raise ValueError(f"user {name!r} already exists")
        api_key = "tempo_" + secrets.token_urlsafe(32)
        user = User(id=uuid.uuid4().hex, name=name)
        self.store.execute(
            "INSERT INTO users (id, name, api_key_hash, created_at) VALUES (?,?,?,?)",
            (user.id, user.name, hash_api_key(api_key), time.time()),
        )
        return user, api_key

    def authenticate(self, api_key: str) -> User | None:
        if not api_key:
            return None
        rows = self.store.query(
            "SELECT id, name FROM users WHERE api_key_hash = ?", (hash_api_key(api_key),)
        )
        return User(**rows[0]) if rows else None

    def find(self, name: str) -> User | None:
        rows = self.store.query("SELECT id, name FROM users WHERE name = ?", (name,))
        return User(**rows[0]) if rows else None

    def list_users(self) -> list[dict[str, Any]]:
        return self.store.query(
            "SELECT u.name, u.created_at, COUNT(k.provider) AS keys FROM users u "
            "LEFT JOIN user_keys k ON k.user_id = u.id GROUP BY u.id ORDER BY u.name"
        )

    def delete_user(self, name: str) -> bool:
        user = self.find(name)
        if user is None:
            return False
        self.store.execute("DELETE FROM user_keys WHERE user_id = ?", (user.id,))
        self.store.execute("DELETE FROM users WHERE id = ?", (user.id,))
        return True

    # --- provider keys ------------------------------------------------------------------

    def set_key(
        self, user_id: str, provider: str, api_key: str, verified: bool | None = None
    ) -> None:
        api_key = api_key.strip()
        if not api_key:
            raise ValueError("empty API key")
        ciphertext = self.vault.encrypt(api_key.encode())
        self.store.execute(
            "INSERT INTO user_keys (user_id, provider, ciphertext, last4, verified, created_at) "
            "VALUES (?,?,?,?,?,?) ON CONFLICT(user_id, provider) DO UPDATE SET "
            "ciphertext = excluded.ciphertext, last4 = excluded.last4, "
            "verified = excluded.verified, created_at = excluded.created_at",
            (user_id, provider, ciphertext, api_key[-4:], verified, time.time()),
        )

    def delete_key(self, user_id: str, provider: str) -> bool:
        cursor = self.store.execute(
            "DELETE FROM user_keys WHERE user_id = ? AND provider = ?", (user_id, provider)
        )
        return cursor.rowcount > 0

    def keys(self, user_id: str) -> dict[str, str]:
        """Decrypted provider keys for building a request's Access (never returned to clients)."""
        out: dict[str, str] = {}
        for row in self.store.query(
            "SELECT provider, ciphertext FROM user_keys WHERE user_id = ?", (user_id,)
        ):
            try:
                out[row["provider"]] = self.vault.decrypt(row["ciphertext"]).decode()
            except InvalidToken:
                log.warning(
                    "Stored %s key for a user cannot be decrypted (secret changed?)",
                    row["provider"],
                )
        return out

    def key_info(self, user_id: str) -> list[dict[str, Any]]:
        return self.store.query(
            "SELECT provider, last4, verified, created_at FROM user_keys WHERE user_id = ? "
            "ORDER BY provider",
            (user_id,),
        )
