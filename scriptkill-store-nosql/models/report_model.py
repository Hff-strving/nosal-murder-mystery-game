# -*- coding: utf-8 -*-
"""
报表模型 - MongoDB 版本
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from nosql.mongo import col

logger = logging.getLogger(__name__)


def _parse_date(date_str: str) -> datetime:
    return datetime.strptime(date_str, "%Y-%m-%d")


def _day_start(dt: datetime) -> datetime:
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)


def _month_start(dt: datetime) -> datetime:
    return dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _week_start_monday(dt: datetime) -> datetime:
    base = _day_start(dt)
    return base - timedelta(days=base.weekday())


class ReportModel:
    @staticmethod
    def get_dashboard_stats(dm_id: Optional[int] = None) -> dict:
        try:
            now = datetime.now()
            today0 = _day_start(now)
            week0 = _week_start_monday(now)
            month0 = _month_start(now)

            tx_match_base: Dict[str, Any] = {"Trans_Type": 1, "Result": 1}
            if dm_id is not None:
                tx_match_base["DM_ID"] = int(dm_id)

            def _sum_count(start: datetime) -> tuple[float, int]:
                pipeline = [
                    {"$match": {**tx_match_base, "Trans_Time": {"$gte": start, "$lte": now}}},
                    {"$group": {"_id": None, "revenue": {"$sum": "$Amount"}, "orders": {"$sum": 1}}},
                ]
                row = next(iter(col("transactions").aggregate(pipeline)), None) or {}
                return float(row.get("revenue") or 0), int(row.get("orders") or 0)

            today_revenue, today_orders = _sum_count(today0)
            week_revenue, week_orders = _sum_count(week0)
            month_revenue, month_orders = _sum_count(month0)

            # 活跃锁位
            lock_query: Dict[str, Any] = {"Status": 0, "ExpireTime": {"$gt": now}}
            if dm_id is not None:
                lock_query["DM_ID"] = int(dm_id)
            active_locks = col("lock_records").count_documents(lock_query)

            # 未来 7 天上座率
            sch_query: Dict[str, Any] = {
                "Start_Time": {"$gte": now, "$lt": now + timedelta(days=7)},
                "Status": {"$in": [0, 1]},
            }
            if dm_id is not None:
                sch_query["DM_ID"] = int(dm_id)
            schedules = list(col("schedules").find(sch_query, {"Schedule_ID": 1, "Max_Players": 1}))
            schedule_ids = [int(s["Schedule_ID"]) for s in schedules]
            capacity = sum(int(s.get("Max_Players") or 0) for s in schedules)
            occupied = 0
            if schedule_ids:
                occupied += col("orders").count_documents(
                    {"Schedule_ID": {"$in": schedule_ids}, "Pay_Status": {"$in": [0, 1]}}
                )
                occupied += col("lock_records").count_documents(
                    {"Schedule_ID": {"$in": schedule_ids}, "Status": 0, "ExpireTime": {"$gt": now}}
                )
            occupancy_rate = round((occupied / capacity) * 100, 2) if capacity > 0 else 0.0

            # 最近订单（10条）
            order_query: Dict[str, Any] = {}
            if dm_id is not None:
                order_query["DM_ID"] = int(dm_id)
            recent_orders = list(
                col("orders")
                .find(
                    order_query,
                    {
                        "_id": 0,
                        "Order_ID": 1,
                        "Amount": 1,
                        "Pay_Status": 1,
                        "Create_Time": 1,
                        "Script_Title": 1,
                        "Start_Time": 1,
                        "Room_Name": 1,
                        "DM_ID": 1,
                        "DM_Name": 1,
                    },
                )
                .sort("Create_Time", -1)
                .limit(10)
            )

            # 未来场次（10条）
            upcoming_query: Dict[str, Any] = {"Start_Time": {"$gt": now}, "Status": {"$in": [0, 1]}}
            if dm_id is not None:
                upcoming_query["DM_ID"] = int(dm_id)
            upcoming_schedules = list(
                col("schedules")
                .find(
                    upcoming_query,
                    {
                        "_id": 0,
                        "Schedule_ID": 1,
                        "Start_Time": 1,
                        "Real_Price": 1,
                        "Room_Name": 1,
                        "DM_ID": 1,
                        "DM_Name": 1,
                        "Script_ID": 1,
                        "Script_Title": 1,
                        "Max_Players": 1,
                    },
                )
                .sort("Start_Time", 1)
                .limit(10)
            )

            return {
                "today_revenue": round(today_revenue, 2),
                "today_orders": today_orders,
                "week_revenue": round(week_revenue, 2),
                "week_orders": week_orders,
                "month_revenue": round(month_revenue, 2),
                "month_orders": month_orders,
                "active_locks": int(active_locks),
                "occupancy_rate": occupancy_rate,
                "recent_orders": recent_orders,
                "upcoming_schedules": upcoming_schedules,
            }
        except Exception as e:
            logger.error(f"查询仪表盘统计失败: {str(e)}")
            raise

    @staticmethod
    def get_top_scripts(
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        limit: int = 5,
        dm_id: Optional[int] = None,
    ) -> List[dict]:
        try:
            match: Dict[str, Any] = {"Script_ID": {"$ne": None}}
            if dm_id is not None:
                match["DM_ID"] = int(dm_id)
            if start_date:
                match["Create_Time"] = {**match.get("Create_Time", {}), "$gte": _parse_date(start_date)}
            if end_date:
                match["Create_Time"] = {**match.get("Create_Time", {}), "$lte": _parse_date(end_date) + timedelta(days=1)}

            pipeline = [
                {"$match": match},
                {
                    "$group": {
                        "_id": "$Script_ID",
                        "Title": {"$first": "$Script_Title"},
                        "order_count": {
                            "$sum": {"$cond": [{"$in": ["$Pay_Status", [0, 1]]}, 1, 0]}
                        },
                        "total_revenue": {
                            "$sum": {"$cond": [{"$eq": ["$Pay_Status", 1]}, "$Amount", 0]}
                        },
                    }
                },
                {"$sort": {"order_count": -1, "total_revenue": -1}},
                {"$limit": int(limit)},
            ]
            rows = list(col("orders").aggregate(pipeline))
            results = []
            for r in rows:
                results.append(
                    {
                        "Script_ID": int(r["_id"]),
                        "Title": r.get("Title") or "",
                        "order_count": int(r.get("order_count") or 0),
                        "total_revenue": float(r.get("total_revenue") or 0),
                    }
                )
            return results
        except Exception as e:
            logger.error(f"查询热门剧本失败: {str(e)}")
            raise

    @staticmethod
    def get_room_utilization(
        start_date: Optional[str] = None, end_date: Optional[str] = None, dm_id: Optional[int] = None
    ) -> List[dict]:
        try:
            match: Dict[str, Any] = {}
            if start_date:
                match["Start_Time"] = {**match.get("Start_Time", {}), "$gte": _parse_date(start_date)}
            if end_date:
                match["Start_Time"] = {**match.get("Start_Time", {}), "$lte": _parse_date(end_date) + timedelta(days=1)}
            if dm_id is not None:
                match["DM_ID"] = int(dm_id)

            pipeline = [
                {"$match": match},
                {
                    "$lookup": {
                        "from": "orders",
                        "let": {"sid": "$Schedule_ID"},
                        "pipeline": [
                            {
                                "$match": {
                                    "$expr": {
                                        "$and": [
                                            {"$eq": ["$Schedule_ID", "$$sid"]},
                                            {"$eq": ["$Pay_Status", 1]},
                                        ]
                                    }
                                }
                            },
                            {"$project": {"_id": 0, "Order_ID": 1}},
                        ],
                        "as": "paid_orders",
                    }
                },
                {
                    "$project": {
                        "Room_ID": 1,
                        "Room_Name": 1,
                        "Status": 1,
                        "paid_count": {"$size": "$paid_orders"},
                    }
                },
                {
                    "$group": {
                        "_id": {"Room_ID": "$Room_ID", "Room_Name": "$Room_Name"},
                        "total_schedules": {"$sum": 1},
                        "completed_schedules": {"$sum": {"$cond": [{"$eq": ["$Status", 1]}, 1, 0]}},
                        "paid_orders": {"$sum": "$paid_count"},
                    }
                },
                {"$sort": {"paid_orders": -1, "completed_schedules": -1, "total_schedules": -1}},
            ]
            rows = list(col("schedules").aggregate(pipeline))
            results = []
            for r in rows:
                total = int(r.get("total_schedules") or 0)
                completed = int(r.get("completed_schedules") or 0)
                util = round((completed * 100.0 / total), 2) if total > 0 else 0
                results.append(
                    {
                        "Room_ID": r["_id"].get("Room_ID"),
                        "Room_Name": r["_id"].get("Room_Name") or "",
                        "total_schedules": total,
                        "completed_schedules": completed,
                        "paid_orders": int(r.get("paid_orders") or 0),
                        "utilization_rate": util,
                    }
                )
            return results
        except Exception as e:
            logger.error(f"查询房间利用率失败: {str(e)}")
            raise

    @staticmethod
    def get_lock_conversion_rate(
        start_date: Optional[str] = None, end_date: Optional[str] = None, dm_id: Optional[int] = None
    ) -> dict:
        try:
            match_locks: Dict[str, Any] = {}
            if start_date:
                match_locks["LockTime"] = {**match_locks.get("LockTime", {}), "$gte": _parse_date(start_date)}
            if end_date:
                match_locks["LockTime"] = {
                    **match_locks.get("LockTime", {}),
                    "$lte": _parse_date(end_date) + timedelta(days=1),
                }
            if dm_id is not None:
                match_locks["DM_ID"] = int(dm_id)

            total_locks = col("lock_records").count_documents(match_locks)
            converted_locks = col("lock_records").count_documents({**match_locks, "Status": 1})

            match_orders: Dict[str, Any] = {}
            if start_date:
                match_orders["Create_Time"] = {**match_orders.get("Create_Time", {}), "$gte": _parse_date(start_date)}
            if end_date:
                match_orders["Create_Time"] = {
                    **match_orders.get("Create_Time", {}),
                    "$lte": _parse_date(end_date) + timedelta(days=1),
                }
            if dm_id is not None:
                match_orders["DM_ID"] = int(dm_id)

            total_orders = col("orders").count_documents(match_orders)
            paid_orders = col("orders").count_documents({**match_orders, "Pay_Status": 1})

            lock_to_order_rate = round((converted_locks * 100.0 / total_locks), 2) if total_locks > 0 else 0
            order_to_pay_rate = round((paid_orders * 100.0 / total_orders), 2) if total_orders > 0 else 0

            return {
                "total_locks": int(total_locks),
                "converted_locks": int(converted_locks),
                "total_orders": int(total_orders),
                "paid_orders": int(paid_orders),
                "lock_to_order_rate": lock_to_order_rate,
                "order_to_pay_rate": order_to_pay_rate,
            }
        except Exception as e:
            logger.error(f"查询锁位转化率失败: {str(e)}")
            raise

    @staticmethod
    def get_dm_performance(start_date: Optional[str] = None, end_date: Optional[str] = None) -> List[dict]:
        try:
            dms = list(col("dms").find({}, {"_id": 0}))
            if not dms:
                return []

            now = datetime.now()
            start_dt = _parse_date(start_date) if start_date else None
            end_dt = (_parse_date(end_date) + timedelta(days=1)) if end_date else None

            results = []
            for dm in dms:
                dm_id = int(dm.get("DM_ID") or 0)
                if dm_id <= 0:
                    continue

                sch_match: Dict[str, Any] = {"DM_ID": dm_id}
                if start_dt:
                    sch_match["Start_Time"] = {**sch_match.get("Start_Time", {}), "$gte": start_dt}
                if end_dt:
                    sch_match["Start_Time"] = {**sch_match.get("Start_Time", {}), "$lt": end_dt}

                schedule_count = col("schedules").count_documents(sch_match)

                order_match: Dict[str, Any] = {"DM_ID": dm_id}
                if start_dt:
                    order_match["Start_Time"] = {**order_match.get("Start_Time", {}), "$gte": start_dt}
                if end_dt:
                    order_match["Start_Time"] = {**order_match.get("Start_Time", {}), "$lt": end_dt}

                order_count = col("orders").count_documents(order_match)
                paid_orders = col("orders").count_documents({**order_match, "Pay_Status": 1})

                tx_match: Dict[str, Any] = {"DM_ID": dm_id, "Trans_Type": 1, "Result": 1}
                if start_dt:
                    tx_match["Trans_Time"] = {**tx_match.get("Trans_Time", {}), "$gte": start_dt}
                if end_dt:
                    tx_match["Trans_Time"] = {**tx_match.get("Trans_Time", {}), "$lt": end_dt}

                revenue_row = next(
                    iter(
                        col("transactions").aggregate(
                            [{"$match": tx_match}, {"$group": {"_id": None, "revenue": {"$sum": "$Amount"}}}]
                        )
                    ),
                    None,
                )
                revenue = float((revenue_row or {}).get("revenue") or 0)

                active_locks = col("lock_records").count_documents(
                    {"DM_ID": dm_id, "Status": 0, "ExpireTime": {"$gt": now}}
                )

                results.append(
                    {
                        "DM_ID": dm_id,
                        "DM_Name": dm.get("Name") or dm.get("DM_Name") or "",
                        "Phone": dm.get("Phone"),
                        "Star_Level": dm.get("Star_Level"),
                        "schedule_count": int(schedule_count),
                        "order_count": int(order_count),
                        "paid_orders": int(paid_orders),
                        "revenue": round(revenue, 2),
                        "active_locks": int(active_locks),
                    }
                )

            results.sort(key=lambda x: (x.get("revenue", 0), x.get("paid_orders", 0), x.get("order_count", 0)), reverse=True)
            return results
        except Exception as e:
            logger.error(f"查询DM业绩失败: {str(e)}")
            raise

