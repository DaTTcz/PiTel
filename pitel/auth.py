"""Heslo do webové správy (PBKDF2, uložené v admin.json s právy 600)."""
import hashlib
import hmac
import os

from . import paths
from .util import read_json, write_json

ITERATIONS = 120_000  # Raspberry Pi 2 to spočítá zhruba za sekundu


def _hash(password: str, salt: bytes, iterations: int) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations).hex()


def set_password(password: str) -> None:
    if len(password) < 6:
        raise ValueError("Heslo do správy musí mít aspoň 6 znaků.")
    salt = os.urandom(16)
    write_json(paths.ADMIN, {
        "hash": f"pbkdf2_sha256${ITERATIONS}${salt.hex()}${_hash(password, salt, ITERATIONS)}"
    }, 0o600)


def has_password() -> bool:
    return bool((read_json(paths.ADMIN, {}) or {}).get("hash"))


def check_password(password: str) -> bool:
    stored = (read_json(paths.ADMIN, {}) or {}).get("hash", "")
    try:
        algo, it, salt, digest = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        return hmac.compare_digest(_hash(password, bytes.fromhex(salt), int(it)), digest)
    except ValueError:
        return False
