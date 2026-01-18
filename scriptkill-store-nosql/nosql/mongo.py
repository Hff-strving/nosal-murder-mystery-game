# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, Optional

from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo import ReturnDocument
from pymongo.collection import Collection
from pymongo.database import Database
from pymongo.errors import PyMongoError

from nosql.config import MONGO_DB_NAME, MONGO_URI

logger = logging.getLogger(__name__)

_client: Optional[MongoClient] = None
_db: Optional[Database] = None


def get_client() -> MongoClient:
    global _client
    if _client is None:
        _client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=3000)
    return _client


def get_db() -> Database:
    global _db
    if _db is None:
        _db = get_client()[MONGO_DB_NAME]
    return _db


def col(name: str) -> Collection:
    return get_db()[name]


def ping() -> bool:
    try:
        get_client().admin.command("ping")
        return True
    except PyMongoError:
        return False


def get_next_sequence(name: str, start: int = 1) -> int:
    counters = col("counters")
    row = counters.find_one_and_update(
        {"_id": name},
        [
            {"$set": {"seq": {"$ifNull": ["$seq", start - 1]}}},
            {"$set": {"seq": {"$add": ["$seq", 1]}, "updated_at": datetime.utcnow()}},
        ],
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return int(row.get("seq") or start)


def ensure_indexes() -> None:
    """
    建立核心索引（满足作业“知识点”要求，也保证查询性能）。
    可以重复调用（幂等）。
    """
    db = get_db()

    db["users"].create_index([("Username", ASCENDING)], unique=True, name="uk_users_username")
    db["users"].create_index([("Phone", ASCENDING)], unique=True, name="uk_users_phone")
    db["users"].create_index([("Role", ASCENDING)], name="idx_users_role")

    db["players"].create_index([("Phone", ASCENDING)], name="idx_players_phone")
    db["dms"].create_index([("Phone", ASCENDING)], name="idx_dms_phone")

    db["scripts"].create_index([("Status", ASCENDING)], name="idx_scripts_status")
    db["scripts"].create_index([("Title", ASCENDING)], name="idx_scripts_title")

    db["rooms"].create_index([("Room_Name", ASCENDING)], name="idx_rooms_name")

    db["schedules"].create_index([("Script_ID", ASCENDING), ("Start_Time", ASCENDING)], name="idx_sch_script_start")
    db["schedules"].create_index([("Start_Time", ASCENDING)], name="idx_sch_start")
    db["schedules"].create_index([("DM_ID", ASCENDING)], name="idx_sch_dm")
    db["schedules"].create_index([("Room_ID", ASCENDING)], name="idx_sch_room")

    db["orders"].create_index([("Player_ID", ASCENDING), ("Create_Time", DESCENDING)], name="idx_orders_player_time")
    db["orders"].create_index([("Schedule_ID", ASCENDING)], name="idx_orders_schedule")
    db["orders"].create_index([("DM_ID", ASCENDING)], name="idx_orders_dm")
    db["orders"].create_index([("Pay_Status", ASCENDING)], name="idx_orders_pay_status")
    db["orders"].create_index(
        [("Player_ID", ASCENDING), ("Schedule_ID", ASCENDING), ("Pay_Status", ASCENDING)],
        name="idx_orders_player_schedule_status",
    )

    db["transactions"].create_index([("Order_ID", ASCENDING)], name="idx_tx_order")
    db["transactions"].create_index([("Trans_Time", DESCENDING)], name="idx_tx_time")

    db["lock_records"].create_index([("Player_ID", ASCENDING), ("LockTime", DESCENDING)], name="idx_locks_player_time")
    db["lock_records"].create_index([("Schedule_ID", ASCENDING)], name="idx_locks_schedule")
    db["lock_records"].create_index([("ExpireTime", ASCENDING)], name="idx_locks_expire")
    db["lock_records"].create_index([("DM_ID", ASCENDING)], name="idx_locks_dm")
    db["lock_records"].create_index([("Status", ASCENDING)], name="idx_locks_status")

    logger.info("MongoDB indexes ensured.")


def project(doc: Dict[str, Any]) -> Dict[str, Any]:
    if not doc:
        return {}
    doc.pop("_id", None)
    return doc
