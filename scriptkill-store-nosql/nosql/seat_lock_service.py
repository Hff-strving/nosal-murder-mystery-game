# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional, Tuple

from nosql.config import LOCK_MINUTES_DEFAULT
from nosql.mongo import col
from nosql.redis_client import get_redis

logger = logging.getLogger(__name__)

_LOCK_ID_KEY = "lock:id"
_LOCK_EXP_ZSET = "locks:exp"


def _lock_key(schedule_id: int, player_id: int) -> str:
    return f"lock:{schedule_id}:{player_id}"


def _seats_key(schedule_id: int) -> str:
    return f"seats:{schedule_id}"


def get_active_lock_id(player_id: int, schedule_id: int) -> Optional[int]:
    r = get_redis()
    value = r.get(_lock_key(int(schedule_id), int(player_id)))
    return int(value) if value is not None else None


_LUA_LOCK = r"""
local lockKey = KEYS[1]
local seatsKey = KEYS[2]
local expZset = KEYS[3]
local lockIdKey = KEYS[4]

local ttlMs = tonumber(ARGV[1])
local expAtMs = tonumber(ARGV[2])

if redis.call('EXISTS', lockKey) == 1 then
  return -1
end

local seats = tonumber(redis.call('GET', seatsKey) or '-1')
if seats <= 0 then
  return -2
end

local newId = redis.call('INCR', lockIdKey)
redis.call('DECR', seatsKey)
redis.call('SET', lockKey, newId, 'PX', ttlMs)
redis.call('ZADD', expZset, expAtMs, lockKey)
return newId
"""

_LUA_CANCEL_LOCK = r"""
local lockKey = KEYS[1]
local seatsKey = KEYS[2]
local expZset = KEYS[3]

if redis.call('DEL', lockKey) == 1 then
  redis.call('INCR', seatsKey)
  redis.call('ZREM', expZset, lockKey)
  return 1
end
return 0
"""

_LUA_CONVERT_LOCK = r"""
local lockKey = KEYS[1]
local expZset = KEYS[2]

if redis.call('DEL', lockKey) == 1 then
  redis.call('ZREM', expZset, lockKey)
  return 1
end
return 0
"""

_LUA_TAKE_SEAT = r"""
local seatsKey = KEYS[1]
local seats = tonumber(redis.call('GET', seatsKey) or '-1')
if seats <= 0 then
  return 0
end
redis.call('DECR', seatsKey)
return 1
"""


def ensure_seats_initialized(schedule_id: int) -> None:
    r = get_redis()
    seats_key = _seats_key(schedule_id)
    if r.exists(seats_key):
        return

    sch = col("schedules").find_one({"_id": schedule_id}, {"Max_Players": 1})
    if not sch:
        raise ValueError("场次不存在")
    max_players = int(sch.get("Max_Players") or 0)

    now = datetime.now()
    booked = col("orders").count_documents(
        {"Schedule_ID": schedule_id, "Pay_Status": {"$in": [0, 1]}}
    )
    locked = col("lock_records").count_documents(
        {"Schedule_ID": schedule_id, "Status": 0, "ExpireTime": {"$gt": now}}
    )
    seats = max_players - int(booked) - int(locked)
    if seats < 0:
        seats = 0
    r.set(seats_key, seats)


def create_lock(player_id: int, schedule_id: int, lock_minutes: Optional[int] = None) -> Tuple[int, datetime]:
    ensure_seats_initialized(schedule_id)

    minutes = int(lock_minutes or LOCK_MINUTES_DEFAULT)
    expire_time = datetime.now() + timedelta(minutes=minutes)
    ttl_ms = int(minutes * 60 * 1000)
    exp_at_ms = int(expire_time.timestamp() * 1000)

    r = get_redis()
    lock_key = _lock_key(schedule_id, player_id)
    seats_key = _seats_key(schedule_id)

    new_id = r.eval(_LUA_LOCK, 4, lock_key, seats_key, _LOCK_EXP_ZSET, _LOCK_ID_KEY, ttl_ms, exp_at_ms)
    if int(new_id) == -1:
        raise ValueError("您已经锁定了该场次")
    if int(new_id) == -2:
        raise ValueError("该场次已满")

    return int(new_id), expire_time


def cancel_lock(player_id: int, schedule_id: int) -> bool:
    r = get_redis()
    lock_key = _lock_key(schedule_id, player_id)
    seats_key = _seats_key(schedule_id)
    ok = r.eval(_LUA_CANCEL_LOCK, 3, lock_key, seats_key, _LOCK_EXP_ZSET)
    return bool(int(ok) == 1)


def convert_lock_to_order(player_id: int, schedule_id: int) -> bool:
    r = get_redis()
    lock_key = _lock_key(schedule_id, player_id)
    ok = r.eval(_LUA_CONVERT_LOCK, 2, lock_key, _LOCK_EXP_ZSET)
    return bool(int(ok) == 1)


def take_seat(schedule_id: int) -> bool:
    ensure_seats_initialized(schedule_id)
    r = get_redis()
    ok = r.eval(_LUA_TAKE_SEAT, 1, _seats_key(schedule_id))
    return bool(int(ok) == 1)


def release_seat(schedule_id: int) -> None:
    ensure_seats_initialized(schedule_id)
    get_redis().incr(_seats_key(schedule_id))


def cleanup_expired_locks(limit: int = 200) -> int:
    """
    处理 Redis 中过期锁位对应的“座位归还”，并同步 Mongo 历史状态。
    说明：Redis key TTL 到期后会自动删除 lockKey，但 seats 不会自动 +1，因此需要清理任务。
    """
    r = get_redis()
    now_ms = int(datetime.now().timestamp() * 1000)
    members = r.zrangebyscore(_LOCK_EXP_ZSET, 0, now_ms, start=0, num=limit)
    if not members:
        return 0

    updated = 0
    for lock_key in members:
        # lockKey 格式：lock:{schedule_id}:{player_id}
        parts = lock_key.split(":")
        if len(parts) != 3:
            r.zrem(_LOCK_EXP_ZSET, lock_key)
            continue

        schedule_id = int(parts[1])
        player_id = int(parts[2])

        # 若 key 仍存在，说明还未到期（或被重置），跳过
        if r.exists(lock_key):
            r.zrem(_LOCK_EXP_ZSET, lock_key)
            continue

        # seats +1
        ensure_seats_initialized(schedule_id)
        r.incr(_seats_key(schedule_id))
        r.zrem(_LOCK_EXP_ZSET, lock_key)

        # Mongo：把对应的“仍为锁定且已过期”的记录标为过期（Status=3）
        now = datetime.now()
        res = col("lock_records").update_many(
            {
                "Schedule_ID": schedule_id,
                "Player_ID": player_id,
                "Status": 0,
                "ExpireTime": {"$lte": now},
            },
            {"$set": {"Status": 3}},
        )
        updated += int(res.modified_count)

    return updated
