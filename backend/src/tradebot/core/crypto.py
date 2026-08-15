import base64
import hashlib
import os
from dataclasses import dataclass

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_KEY_BYTES = 32
_NONCE_BYTES = 12


@dataclass(frozen=True)
class SealedSecret:
    wrapped_key: bytes
    nonce: bytes
    ciphertext: bytes
    key_id: str


class SecretBox:
    """Envelope encryption: a per-secret data key, itself wrapped by the master key.

    Rotating the master key only rewraps data keys, so ciphertext never has to be rewritten.
    """

    def __init__(self, master_key: str) -> None:
        if len(master_key) < 32:
            raise ValueError("master key must be at least 32 characters")
        self._master = hashlib.sha256(master_key.encode()).digest()
        self._key_id = hashlib.sha256(self._master).hexdigest()[:16]

    @property
    def key_id(self) -> str:
        return self._key_id

    def seal(self, plaintext: str, *, aad: bytes | None = None) -> SealedSecret:
        data_key = os.urandom(_KEY_BYTES)
        nonce = os.urandom(_NONCE_BYTES)
        ciphertext = AESGCM(data_key).encrypt(nonce, plaintext.encode(), aad)

        wrap_nonce = os.urandom(_NONCE_BYTES)
        wrapped = AESGCM(self._master).encrypt(wrap_nonce, data_key, None)

        return SealedSecret(
            wrapped_key=wrap_nonce + wrapped,
            nonce=nonce,
            ciphertext=ciphertext,
            key_id=self._key_id,
        )

    def open(self, sealed: SealedSecret, *, aad: bytes | None = None) -> str:
        wrap_nonce, wrapped = sealed.wrapped_key[:_NONCE_BYTES], sealed.wrapped_key[_NONCE_BYTES:]
        data_key = AESGCM(self._master).decrypt(wrap_nonce, wrapped, None)
        return AESGCM(data_key).decrypt(sealed.nonce, sealed.ciphertext, aad).decode()

    def rewrap(self, sealed: SealedSecret, previous: "SecretBox") -> SealedSecret:
        wrap_nonce = sealed.wrapped_key[:_NONCE_BYTES]
        wrapped = sealed.wrapped_key[_NONCE_BYTES:]
        data_key = AESGCM(previous._master).decrypt(wrap_nonce, wrapped, None)

        new_nonce = os.urandom(_NONCE_BYTES)
        rewrapped = AESGCM(self._master).encrypt(new_nonce, data_key, None)
        return SealedSecret(
            wrapped_key=new_nonce + rewrapped,
            nonce=sealed.nonce,
            ciphertext=sealed.ciphertext,
            key_id=self._key_id,
        )


def mask(secret: str, *, keep: int = 4) -> str:
    if len(secret) <= keep:
        return "…"
    return f"…{secret[-keep:]}"


def fingerprint(secret: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())[:12].decode()
