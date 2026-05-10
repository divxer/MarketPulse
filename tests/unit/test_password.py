from marketpulse.auth.password import hash_password, verify_password


def test_hash_and_verify() -> None:
    h = hash_password("hunter2")
    assert verify_password("hunter2", h)
    assert not verify_password("wrong", h)


def test_hash_returns_different_each_time() -> None:
    assert hash_password("a") != hash_password("a")
