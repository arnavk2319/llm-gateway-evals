"""Who may be an admin: an allowlist of keyed hashes, not of email addresses.

The gateway holds a secret key (GATEWAY_ADMIN_HASH_KEY) and a list of HMAC-SHA256 digests of the admin
addresses (GATEWAY_ADMIN_EMAIL_HASHES). Reading the configuration therefore does not reveal who the admin
is, and, because the digest is keyed, it cannot be checked against guessed addresses without the key.
(Encryption would be the wrong tool: nothing here ever needs the address back, only a yes or no.)

An account is an admin only while BOTH hold: its stored role is `admin`, and its address is on the list.
Take a digest off the list, or change the key, and that account is an ordinary user from the next request
on. Missing configuration means nobody is an admin (fail closed).
"""

from __future__ import annotations

import hashlib
import hmac
import re

MIN_KEY_LENGTH = 32
_DIGEST = re.compile(r"^[a-f0-9]{64}$")


def normalize(email: str) -> str:
    return email.strip().lower()


def admin_hash(email: str, key: str) -> str:
    return hmac.new(key.encode(), normalize(email).encode(), hashlib.sha256).hexdigest()


class AdminAllowlist:
    def __init__(self, key: str | None, hashes: str | None) -> None:
        self._key = (key or "").strip()
        self._hashes = tuple(h for h in (x.strip().lower() for x in (hashes or "").split(",")) if _DIGEST.match(h))

    @property
    def enabled(self) -> bool:
        return len(self._key) >= MIN_KEY_LENGTH and bool(self._hashes)

    def hash(self, email: str) -> str:
        if len(self._key) < MIN_KEY_LENGTH:
            raise ValueError(f"GATEWAY_ADMIN_HASH_KEY must be at least {MIN_KEY_LENGTH} characters")
        return admin_hash(email, self._key)

    def allows(self, email: str) -> bool:
        if not self.enabled:
            return False
        digest = self.hash(email)
        return any(hmac.compare_digest(digest, h) for h in self._hashes)  # every entry is compared, always

    def effective_role(self, stored: str, email: str) -> str:
        """The role the rest of the system sees: a stored admin who is no longer on the list is a user."""
        return "admin" if stored == "admin" and self.allows(email) else "user"
