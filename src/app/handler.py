"""Lambda entry point for the API Gateway HTTP API (payload format 2.0)."""

from __future__ import annotations

import hmac
import json
import logging
import os

from . import repository
from .validation import STATUSES, ValidationError, validate_new, validate_patch

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def _response(status: int, body: object | None = None) -> dict:
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json"},
        "body": "" if body is None else json.dumps(body),
    }


def _error(status: int, code: str, message: str, **extra) -> dict:
    return _response(status, {"error": {"code": code, "message": message, **extra}})


def _authorised(event: dict) -> bool:
    expected = os.environ.get("API_TOKEN", "")
    header = (event.get("headers") or {}).get("authorization", "")
    supplied = header[7:] if header.lower().startswith("bearer ") else ""
    return bool(expected) and hmac.compare_digest(supplied.encode(), expected.encode())


def _json_body(event: dict) -> object:
    raw = event.get("body") or ""
    if event.get("isBase64Encoded"):
        import base64

        raw = base64.b64decode(raw).decode()
    try:
        return json.loads(raw) if raw else None
    except json.JSONDecodeError as exc:
        raise ValidationError({"body": "must be valid JSON"}) from exc


def lambda_handler(event: dict, context: object) -> dict:
    route = event.get("routeKey", "")
    if not _authorised(event):
        return _error(401, "unauthorized", "Missing or invalid bearer token.")
    params = event.get("pathParameters") or {}
    query = event.get("queryStringParameters") or {}
    try:
        if route == "POST /applications":
            return _response(201, repository.create(validate_new(_json_body(event))))
        if route == "GET /applications":
            status = query.get("status")
            if status and status not in STATUSES:
                raise ValidationError({"status": f"must be one of {', '.join(STATUSES)}"})
            try:
                limit = min(max(int(query.get("limit", "20")), 1), 100)
            except ValueError as exc:
                raise ValidationError({"limit": "must be an integer"}) from exc
            try:
                return _response(200, repository.list_applications(status, limit, query.get("cursor")))
            except ValueError as exc:
                raise ValidationError({"cursor": "is invalid"}) from exc
        if route == "GET /applications/{id}":
            return _response(200, repository.get(params["id"]))
        if route == "PATCH /applications/{id}":
            changes, version = validate_patch(_json_body(event))
            return _response(200, repository.update(params["id"], changes, version))
        if route == "DELETE /applications/{id}":
            repository.delete(params["id"])
            return _response(204)
        if route == "POST /applications/{id}/attachments":
            body = _json_body(event) or {}
            filename = body.get("filename") if isinstance(body, dict) else None
            if not isinstance(filename, str) or not filename.lower().endswith(".pdf"):
                raise ValidationError({"filename": "must be a .pdf file name"})
            return _response(201, repository.attachment_upload_url(params["id"], filename))
        if route == "GET /stats":
            return _response(200, repository.stats())
        return _error(404, "not_found", f"No route for {route or 'this request'}.")
    except ValidationError as err:
        return _error(400, "validation_failed", "The request is invalid.", fields=err.errors)
    except repository.NotFound:
        return _error(404, "not_found", "Application not found.")
    except repository.VersionConflict as err:
        return _error(
            409, "version_conflict", "The application changed since you read it.", currentVersion=err.current_version
        )
    except Exception:  # noqa: BLE001 - last-resort guard so clients never see a stack trace
        logger.exception("Unhandled error for route %s", route)
        return _error(500, "internal_error", "Something went wrong.")
