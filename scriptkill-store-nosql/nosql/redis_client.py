# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
from typing import Optional

import redis

from nosql.config import REDIS_DB, REDIS_HOST, REDIS_PASSWORD, REDIS_PORT

logger = logging.getLogger(__name__)

_redis: Optional[redis.Redis] = None


def get_redis() -> redis.Redis:
    global _redis
    if _redis is None:
        _redis = redis.Redis(
            host=REDIS_HOST,
            port=REDIS_PORT,
            db=REDIS_DB,
            password=REDIS_PASSWORD,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
    return _redis


def ping() -> bool:
    try:
        return bool(get_redis().ping())
    except Exception:
        return False

