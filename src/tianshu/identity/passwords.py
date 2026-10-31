"""Existing scrypt-v1 password representation, without a database compatibility layer."""

import hashlib
import hmac
import secrets


def password_hash(password):
    if not isinstance(password, str) or not 10 <= len(password) <= 1024:
        raise ValueError("password_length")
    salt = secrets.token_bytes(16)
    key = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32)
    return "scrypt-v1$" + salt.hex() + "$" + key.hex()


def verify_password(password, encoded):
    try:
        version, salt, expected = encoded.split("$")
        if version != "scrypt-v1" or not isinstance(password, str) or len(password) > 1024:
            return False
        actual = hashlib.scrypt(
            password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1, dklen=32
        )
        return hmac.compare_digest(actual, bytes.fromhex(expected))
    except (AttributeError, ValueError, TypeError):
        return False
