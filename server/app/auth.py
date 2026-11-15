"""Users, roles and sessions.

Passwords are stored as salted PBKDF2 hashes. A login creates a random session token; the
browser keeps it in an HttpOnly cookie, the database keeps only its hash. Everything is
local: no external identity provider, as the plant network has no internet access.
"""
import hashlib
import hmac
import secrets

COOKIE = "eqv_session"
SESSION_SECONDS = 12 * 3600
ITERATIONS = 200_000

ROLES = {"engineer": "инженер", "master": "мастер", "admin": "администратор"}

# What each role may do. Everyone signed in may view cards, telemetry and reports.
PERMISSIONS = {
    "inspect": {"engineer", "master", "admin"},         # record findings, save an inspection
    "register_work": {"master", "admin"},               # maintenance and replacement: they reset resource counters
    "manage_users": {"admin"},
    "edit_history": {"admin"},                          # correct or remove a saved record
}


def can(user: dict, permission: str) -> bool:
    return user["role"] in PERMISSIONS[permission]


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iterations, salt, digest = stored.split("$")
        candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(iterations))
    except ValueError:
        return False
    return hmac.compare_digest(candidate.hex(), digest)


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
