# -*- coding: utf-8 -*-
"""
场次模型 - MongoDB/Redis 版本
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from nosql.mongo import col, get_next_sequence
from nosql.seat_lock_service import ensure_seats_initialized
from security_utils import InputValidator

logger = logging.getLogger(__name__)


def _parse_date(date_str: str) -> datetime:
    return datetime.strptime(date_str, "%Y-%m-%d")


def _parse_dt(value) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        # 兼容前端传入：2024-01-01 14:00:00
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    raise ValueError("时间格式错误，应为 YYYY-MM-DD HH:MM:SS")


class ScheduleModel:
    @staticmethod
    def get_schedules_by_script(script_id: int, player_id: Optional[int] = None) -> List[dict]:
        try:
            script_id = InputValidator.validate_id(script_id, "剧本ID")
            now = datetime.now()

            schedules = list(
                col("schedules")
                .find(
                    {"Script_ID": int(script_id), "Start_Time": {"$gt": now}, "Status": {"$in": [0, 1]}},
                    {"_id": 0},
                )
                .sort("Start_Time", 1)
            )

            if not schedules:
                return []

            schedule_ids = [int(s["Schedule_ID"]) for s in schedules]

            # 订单数（未付+已付）
            booked_map = {}
            for row in col("orders").aggregate(
                [
                    {"$match": {"Schedule_ID": {"$in": schedule_ids}, "Pay_Status": {"$in": [0, 1]}}},
                    {"$group": {"_id": "$Schedule_ID", "cnt": {"$sum": 1}}},
                ]
            ):
                booked_map[int(row["_id"])] = int(row["cnt"])

            # 有效锁位数
            locked_map = {}
            for row in col("lock_records").aggregate(
                [
                    {
                        "$match": {
                            "Schedule_ID": {"$in": schedule_ids},
                            "Status": 0,
                            "ExpireTime": {"$gt": now},
                        }
                    },
                    {"$group": {"_id": "$Schedule_ID", "cnt": {"$sum": 1}}},
                ]
            ):
                locked_map[int(row["_id"])] = int(row["cnt"])

            # 当前玩家状态（可选）
            user_booked = {}
            user_locked = {}
            if player_id:
                player_id = InputValidator.validate_id(player_id, "玩家ID")
                for row in col("orders").aggregate(
                    [
                        {
                            "$match": {
                                "Schedule_ID": {"$in": schedule_ids},
                                "Player_ID": int(player_id),
                                "Pay_Status": {"$in": [0, 1]},
                            }
                        },
                        {"$group": {"_id": "$Schedule_ID", "cnt": {"$sum": 1}}},
                    ]
                ):
                    user_booked[int(row["_id"])] = int(row["cnt"])

                for row in col("lock_records").aggregate(
                    [
                        {
                            "$match": {
                                "Schedule_ID": {"$in": schedule_ids},
                                "Player_ID": int(player_id),
                                "Status": 0,
                                "ExpireTime": {"$gt": now},
                            }
                        },
                        {"$group": {"_id": "$Schedule_ID", "cnt": {"$sum": 1}}},
                    ]
                ):
                    user_locked[int(row["_id"])] = int(row["cnt"])

            for sch in schedules:
                sid = int(sch["Schedule_ID"])
                sch["Booked_Count"] = booked_map.get(sid, 0)
                sch["Locked_Count"] = locked_map.get(sid, 0)
                if player_id:
                    sch["User_Booked"] = user_booked.get(sid, 0)
                    sch["User_Locked"] = user_locked.get(sid, 0)

            return schedules
        except Exception as e:
            logger.error(f"查询剧本场次失败: {str(e)}")
            raise

    @staticmethod
    def get_all_schedules(
        date: Optional[str] = None,
        room_id: Optional[int] = None,
        script_id: Optional[int] = None,
        status: Optional[int] = None,
        dm_id: Optional[int] = None,
    ) -> List[dict]:
        try:
            query: Dict[str, Any] = {}
            if date:
                day = _parse_date(date)
                query["Start_Time"] = {"$gte": day, "$lt": day + timedelta(days=1)}
            if room_id:
                query["Room_ID"] = InputValidator.validate_id(room_id, "房间ID")
            if script_id:
                query["Script_ID"] = InputValidator.validate_id(script_id, "剧本ID")
            if status is not None:
                query["Status"] = int(status)
            if dm_id is not None:
                query["DM_ID"] = int(dm_id)

            schedules_raw = list(col("schedules").find(query).sort("Start_Time", -1))
            if not schedules_raw:
                return []

            # 兼容历史/测试数据：允许只有 _id 但没有 Schedule_ID，或缺少关键字段的脏数据
            schedules: List[dict] = []
            for sch in schedules_raw:
                sid = sch.get("Schedule_ID") or sch.get("_id")
                if sid is None:
                    continue
                sch["Schedule_ID"] = int(sid)
                sch.pop("_id", None)
                schedules.append(sch)
            if not schedules:
                return []

            now = datetime.now()
            schedule_ids = [int(s["Schedule_ID"]) for s in schedules]

            booked_map = {}
            for row in col("orders").aggregate(
                [
                    {"$match": {"Schedule_ID": {"$in": schedule_ids}, "Pay_Status": {"$in": [0, 1]}}},
                    {"$group": {"_id": "$Schedule_ID", "cnt": {"$sum": 1}}},
                ]
            ):
                booked_map[int(row["_id"])] = int(row["cnt"])

            locked_map = {}
            for row in col("lock_records").aggregate(
                [
                    {"$match": {"Schedule_ID": {"$in": schedule_ids}, "Status": 0, "ExpireTime": {"$gt": now}}},
                    {"$group": {"_id": "$Schedule_ID", "cnt": {"$sum": 1}}},
                ]
            ):
                locked_map[int(row["_id"])] = int(row["cnt"])

            for sch in schedules:
                sid = int(sch["Schedule_ID"])
                sch["Booked_Count"] = booked_map.get(sid, 0)
                sch["Locked_Count"] = locked_map.get(sid, 0)

            return schedules
        except Exception as e:
            logger.error(f"查询所有场次失败: {str(e)}")
            raise

    @staticmethod
    def create_schedule(script_id, room_id, dm_id, start_time, end_time, real_price) -> int:
        try:
            script_id = InputValidator.validate_id(script_id, "剧本ID")
            room_id = InputValidator.validate_id(room_id, "房间ID")
            dm_id = InputValidator.validate_id(dm_id, "DM ID")

            script = col("scripts").find_one({"_id": int(script_id)})
            room = col("rooms").find_one({"_id": int(room_id)})
            dm = col("dms").find_one({"_id": int(dm_id)})
            if not script or not room or not dm:
                raise ValueError("剧本/房间/DM 不存在")

            schedule_id = get_next_sequence("Schedule_ID", start=4001)
            doc = {
                "_id": int(schedule_id),
                "Schedule_ID": int(schedule_id),
                "Script_ID": int(script_id),
                "Room_ID": int(room_id),
                "DM_ID": int(dm_id),
                "Start_Time": _parse_dt(start_time),
                "End_Time": _parse_dt(end_time),
                "Real_Price": float(real_price),
                "Status": 0,
                # 反范式字段（减少查询拼装）
                "Room_Name": room.get("Room_Name"),
                "DM_Name": dm.get("Name") or dm.get("DM_Name") or dm.get("Name"),
                "Script_Title": script.get("Title"),
                "Max_Players": int(script.get("Max_Players") or 0),
                "Script_Cover": script.get("Cover_Image"),
            }
            col("schedules").insert_one(doc)
            ensure_seats_initialized(int(schedule_id))
            return int(schedule_id)
        except Exception as e:
            logger.error(f"创建场次失败: {str(e)}")
            raise

    @staticmethod
    def update_schedule(
        schedule_id,
        script_id=None,
        room_id=None,
        dm_id=None,
        start_time=None,
        end_time=None,
        real_price=None,
        status=None,
    ) -> int:
        try:
            schedule_id = InputValidator.validate_id(schedule_id, "场次ID")

            updates: Dict[str, Any] = {}
            if script_id is not None:
                script_id = InputValidator.validate_id(script_id, "剧本ID")
                script = col("scripts").find_one({"_id": int(script_id)})
                if not script:
                    raise ValueError("剧本不存在")
                updates.update(
                    {
                        "Script_ID": int(script_id),
                        "Script_Title": script.get("Title"),
                        "Max_Players": int(script.get("Max_Players") or 0),
                        "Script_Cover": script.get("Cover_Image"),
                    }
                )

            if room_id is not None:
                room_id = InputValidator.validate_id(room_id, "房间ID")
                room = col("rooms").find_one({"_id": int(room_id)})
                if not room:
                    raise ValueError("房间不存在")
                updates.update({"Room_ID": int(room_id), "Room_Name": room.get("Room_Name")})

            if dm_id is not None:
                dm_id = InputValidator.validate_id(dm_id, "DM ID")
                dm = col("dms").find_one({"_id": int(dm_id)})
                if not dm:
                    raise ValueError("DM不存在")
                updates.update({"DM_ID": int(dm_id), "DM_Name": dm.get("Name") or dm.get("DM_Name")})

            if start_time is not None:
                updates["Start_Time"] = _parse_dt(start_time)
            if end_time is not None:
                updates["End_Time"] = _parse_dt(end_time)
            if real_price is not None:
                updates["Real_Price"] = float(real_price)
            if status is not None:
                updates["Status"] = int(status)

            if not updates:
                raise ValueError("没有需要更新的字段")

            res = col("schedules").update_one({"_id": int(schedule_id)}, {"$set": updates})
            if res.matched_count == 0:
                raise ValueError("场次不存在")

            # seats 可能依赖 Max_Players；若修改脚本（Max_Players）建议重置 seats
            ensure_seats_initialized(int(schedule_id))
            return int(res.modified_count)
        except Exception as e:
            logger.error(f"更新场次失败: {str(e)}")
            raise

    @staticmethod
    def cancel_schedule(schedule_id: int) -> int:
        try:
            schedule_id = InputValidator.validate_id(schedule_id, "场次ID")
            paid = col("orders").count_documents({"Schedule_ID": int(schedule_id), "Pay_Status": 1})
            if paid > 0:
                raise ValueError("该场次有已支付订单，无法取消")

            res = col("schedules").update_one({"_id": int(schedule_id)}, {"$set": {"Status": 2}})
            if res.matched_count == 0:
                raise ValueError("场次不存在")
            return int(res.modified_count)
        except Exception as e:
            logger.error(f"取消场次失败: {str(e)}")
            raise

    @staticmethod
    def get_schedule_basic(schedule_id: int) -> dict:
        schedule_id = InputValidator.validate_id(schedule_id, "场次ID")
        sch = col("schedules").find_one({"_id": int(schedule_id)}, {"_id": 0})
        if not sch:
            raise ValueError("场次不存在")
        return sch
