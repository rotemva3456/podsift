"""One sealing primitive for every secret a feature keeps in the data folder instead of a login
session: the AI provider key (``providers/store.py``) and the private-feed PodFetch API key
(``feeds.py``), and any later one.

A value is sealed with an HMAC-SHA256 stream cipher (``_stream`` as the keystream) plus an
HMAC authentication tag (``_tag``), under a secret that lives only in a small 0600 file beside the
database (``secret()``). ``label`` is the domain-separation string baked into the derived stream
and tag keys, and it is also the secret file's own name (``<label>.secret``) -- so two callers
never share key material even though neither knows about the other, and losing one label's secret
file can never affect another label's sealed values.

There is no key rotation and no re-keying: replacing a label's secret file makes every value ever
sealed under it unreadable (``unseal`` returns ``None``), so guard it the same way you would the
values it protects.

The exact bytes matter: ``label="ai-settings"`` must keep deriving what ``providers/store.py``
always derived (``b"ai-settings stream"`` / ``b"ai-settings tag"``, file ``ai-settings.secret``),
so AI keys already saved before this module existed keep opening. See
``companion/test_sealed.py`` for the fixed, pre-refactor sealed value that proves it.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
from pathlib import Path


def _stream(secret_bytes: bytes, label: str, nonce: bytes, size: int) -> bytes:
    key = hmac.new(secret_bytes, f"{label} stream".encode(), hashlib.sha256).digest()
    out = bytearray()
    for counter in range((size + 31) // 32):
        out += hmac.new(key, nonce + counter.to_bytes(4, "big"), hashlib.sha256).digest()
    return bytes(out[:size])


def _tag(secret_bytes: bytes, label: str, data: bytes) -> bytes:
    return hmac.new(hmac.new(secret_bytes, f"{label} tag".encode(), hashlib.sha256).digest(),
                    data, hashlib.sha256).digest()


def seal(secret_bytes: bytes, label: str, value: str, value_origin: str) -> str:
    """A sealed, base64 blob of ``value`` bound to ``value_origin`` (unseal returns both, so a
    caller can refuse a value sealed for a different origin -- a copied data folder pointed at a
    different server can't reuse an old value)."""
    plain = json.dumps({"key": value, "origin": value_origin}).encode()
    nonce = secrets.token_bytes(16)
    body = nonce + bytes(a ^ b for a, b in zip(plain, _stream(secret_bytes, label, nonce, len(plain))))
    return "v1." + base64.urlsafe_b64encode(body + _tag(secret_bytes, label, body)).decode("ascii")


def unseal(secret_bytes: bytes, label: str, sealed: str) -> tuple[str, str] | None:
    """(value, origin), or None when ``sealed`` or ``secret_bytes`` don't match ``label``."""
    try:
        raw = base64.urlsafe_b64decode(sealed.removeprefix("v1.").encode("ascii"))
        body, tag = raw[:-32], raw[-32:]
        if len(body) < 17 or not hmac.compare_digest(tag, _tag(secret_bytes, label, body)):
            return None
        nonce, cipher = body[:16], body[16:]
        data = json.loads(bytes(a ^ b for a, b in zip(cipher, _stream(secret_bytes, label, nonce, len(cipher)))))
        return str(data["key"]), str(data["origin"])
    except (ValueError, KeyError, TypeError, UnicodeError):
        return None


def secret_file_name(label: str) -> str:
    return f"{label}.secret"


def secret(folder: Path, label: str, *, create: bool) -> bytes | None:
    """``label``'s own secret: a private 0600 file beside the database, created once. Two labels
    never share a file, so replacing one's secret can never affect another's sealed values."""
    path = folder / secret_file_name(label)
    if not path.exists() and create:
        value = base64.b64encode(secrets.token_bytes(32))
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            handle = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(handle, "wb") as out:
                out.write(value + b"\n")
        except FileExistsError:
            pass   # another copy of the app made it a moment ago
    try:
        value = base64.b64decode(path.read_bytes().strip(), validate=True)
    except (OSError, ValueError):
        return None
    return value if len(value) >= 32 else None
