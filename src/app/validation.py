"""Request validation. Kept dependency-free so the Lambda package stays tiny."""

from __future__ import annotations

import re
from datetime import date

STATUSES = ("applied", "interviewing", "offer", "rejected", "withdrawn")
_URL = re.compile(r"^https?://\S{1,490}$")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class ValidationError(Exception):
    def __init__(self, errors: dict[str, str]):
        super().__init__("invalid request")
        self.errors = errors


def _text(body: dict, field: str, errors: dict, *, required: bool, max_len: int) -> str | None:
    value = body.get(field)
    if value is None:
        if required:
            errors[field] = "is required"
        return None
    if not isinstance(value, str) or not value.strip():
        errors[field] = "must be a non-empty string"
        return None
    if len(value) > max_len:
        errors[field] = f"must be at most {max_len} characters"
        return None
    return value.strip()


def _status(body: dict, errors: dict, *, default: str | None) -> str | None:
    value = body.get("status", default)
    if value is not None and value not in STATUSES:
        errors["status"] = f"must be one of {', '.join(STATUSES)}"
    return value


def _url(body: dict, errors: dict) -> str | None:
    value = body.get("url")
    if value is not None and (not isinstance(value, str) or not _URL.match(value)):
        errors["url"] = "must be an http(s) URL"
    return value


def _applied_on(body: dict, errors: dict, today: date) -> str | None:
    value = body.get("appliedOn")
    if value is None:
        errors["appliedOn"] = "is required"
        return None
    try:
        if not isinstance(value, str) or not _ISO_DATE.match(value):
            raise ValueError
        parsed = date.fromisoformat(value)
    except ValueError:
        errors["appliedOn"] = "must be a date in YYYY-MM-DD format"
        return None
    if parsed > today:
        errors["appliedOn"] = "cannot be in the future"
    return value


def _reject_unknown(body: dict, allowed: set[str], errors: dict) -> None:
    for field in body:
        if field not in allowed:
            errors[field] = "is not a recognised field"


def validate_new(body: object, today: date | None = None) -> dict:
    if not isinstance(body, dict):
        raise ValidationError({"body": "must be a JSON object"})
    errors: dict[str, str] = {}
    _reject_unknown(body, {"company", "role", "status", "appliedOn", "url", "notes"}, errors)
    clean = {
        "company": _text(body, "company", errors, required=True, max_len=100),
        "role": _text(body, "role", errors, required=True, max_len=100),
        "status": _status(body, errors, default="applied"),
        "appliedOn": _applied_on(body, errors, today or date.today()),
        "url": _url(body, errors),
        "notes": _text(body, "notes", errors, required=False, max_len=2000),
    }
    if errors:
        raise ValidationError(errors)
    return {k: v for k, v in clean.items() if v is not None}


def validate_patch(body: object) -> tuple[dict, int]:
    """Returns (changes, expected_version). Clients must send the version they last read."""
    if not isinstance(body, dict):
        raise ValidationError({"body": "must be a JSON object"})
    errors: dict[str, str] = {}
    _reject_unknown(body, {"status", "notes", "url", "role", "version"}, errors)
    version = body.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        errors["version"] = "is required and must be a positive integer"
    changes = {
        "status": _status(body, errors, default=None),
        "notes": _text(body, "notes", errors, required=False, max_len=2000),
        "url": _url(body, errors),
        "role": _text(body, "role", errors, required=False, max_len=100),
    }
    changes = {k: v for k, v in changes.items() if v is not None}
    if not changes and "version" not in errors:
        errors["body"] = "include at least one of status, notes, url, role"
    if errors:
        raise ValidationError(errors)
    return changes, version
