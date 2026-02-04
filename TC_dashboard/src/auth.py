"""Simple authentication: user store with password hashes only, no plaintext passwords."""

import json
import logging
import os
from pathlib import Path

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

logger = logging.getLogger(__name__)

# Users file path: persisted under /app/logs in container (mounted volume)
USERS_FILE = Path(os.getenv("DASHBOARD_USERS_FILE", "/app/logs/users.json"))


class User(UserMixin):
    """Minimal user for Flask-Login: id is username."""

    def __init__(self, user_id: str):
        self.id = user_id


def _load_users() -> dict:
    """Load {username: password_hash} from disk. Returns empty dict if file missing/invalid."""
    if not USERS_FILE.exists():
        return {}
    try:
        with open(USERS_FILE, "r") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items() if v}
    except Exception as e:
        logger.warning(f"Could not load users file: {e}")
    return {}


def _save_users(users: dict) -> None:
    """Persist {username: password_hash} to disk. Only stores hashes."""
    USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(USERS_FILE, "w") as f:
        json.dump(users, f, indent=2)


def get_user(user_id: str) -> User | None:
    """Return a User if username exists, else None. Used by Flask-Login."""
    users = _load_users()
    if user_id in users:
        return User(user_id)
    return None


def verify_password(username: str, password: str) -> bool:
    """Verify username/password; passwords are compared against stored hashes only."""
    users = _load_users()
    stored_hash = users.get(username)
    if not stored_hash:
        return False
    return check_password_hash(stored_hash, password)


def add_user(username: str, password: str) -> bool:
    """Add or update a user; password is hashed before storage. Returns True on success."""
    username = (username or "").strip()
    if not username:
        return False
    users = _load_users()
    users[username] = generate_password_hash(password, method="scrypt")
    _save_users(users)
    return True


def user_count() -> int:
    """Number of registered users (for setup flow)."""
    return len(_load_users())


def _cli_adduser(username: str, password: str) -> None:
    """CLI: add or update a user (password hashed before storage)."""
    if not username or not password:
        raise SystemExit("Usage: python -m src.auth adduser <username> <password>")
    add_user(username, password)
    print(f"User {username!r} added/updated. Password stored as hash only.")


if __name__ == "__main__":
    import getpass
    import sys
    if len(sys.argv) >= 3 and sys.argv[1] == "adduser":
        username = sys.argv[2]
        password = (sys.argv[3]) if len(sys.argv) >= 4 else getpass.getpass("Password: ")
        _cli_adduser(username, password)
    else:
        print("Usage: python -m src.auth adduser <username> [password]")
        print("  If password is omitted, you will be prompted (recommended).")
        sys.exit(1)
