from itsdangerous import BadSignature, SignatureExpired, TimestampSigner

SESSION_COOKIE = "mp_session"
DEFAULT_MAX_AGE = 60 * 60 * 24 * 30  # 30 days


class SessionManager:
    def __init__(self, secret: str) -> None:
        self._signer = TimestampSigner(secret)

    def issue(self) -> str:
        return self._signer.sign("auth").decode("utf-8")

    def verify(self, token: str, max_age_seconds: int = DEFAULT_MAX_AGE) -> bool:
        if max_age_seconds <= 0:
            return False
        try:
            self._signer.unsign(token, max_age=max_age_seconds)
            return True
        except (BadSignature, SignatureExpired):
            return False
