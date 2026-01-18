# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List

try:
    from bson import ObjectId
except Exception:  # pragma: no cover
    ObjectId = None  # type: ignore


def _dt_to_str(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def to_jsonable(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, datetime):
        return _dt_to_str(value)
    if isinstance(value, date):
        return value.isoformat()
    if ObjectId is not None and isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, dict):
        return {k: to_jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [to_jsonable(v) for v in value]
    return value

