from unittest.mock import patch

from app.redis_client import generate_oauth_state, verify_oauth_state


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
