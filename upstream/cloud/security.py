from __future__ import annotations

import hashlib
import hmac
import secrets

ITERATIONS = 600_000


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    result = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), ITERATIONS)
    return f"{ITERATIONS}${salt}${result.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    iterations, salt, expected = encoded.split("$")
    actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(iterations)).hex()
    return hmac.compare_digest(actual, expected)
