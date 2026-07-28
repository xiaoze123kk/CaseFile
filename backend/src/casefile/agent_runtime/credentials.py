"""AES-256-GCM encryption for user-level provider credentials."""

from __future__ import annotations

import base64
import os
import secrets
from dataclasses import dataclass

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MASTER_KEY_ENV = "CASEFILE_MASTER_KEY"
MASTER_KEY_VERSION = 1


@dataclass(frozen=True, slots=True)
class EncryptedCredential:
    ciphertext: bytes
    nonce: bytes
    key_version: int
    last_four: str


def encrypt_api_key(api_key: str, *, user_id: int, provider: str) -> EncryptedCredential:
    """Encrypt one provider key with identity-bound associated data."""

    normalized = api_key.strip()
    if len(normalized) < 8:
        raise ValueError("API key is too short")
    nonce = secrets.token_bytes(12)
    ciphertext = AESGCM(_master_key()).encrypt(
        nonce,
        normalized.encode("utf-8"),
        _associated_data(user_id, provider, MASTER_KEY_VERSION),
    )
    return EncryptedCredential(ciphertext, nonce, MASTER_KEY_VERSION, normalized[-4:])


def decrypt_api_key(
    ciphertext: bytes,
    nonce: bytes,
    *,
    user_id: int,
    provider: str,
    key_version: int,
) -> str:
    """Decrypt one provider key without logging or persisting plaintext."""

    plaintext = AESGCM(_master_key()).decrypt(
        nonce,
        ciphertext,
        _associated_data(user_id, provider, key_version),
    )
    return plaintext.decode("utf-8")


def generate_master_key() -> str:
    """Return a URL-safe base64 encoded 256-bit application master key."""

    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii")


def _master_key() -> bytes:
    encoded = os.environ.get(MASTER_KEY_ENV, "").strip()
    if not encoded:
        raise RuntimeError(f"{MASTER_KEY_ENV} is required")
    try:
        key = base64.urlsafe_b64decode(encoded.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as error:
        raise RuntimeError(f"{MASTER_KEY_ENV} must be URL-safe base64") from error
    if len(key) != 32:
        raise RuntimeError(f"{MASTER_KEY_ENV} must decode to exactly 32 bytes")
    return key


def _associated_data(user_id: int, provider: str, key_version: int) -> bytes:
    return f"casefile:user:{user_id}:provider:{provider}:v{key_version}".encode()
