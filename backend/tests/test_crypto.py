import pytest
from cryptography.exceptions import InvalidTag

from tradebot.core.crypto import SecretBox, fingerprint, mask

MASTER = "master-key-that-is-long-enough-for-tests"
OTHER = "another-master-key-long-enough-for-tests"


def test_seal_open_round_trip() -> None:
    box = SecretBox(MASTER)
    sealed = box.seal("sk-live-abcdef123456")
    assert box.open(sealed) == "sk-live-abcdef123456"


def test_ciphertext_is_not_the_plaintext() -> None:
    box = SecretBox(MASTER)
    sealed = box.seal("sk-live-abcdef123456")
    assert b"abcdef123456" not in sealed.ciphertext


def test_same_plaintext_seals_differently() -> None:
    box = SecretBox(MASTER)
    first = box.seal("identical")
    second = box.seal("identical")
    assert first.ciphertext != second.ciphertext
    assert first.wrapped_key != second.wrapped_key


def test_wrong_master_key_cannot_open() -> None:
    sealed = SecretBox(MASTER).seal("secret")
    with pytest.raises(InvalidTag):
        SecretBox(OTHER).open(sealed)


def test_aad_binds_ciphertext_to_its_owner() -> None:
    box = SecretBox(MASTER)
    sealed = box.seal("secret", aad=b"1:alpaca:api_key")
    with pytest.raises(InvalidTag):
        box.open(sealed, aad=b"2:alpaca:api_key")


def test_rewrap_preserves_plaintext_without_touching_ciphertext() -> None:
    old, new = SecretBox(MASTER), SecretBox(OTHER)
    sealed = old.seal("rotate-me")
    rotated = new.rewrap(sealed, old)

    assert rotated.ciphertext == sealed.ciphertext
    assert rotated.key_id == new.key_id != old.key_id
    assert new.open(rotated) == "rotate-me"


def test_short_master_key_is_rejected() -> None:
    with pytest.raises(ValueError, match="at least 32"):
        SecretBox("too-short")


def test_mask_keeps_only_a_tail() -> None:
    assert mask("sk-live-abcdef1234") == "…1234"
    assert mask("ab") == "…"


def test_fingerprint_is_stable_and_distinct() -> None:
    assert fingerprint("a") == fingerprint("a")
    assert fingerprint("a") != fingerprint("b")
