#!/usr/bin/env python3
"""Encryption at rest for BYOK provider keys — round-trips, and never leaks.

Proves the properties the store depends on: a value comes back exactly as it
went in, the ciphertext never contains the plaintext, the same secret encrypts
differently each time (random nonce), a tampered value is refused rather than
silently returning garbage, and the key file is owner-only. When the optional
crypto extra is absent, the one thing to prove is that storing a key is refused
with a clear hint, not written in the clear.

    python3 tools/secretbox_check.py

Exit status is 0 on pass, 1 on fail.
"""

from __future__ import annotations

import shutil
import stat
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from clique import secretbox


def main() -> int:
    res: dict[str, object] = {}
    home = Path(tempfile.mkdtemp(prefix="clique-secretbox-"))
    key_path = home / "secret.key"
    try:
        res["is_encrypted_discriminates"] = not secretbox.is_encrypted(
            "sk-plain-1234"
        ) and not secretbox.is_encrypted("")

        if not secretbox.available():
            # No crypto extra: the only correct behaviour is a clean refusal.
            try:
                secretbox.encrypt("sk-secret", key_path)
                res["refuses_without_extra"] = False
            except secretbox.SecretsUnavailable as e:
                res["refuses_without_extra"] = "pip install" in str(e)
            res["no_key_written"] = not key_path.exists()
            return _report(res)

        secrets = ["sk-abc123", "üñîçødé-key-❤", "", "x" * 4096]
        res["round_trips"] = all(
            secretbox.decrypt(secretbox.encrypt(s, key_path), key_path) == s for s in secrets
        )

        token = secretbox.encrypt("sk-super-secret-value", key_path)
        res["announces_scheme"] = token.startswith("gcm1:") and secretbox.is_encrypted(token)
        res["plaintext_absent"] = "sk-super-secret-value" not in token

        # Same secret, two calls — different ciphertext (fresh nonce each time),
        # so a store full of identical keys does not reveal that they match.
        a = secretbox.encrypt("same", key_path)
        b = secretbox.encrypt("same", key_path)
        res["nonce_randomised"] = a != b and secretbox.decrypt(a, key_path) == "same"

        # A flipped ciphertext byte must fail the tag, not decrypt to garbage.
        # Tamper on the decoded bytes so this tests the cipher's authentication,
        # not base64 validity.
        import base64

        from cryptography.exceptions import InvalidTag

        blob = bytearray(base64.b64decode(token[len("gcm1:") :]))
        blob[-1] ^= 0x01  # a bit inside the GCM tag
        tampered = "gcm1:" + base64.b64encode(bytes(blob)).decode("ascii")
        try:
            secretbox.decrypt(tampered, key_path)
            res["tamper_rejected"] = False
        except InvalidTag:
            res["tamper_rejected"] = True

        # A value that is not ours is rejected before any crypto is attempted.
        try:
            secretbox.decrypt("sk-not-encrypted", key_path)
            res["non_token_rejected"] = False
        except ValueError:
            res["non_token_rejected"] = True

        # The key file exists and is readable by nobody but its owner.
        mode = stat.S_IMODE(key_path.stat().st_mode)
        res["key_file_0600"] = mode == 0o600

        # A key that drifts looser is re-tightened on next use, not trusted.
        key_path.chmod(0o644)
        secretbox.encrypt("trigger", key_path)
        res["retightens_loose_key"] = stat.S_IMODE(key_path.stat().st_mode) == 0o600
    finally:
        shutil.rmtree(home, ignore_errors=True)

    return _report(res)


def _report(res: dict) -> int:
    ok = all(res.values())
    for key, value in res.items():
        print(f"  {'ok  ' if value else 'FAIL'} {key}: {value}")
    print("secretbox_check:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
