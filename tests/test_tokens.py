import pytest

from shopagent.security.tokens import TokenError, issue_token, verify_token


def test_signed_token_round_trip_and_agent_identity():
    token = issue_token(
        "s" * 32,
        subject="orchestrator",
        role="service",
        agent="product_agent",
        now=100,
        ttl_seconds=60,
    )
    principal = verify_token("s" * 32, token, expected_role="service", now=120)
    assert principal.subject == "orchestrator"
    assert principal.agent == "product_agent"


def test_signed_token_rejects_tampering_expiry_and_wrong_role():
    token = issue_token("s" * 32, subject="alice", role="user", now=100, ttl_seconds=60)
    with pytest.raises(TokenError, match="signature"):
        verify_token("x" * 32, token, now=120)
    with pytest.raises(TokenError, match="expired"):
        verify_token("s" * 32, token, now=160)
    with pytest.raises(TokenError, match="role"):
        verify_token("s" * 32, token, expected_role="service", now=120)
