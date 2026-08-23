"""Encryption at rest for bring-your-own-key provider secrets.

The base panel carries no crypto dependency — stdlib-only is the whole security
posture, and Python's stdlib has no cipher. Real authenticated encryption
(AES-256-GCM) comes from the optional ``cryptography`` extra, pulled in only by
operators who actually store a key:

    pip install 'clique-panel[llm]'

Without it, storing a provider key is refused with that one-line hint rather
than written in the clear — a key we cannot encrypt is a key we do not keep.

The data key is 32 random bytes in a ``0600`` file (``secret.key``) that lives
in ``$CLIQUE_HOME``, outside the settings store and never under the served web
root. So lifting ``state.json`` — from a backup, a sync, a misconfigured mount
— yields ciphertext and nothing else; you also need the key file, which the
panel guards at owner-only permissions and re-tightens if it ever drifts.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path

#: A stored value announces its own scheme, so a future format can coexist with
#: values already on disk instead of guessing at them.
_PREFIX = "gcm1:"
_KEY_BYTES = 32  # AES-256
_NONCE_BYTES = 12  # GCM standard


class SecretsUnavailable(RuntimeError):
    """The ``cryptography`` extra is absent, so a key cannot be stored."""

    HINT = "pip install 'clique-panel[llm]'"

    def __init__(self) -> None:
        super().__init__(f"encrypting a provider key needs the crypto extra — {self.HINT}")


def available() -> bool:
    """Is the optional crypto extra installed? The API checks this before it
    offers to store a key, so the refusal is a clear message, not a stack
    trace."""
    try:
        import cryptography.hazmat.primitives.ciphers.aead  # noqa: F401
    except ModuleNotFoundError:
        return False
    return True


def is_encrypted(value: object) -> bool:
    return isinstance(value, str) and value.startswith(_PREFIX)


def _aesgcm(key: bytes):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    return AESGCM(key)


def _load_or_create_key(key_path: Path) -> bytes:
    """The 32-byte data key, made once and reused. Created atomically at
    ``0600`` so there is never a window where it exists world-readable; an
    existing key that has drifted looser than owner-only is tightened."""
    try:
        raw = key_path.read_bytes()
        if len(raw) == _KEY_BYTES:
            if key_path.stat().st_mode & 0o077:
                key_path.chmod(0o600)
            return raw
    except OSError:
        pass

    key_path.parent.mkdir(parents=True, exist_ok=True)
    key = os.urandom(_KEY_BYTES)
    try:
        fd = os.open(key_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        # Lost the create race (or a wrong-length file already sits there);
        # read what actually landed rather than clobber another writer's key.
        return key_path.read_bytes()
    try:
        os.write(fd, key)
    finally:
        os.close(fd)
    return key


def encrypt(plaintext: str, key_path: Path) -> str:
    """Return an authenticated ciphertext token for ``plaintext``.

    Each call uses a fresh random nonce, so the same key encrypts to a
    different value every time and a stored value leaks nothing by repetition.
    """
    if not available():
        raise SecretsUnavailable()
    key = _load_or_create_key(Path(key_path))
    nonce = os.urandom(_NONCE_BYTES)
    ct = _aesgcm(key).encrypt(nonce, plaintext.encode("utf-8"), None)
    return _PREFIX + base64.b64encode(nonce + ct).decode("ascii")


def decrypt(token: str, key_path: Path) -> str:
    """Recover the plaintext, verifying the tag. A tampered value raises rather
    than returning garbage — the whole point of an authenticated cipher."""
    if not is_encrypted(token):
        raise ValueError("not an encrypted value")
    if not available():
        raise SecretsUnavailable()
    key = _load_or_create_key(Path(key_path))
    blob = base64.b64decode(token[len(_PREFIX) :])
    nonce, ct = blob[:_NONCE_BYTES], blob[_NONCE_BYTES:]
    return _aesgcm(key).decrypt(nonce, ct, None).decode("utf-8")
