from unittest.mock import AsyncMock, patch

import pytest

from app.redis_client import FallbackRedisClient, generate_oauth_state, verify_oauth_state


def test_generate_and_verify_oauth_state() -> None:
    telegram_id = 987654321
    code_verifier = "random_verifier_string_abcd_1234_efgh"

    # Generate token
    state_token = generate_oauth_state(telegram_id, code_verifier)
    assert isinstance(state_token, str)
    assert len(state_token) > 0

    # Verify token
    payload = verify_oauth_state(state_token)
    assert payload is not None
    assert payload["telegram_id"] == telegram_id
    assert payload["code_verifier"] == code_verifier


def test_verify_invalid_oauth_state() -> None:
    # Invalid token format
    payload = verify_oauth_state("invalid-token-string")
    assert payload is None


def test_oauth_state_rejects_tokens_encrypted_with_db_key() -> None:
    """Domain separation: a Fernet token minted with the raw ENCRYPTION_KEY
    (the key protecting credentials at rest in database.py) must NOT verify
    as an OAuth state token — the state key is derived via HKDF and is
    cryptographically independent of the DB key."""
    import json

    from app.database import fernet as db_fernet

    forged = db_fernet.encrypt(
        json.dumps({"telegram_id": 1, "code_verifier": "attacker"}).encode("utf-8")
    ).decode("utf-8")

    assert verify_oauth_state(forged) is None


def test_verify_expired_oauth_state() -> None:
    telegram_id = 112233
    code_verifier = "verifier"

    state_token = generate_oauth_state(telegram_id, code_verifier)

    # Mock cryptography's fernet decrypt to raise an exception (representing TTL expiration)
    with patch("cryptography.fernet.Fernet.decrypt") as mock_decrypt:
        from cryptography.fernet import InvalidToken

        mock_decrypt.side_effect = InvalidToken()

        payload = verify_oauth_state(state_token)
        assert payload is None


@pytest.mark.asyncio
async def test_fallback_client_setex_get_delete_success() -> None:
    """When the underlying Redis client works, calls pass straight through."""
    client = FallbackRedisClient("redis://localhost:6379/0")
    client.client = AsyncMock()
    client.client.get.return_value = "stored-value"

    await client.setex("key1", 600, "value1")
    client.client.setex.assert_called_once_with("key1", 600, "value1")

    result = await client.get("key1")
    assert result == "stored-value"
    # In-memory fallback cache should NOT be used when Redis succeeds
    assert "key1" not in client._local_cache

    await client.delete("key1")
    client.client.delete.assert_called_once_with("key1")


@pytest.mark.asyncio
async def test_fallback_client_setex_falls_back_to_memory_on_redis_error() -> None:
    """If Redis is down, setex/get/delete must fall back to the in-memory cache."""
    client = FallbackRedisClient("redis://localhost:6379/0")
    client.client = AsyncMock()
    client.client.setex.side_effect = ConnectionError("redis unavailable")
    client.client.get.side_effect = ConnectionError("redis unavailable")
    client.client.delete.side_effect = ConnectionError("redis unavailable")

    # setex falls back to writing into the local cache
    await client.setex("key2", 600, "value2")
    assert client._local_cache["key2"] == "value2"

    # get falls back to reading from the local cache
    result = await client.get("key2")
    assert result == "value2"

    # get falls back to None if key isn't in the local cache either
    missing = await client.get("nonexistent-key")
    assert missing is None

    # delete falls back to removing from the local cache without raising
    await client.delete("key2")
    assert "key2" not in client._local_cache

    # deleting a key that was never cached locally should not raise
    await client.delete("never-existed")
