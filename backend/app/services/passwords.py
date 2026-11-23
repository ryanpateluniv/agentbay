"""Password hashing with stdlib scrypt (no extra dependency). Format: scrypt$n$r$p$salt_hex$hash_hex."""
import hashlib
import hmac
import secrets

N, R, P, DKLEN = 2**14, 8, 1, 32


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=N, r=R, p=P, dklen=DKLEN)
    return f"scrypt${N}${R}${P}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt, want = stored.split("$")
        if algo != "scrypt":
            return False
        dk = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p),
                            dklen=len(want) // 2)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(dk.hex(), want)


# Spend the same time on unknown emails as on a wrong password, so login timing doesn't reveal accounts.
DUMMY_HASH = hash_password(secrets.token_hex(8))
