# -*- coding: utf-8 -*-
"""
锁位模型 - Redis(有效锁位/座位) + MongoDB(历史记录) 版本
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import List, Optional

from nosql.mongo import col
from nosql.seat_lock_service import cancel_lock as redis_cancel_lock
from nosql.seat_lock_service import create_lock as redis_create_lock
from security_utils import InputValidator

logger = logging.getLogger(__name__)


class LockModel:
    @staticmethod
    def create_lock(player_id: int, schedule_id: int, lock_minutes: int = 15) -> int:
        try:
            player_id = InputValidator.validate_id(player_id, "玩家ID")
            schedule_id = InputValidator.validate_id(schedule_id, "场次ID")

            sch = col("schedules").find_one({"_id": int(schedule_id)})
            if not sch:
                raise ValueError("场次不存在")

            now = datetime.now()
            # Mongo 兜底防重复（Redis 已做原子判断，这里用于更友好提示）
            dup = col("lock_records").find_one(
                {"Schedule_ID": int(schedule_id), "Player_ID": int(player_id), "Status": 0, "ExpireTime": {"$gt": now}},
                {"_id": 1},
            )
            if dup:
                raise ValueError("您已经锁定了该场次")

            lock_id, expire_time = redis_create_lock(int(player_id), int(schedule_id), lock_minutes)

            doc = {
                "_id": int(lock_id),
                "LockID": int(lock_id),
                "Schedule_ID": int(schedule_id),
                "Player_ID": int(player_id),
                "LockTime": now,
                "ExpireTime": expire_time,
                "Status": 0,
                # 反范式字段
                "Script_ID": sch.get("Script_ID"),
                "Script_Title": sch.get("Script_Title"),
                "Start_Time": sch.get("Start_Time"),
                "Room_ID": sch.get("Room_ID"),
                "Room_Name": sch.get("Room_Name"),
                "DM_ID": sch.get("DM_ID"),
                "DM_Name": sch.get("DM_Name"),
            }
            col("lock_records").insert_one(doc)
            return int(lock_id)
        except Exception as e:
            logger.error(f"创建锁位失败: {str(e)}")
            raise

    @staticmethod
    def cancel_lock(lock_id: int, player_id: int) -> bool:
        try:
            lock_id = InputValidator.validate_id(lock_id, "锁位ID")
            player_id = InputValidator.validate_id(player_id, "玩家ID")

            lock = col("lock_records").find_one({"_id": int(lock_id)})
            if not lock:
                raise ValueError("锁位记录不存在")
            if int(lock.get("Player_ID")) != int(player_id):
                raise ValueError("无权取消他人锁位")
            if int(lock.get("Status") or 0) != 0:
                raise ValueError("该锁位已失效")
            if lock.get("ExpireTime") and lock["ExpireTime"] <= datetime.now():
                # 已过期：不再允许取消（由清理任务归还座位）
                raise ValueError("该锁位已过期")

            ok = redis_cancel_lock(int(player_id), int(lock["Schedule_ID"]))
            if ok:
                col("lock_records").update_one({"_id": int(lock_id)}, {"$set": {"Status": 2}})
            return bool(ok)
        except Exception as e:
            logger.error(f"取消锁位失败: {str(e)}")
            raise

    @staticmethod
    def get_locks_by_player(player_id: int) -> List[dict]:
        try:
            player_id = InputValidator.validate_id(player_id, "玩家ID")
            locks = list(
                col("lock_records")
                .find({"Player_ID": int(player_id)}, {"_id": 0})
                .sort("LockTime", -1)
            )
            return locks if locks else []
        except Exception as e:
            logger.error(f"查询玩家锁位失败: {str(e)}")
            raise

    @staticmethod
    def get_all_locks(dm_id: Optional[int] = None) -> List[dict]:
        try:
            query = {}
            if dm_id is not None:
                query["DM_ID"] = int(dm_id)
            locks = list(col("lock_records").find(query, {"_id": 0}).sort("LockTime", -1))
            return locks if locks else []
        except Exception as e:
            logger.error(f"查询锁位列表失败: {str(e)}")
            raise

