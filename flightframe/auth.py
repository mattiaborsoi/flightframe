"""Password hashing, sessions, CSRF, and login throttling — stdlib only.

The server is on the public internet, so the rules are strict and small:

  * scrypt for passwords (hashlib.scrypt; n=2^14, r=8, p=1 — interactive
    login cost, ~50ms). Format: scrypt$14$8$1$<salt_hex>$<hash_hex>.
  * Sessions are random 256-bit values in an HttpOnly, Secure, SameSite=Lax
    cookie; the registry stores only their sha256.
  * CSRF: every state-changing request must carry an Origin (or Referer)
    header matching the expected host. SameSite=Lax already blocks the
    classic cross-site POST; the Origin check covers older browsers and
    subdomain surprises. No token dance needed for a same-origin-only app.
  * Login throttling: a small in-process sliding window per IP. Cloudflare
    rate rules sit in front of this; this is the belt to their braces.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from collections import defaultdict, deque

SCRYPT_N_LOG2, SCRYPT_R, SCRYPT_P = 14, 8, 1

LOGIN_WINDOW_S = 300
LOGIN_MAX_ATTEMPTS = 10


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt,
                            n=1 << SCRYPT_N_LOG2, r=SCRYPT_R, p=SCRYPT_P)
    return (f"scrypt${SCRYPT_N_LOG2}${SCRYPT_R}${SCRYPT_P}"
            f"${salt.hex()}${digest.hex()}")


# A valid scrypt hash of an unguessable string, verified against on the
# login miss path so a nonexistent account costs the same time as a real one.
DUMMY_HASH = hash_password(secrets.token_hex(16))


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n_log2, r, p, salt_hex, hash_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        digest = hashlib.scrypt(password.encode(),
                                salt=bytes.fromhex(salt_hex),
                                n=1 << int(n_log2), r=int(r), p=int(p))
        return hmac.compare_digest(digest.hex(), hash_hex)
    except (ValueError, KeyError):
        return False


def origin_ok(headers, expected_hosts: set[str]) -> bool:
    """True when the request's Origin/Referer names a host we serve.

    Absent both headers, fail closed: every modern browser sends Origin on
    POST, and the device protocol never passes through here.
    """
    origin = headers.get("Origin") or headers.get("Referer") or ""
    if not origin:
        return False
    host = origin.split("://", 1)[-1].split("/", 1)[0].split(":", 1)[0]
    return host in expected_hosts


class LoginLimiter:
    """Sliding window per key (client IP). In-process, deliberately simple."""

    def __init__(self, max_attempts: int = LOGIN_MAX_ATTEMPTS,
                 window_s: int = LOGIN_WINDOW_S):
        self.max = max_attempts
        self.window = window_s
        self._hits: dict[str, deque] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = time.time()
        hits = self._hits[key]
        while hits and hits[0] < now - self.window:
            hits.popleft()
        if len(hits) >= self.max:
            return False
        hits.append(now)
        return True
