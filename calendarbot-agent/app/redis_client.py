"""Global async Redis client and stateless OAuth CSRF state verification."""

import base64
import json
import logging

import redis.asyncio as redis
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.config import settings

logger = logging.getLogger(__name__)

# Domain separation: OAuth state tokens travel through browser URLs, while the
# raw ENCRYPTION_KEY also protects credentials at rest (database.py). Derive a
# dedicated state-token key via HKDF so compromising one domain never exposes
# the other, and so a Fernet token minted for one purpose is not valid
# ciphertext for the other.
_state_key = base64.urlsafe_b64encode(
    HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=b"calendarbot-oauth-state",
    ).derive(settings.encryption_key.encode("utf-8"))
)
fernet = Fernet(_state_key)


# ── Stateless OAuth State Helpers ─────────────────────────────────────────────


def generate_oauth_state(telegram_id: int, code_verifier: str) -> str:
    """Generate a signed, encrypted, URL-safe state token containing telegram_id and code_verifier."""
    payload = {
        "telegram_id": telegram_id,
        "code_verifier": code_verifier,
    }
    return fernet.encrypt(json.dumps(payload).encode("utf-8")).decode("utf-8")


def verify_oauth_state(state: str) -> dict | None:
    """Decrypt and verify the state token, returning the payload dict if valid."""
    from app.tools import OAUTH_STATE_TTL

    try:
        decrypted = fernet.decrypt(state.encode("utf-8"), ttl=OAUTH_STATE_TTL).decode("utf-8")
        return json.loads(decrypted)
    except Exception as exc:
        logger.warning("OAuth state verification failed: %s", exc)
        return None


# ── Redis Client with Fallback ────────────────────────────────────────────────


class FallbackRedisClient:
    """Redis client wrapper that falls back to in-memory storage if Redis is unavailable."""

    def __init__(self, redis_url: str):
        self.client = redis.from_url(redis_url, decode_responses=True)
        self._local_cache: dict[str, str] = {}

    async def setex(self, key: str, ttl: int, value: str) -> None:
        try:
            await self.client.setex(key, ttl, value)
        except Exception as exc:
            logger.warning("Redis setex failed, using in-memory fallback: %s", exc)
            self._local_cache[key] = value

    async def get(self, key: str) -> str | None:
        try:
            return await self.client.get(key)
        except Exception as exc:
            logger.warning("Redis get failed, using in-memory fallback: %s", exc)
            return self._local_cache.get(key)

    async def delete(self, key: str) -> None:
        try:
            await self.client.delete(key)
        except Exception as exc:
            logger.warning("Redis delete failed, using in-memory fallback: %s", exc)
            self._local_cache.pop(key, None)


redis_client = FallbackRedisClient(settings.redis_url)
