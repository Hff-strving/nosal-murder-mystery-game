# -*- coding: utf-8 -*-
"""
剧本模型 - MongoDB 版本
"""

from __future__ import annotations

import logging
from typing import List

from nosql.mongo import col, project
from security_utils import InputValidator

logger = logging.getLogger(__name__)


class ScriptModel:
    @staticmethod
    def get_all_scripts(status=None) -> List[dict]:
        try:
            query = {}
            if status is not None:
                status = InputValidator.validate_enum(status, [0, 1], "剧本状态")
                query["Status"] = int(status)

            cursor = col("scripts").find(
                query,
                {
                    "_id": 0,
                    "Script_ID": 1,
                    "Title": 1,
                    "Type": 1,
                    "Min_Players": 1,
                    "Max_Players": 1,
                    "Duration": 1,
                    "Base_Price": 1,
                    "Status": 1,
                    "Cover_Image": 1,
                    "Group_Category": 1,
                    "Difficulty": 1,
                    "Gender_Config": 1,
                },
            ).sort("Script_ID", 1)
            return list(cursor)
        except Exception as e:
            logger.error(f"获取剧本列表失败: {str(e)}")
            raise

    @staticmethod
    def get_script_by_id(script_id: int) -> dict:
        try:
            script_id = InputValidator.validate_id(script_id, "剧本ID")
            doc = col("scripts").find_one(
                {"_id": int(script_id)},
                {"_id": 0},
            )
            if not doc:
                raise ValueError(f"剧本ID {script_id} 不存在")
            return doc
        except Exception as e:
            logger.error(f"获取剧本详情失败: {str(e)}")
            raise

    @staticmethod
    def get_hot_scripts(limit: int = 10) -> List[dict]:
        """
        热门剧本：按已支付订单数 + 总金额排序（与原 MySQL 逻辑一致）。
        说明：orders 中已冗余 Script_ID / Amount 等字段，因此无需多表 JOIN。
        """
        try:
            limit = InputValidator.validate_id(limit, "限制数量")

            pipeline = [
                {"$match": {"Pay_Status": 1, "Script_ID": {"$ne": None}}},
                {
                    "$group": {
                        "_id": "$Script_ID",
                        "paid_orders": {"$sum": 1},
                        "total_amount": {"$sum": {"$ifNull": ["$Amount", 0]}},
                    }
                },
                {"$sort": {"paid_orders": -1, "total_amount": -1}},
                {"$limit": int(limit)},
            ]
            stats = list(col("orders").aggregate(pipeline))
            if not stats:
                return []

            script_ids = [int(s["_id"]) for s in stats]
            scripts = list(
                col("scripts").find(
                    {"_id": {"$in": script_ids}, "Status": 1},
                    {
                        "_id": 0,
                        "Script_ID": 1,
                        "Title": 1,
                        "Type": 1,
                        "Min_Players": 1,
                        "Max_Players": 1,
                        "Duration": 1,
                        "Base_Price": 1,
                        "Status": 1,
                        "Cover_Image": 1,
                        "Group_Category": 1,
                        "Difficulty": 1,
                        "Gender_Config": 1,
                    },
                )
            )
            by_id = {int(s["Script_ID"]): s for s in scripts}

            results = []
            for idx, row in enumerate(stats):
                sid = int(row["_id"])
                base = by_id.get(sid) or {"Script_ID": sid}
                base["paid_orders"] = int(row.get("paid_orders") or 0)
                base["total_amount"] = float(row.get("total_amount") or 0)
                base["hot_rank"] = idx + 1
                results.append(base)

            return results
        except Exception as e:
            logger.error(f"获取热门剧本失败: {str(e)}")
            raise

