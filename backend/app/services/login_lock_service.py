import time


MAX_ATTEMPTS = 5
LOCK_DURATION = 900  # 15 minutes

_login_attempts: dict[str, dict] = {}


def check_lock(email: str) -> bool:
    entry = _login_attempts.get(email.lower())
    if entry is None:
        return False
    if entry.get("locked_until") and time.time() < entry["locked_until"]:
        return True
    # Lock expired, clean up
    _login_attempts.pop(email.lower(), None)
    return False


def record_failure(email: str) -> None:
    key = email.lower()
    entry = _login_attempts.setdefault(key, {"count": 0, "locked_until": None})
    entry["count"] += 1
    if entry["count"] >= MAX_ATTEMPTS:
        entry["locked_until"] = time.time() + LOCK_DURATION


def reset(email: str) -> None:
    _login_attempts.pop(email.lower(), None)
