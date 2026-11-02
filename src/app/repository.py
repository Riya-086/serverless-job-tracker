"""DynamoDB and S3 access.

Table design (single table, on-demand):
  pk: id
  GSI byDate   (entity, appliedOn)  -> all applications, newest first
  GSI byStatus (status, appliedOn)  -> applications in one status, newest first
"""

from __future__ import annotations

import base64
import json
import os
import re
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Attr, Key
from botocore.config import Config
from botocore.exceptions import ClientError

ENTITY = "APPLICATION"


class NotFound(Exception):
    pass


class VersionConflict(Exception):
    def __init__(self, current_version: int):
        super().__init__("version conflict")
        self.current_version = current_version


def _table():
    return boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _plain(item: dict) -> dict:
    """DynamoDB returns numbers as Decimal; convert for JSON and drop internal keys."""
    out = {k: (int(v) if isinstance(v, Decimal) else v) for k, v in item.items() if k != "entity"}
    return out


def create(fields: dict) -> dict:
    now = _now()
    item = {"id": str(uuid.uuid4()), "entity": ENTITY, "version": 1, "createdAt": now, "updatedAt": now, **fields}
    _table().put_item(Item=item, ConditionExpression=Attr("id").not_exists())
    return _plain(item)


def get(app_id: str) -> dict:
    item = _table().get_item(Key={"id": app_id}).get("Item")
    if not item:
        raise NotFound(app_id)
    return _plain(item)


def _encode_cursor(key: dict | None) -> str | None:
    if not key:
        return None
    return base64.urlsafe_b64encode(json.dumps(key).encode()).decode()


def _decode_cursor(cursor: str) -> dict:
    try:
        key = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        if not isinstance(key, dict):
            raise ValueError
        return key
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValueError("invalid cursor") from exc


def list_applications(status: str | None = None, limit: int = 20, cursor: str | None = None) -> dict:
    if status:
        params = {"IndexName": "byStatus", "KeyConditionExpression": Key("status").eq(status)}
    else:
        params = {"IndexName": "byDate", "KeyConditionExpression": Key("entity").eq(ENTITY)}
    params.update(ScanIndexForward=False, Limit=limit)
    if cursor:
        params["ExclusiveStartKey"] = _decode_cursor(cursor)
    page = _table().query(**params)
    return {"items": [_plain(i) for i in page["Items"]], "nextCursor": _encode_cursor(page.get("LastEvaluatedKey"))}


def update(app_id: str, changes: dict, expected_version: int) -> dict:
    names = {f"#{k}": k for k in changes}
    values = {f":{k}": v for k, v in changes.items()}
    sets = ", ".join(f"#{k} = :{k}" for k in changes)
    try:
        result = _table().update_item(
            Key={"id": app_id},
            UpdateExpression=f"SET {sets}, #version = #version + :one, #updatedAt = :now",
            ConditionExpression="attribute_exists(id) AND #version = :expected",
            ExpressionAttributeNames={**names, "#version": "version", "#updatedAt": "updatedAt"},
            ExpressionAttributeValues={**values, ":one": 1, ":now": _now(), ":expected": expected_version},
            ReturnValues="ALL_NEW",
        )
    except ClientError as err:
        if err.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
        current = get(app_id)  # raises NotFound when the item does not exist
        raise VersionConflict(current["version"]) from err
    return _plain(result["Attributes"])


def delete(app_id: str) -> None:
    try:
        _table().delete_item(Key={"id": app_id}, ConditionExpression=Attr("id").exists())
    except ClientError as err:
        if err.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise NotFound(app_id) from err
        raise


def stats() -> dict:
    counts: dict[str, int] = {}
    params = {
        "IndexName": "byDate",
        "KeyConditionExpression": Key("entity").eq(ENTITY),
        "ProjectionExpression": "#s",
        "ExpressionAttributeNames": {"#s": "status"},
    }
    while True:
        page = _table().query(**params)
        for item in page["Items"]:
            counts[item["status"]] = counts.get(item["status"], 0) + 1
        if "LastEvaluatedKey" not in page:
            break
        params["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    return {"total": sum(counts.values()), "byStatus": counts}


_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def attachment_upload_url(app_id: str, filename: str) -> dict:
    get(app_id)  # 404 for unknown applications
    safe_name = _SAFE.sub("-", filename)[-80:] or "document.pdf"
    key = f"applications/{app_id}/{uuid.uuid4().hex[:8]}-{safe_name}"
    url = boto3.client("s3", config=Config(signature_version="s3v4")).generate_presigned_url(
        "put_object",
        Params={"Bucket": os.environ["BUCKET_NAME"], "Key": key, "ContentType": "application/pdf"},
        ExpiresIn=300,
    )
    _table().update_item(
        Key={"id": app_id},
        UpdateExpression="SET attachments = list_append(if_not_exists(attachments, :empty), :key)",
        ExpressionAttributeValues={":empty": [], ":key": [key]},
    )
    return {"uploadUrl": url, "key": key, "expiresInSeconds": 300, "contentType": "application/pdf"}
