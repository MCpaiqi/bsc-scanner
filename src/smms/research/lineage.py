"""Exact, JSON-stable source manifests shared by derived research producers."""

import hashlib
import json
from datetime import datetime
from decimal import Decimal
from enum import Enum

from sqlalchemy import Numeric

from smms.evaluation import utc


def json_value(value):
    if isinstance(value, Decimal):
        result = format(value, "f")
        return result.rstrip("0").rstrip(".") if "." in result else result
    if isinstance(value, datetime):
        return utc(value).isoformat(timespec="microseconds")
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {key: json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [json_value(item) for item in value]
    return value


def digest(value):
    return hashlib.sha256(json.dumps(json_value(value), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def row_values(row):
    values = {}
    for column in row.__table__.columns:
        value = getattr(row, column.name)
        if value is not None and isinstance(column.type, Numeric):
            value = Decimal(str(value))
        values[column.name] = value
    return json_value(values)


def manifest(*, kind, parameters, facts, dependencies=None, notes=None):
    body = json_value({"schema_version": "derived-inputs-v1", "kind": kind,
                       "parameters": parameters, "facts": facts,
                       "dependencies": dependencies or [], "notes": notes or []})
    return {**body, "digest": digest(body)}
