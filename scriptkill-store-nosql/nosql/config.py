# -*- coding: utf-8 -*-
import os


def _env(name: str, default: str) -> str:
    value = os.getenv(name)
    return value if value not in (None, "") else default


MONGO_URI = _env("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB_NAME = _env("MONGO_DB_NAME", "script_kill_store")

REDIS_HOST = _env("REDIS_HOST", "localhost")
REDIS_PORT = int(_env("REDIS_PORT", "6379"))
REDIS_DB = int(_env("REDIS_DB", "0"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD") or None

# 锁位默认时长（分钟）
LOCK_MINUTES_DEFAULT = int(_env("LOCK_MINUTES_DEFAULT", "15"))

