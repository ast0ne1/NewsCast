from app.services import passwords


def test_hash_and_verify_round_trip():
    hashed = passwords.hash_password("secret-pass")
    assert passwords.is_hashed(hashed)
    assert passwords.verify_password(hashed, "secret-pass")
    assert not passwords.verify_password(hashed, "wrong")


def test_plaintext_legacy_verify():
    assert passwords.verify_password("admin", "admin")
    assert not passwords.verify_password("admin", "nope")
    assert passwords.needs_rehash("admin")


def test_needs_rehash_false_for_fresh_hash():
    hashed = passwords.hash_password("ok")
    assert not passwords.needs_rehash(hashed)
