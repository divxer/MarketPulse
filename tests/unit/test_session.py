from marketpulse.auth.session import SESSION_COOKIE, SessionManager


def test_roundtrip() -> None:
    mgr = SessionManager(secret="x" * 32)
    token = mgr.issue()
    assert mgr.verify(token) is True


def test_tampered_token_rejected() -> None:
    mgr = SessionManager(secret="x" * 32)
    token = mgr.issue()
    bad = token[:-2] + ("aa" if not token.endswith("aa") else "bb")
    assert mgr.verify(bad) is False


def test_expired_token_rejected() -> None:
    mgr = SessionManager(secret="x" * 32)
    token = mgr.issue()
    assert mgr.verify(token, max_age_seconds=0) is False


def test_cookie_constant_present() -> None:
    assert SESSION_COOKIE
