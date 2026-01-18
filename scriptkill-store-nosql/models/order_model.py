# -*- coding: utf-8 -*-
"""
订单模型 - MongoDB/Redis 版本
"""

from __future__ import annotations

import logging
import random
from datetime import datetime
from typing import List, Optional

from nosql.mongo import col
from nosql.redis_client import get_redis
from nosql.seat_lock_service import convert_lock_to_order, get_active_lock_id, release_seat, take_seat
from security_utils import InputValidator

logger = logging.getLogger(__name__)


class OrderModel:
    STATUS_UNPAID = 0
    STATUS_PAID = 1
    STATUS_REFUNDED = 2
    STATUS_CANCELLED = 3

    @staticmethod
    def _gen_id() -> int:
        return int(datetime.now().strftime("%Y%m%d%H%M%S")) + random.randint(1000, 9999)

    @staticmethod
    def create_order(player_id: int, schedule_id: int, amount=None) -> int:
        try:
            player_id = InputValidator.validate_id(player_id, "玩家ID")
            schedule_id = InputValidator.validate_id(schedule_id, "场次ID")

            # 防重复预约：同一玩家同一场次有效订单（未付/已付）
            dup = col("orders").find_one(
                {"Player_ID": int(player_id), "Schedule_ID": int(schedule_id), "Pay_Status": {"$in": [0, 1]}},
                {"_id": 1},
            )
            if dup:
                raise ValueError("您已经预约过该场次，请勿重复预约")

            sch = col("schedules").find_one({"_id": int(schedule_id)})
            if not sch:
                raise ValueError(f"场次 {schedule_id} 不存在")

            actual_amount = float(sch.get("Real_Price") or 0)

            # 先尝试“用锁位转订单”，若无锁位则占一个座位
            lock_id = get_active_lock_id(int(player_id), int(schedule_id))
            took_seat = False
            if lock_id is None:
                if not take_seat(int(schedule_id)):
                    raise ValueError("该场次已满")
                took_seat = True

            order_id = OrderModel._gen_id()
            now = datetime.now()
            doc = {
                "_id": int(order_id),
                "Order_ID": int(order_id),
                "Player_ID": int(player_id),
                "Schedule_ID": int(schedule_id),
                "Amount": actual_amount,
                "Pay_Status": OrderModel.STATUS_UNPAID,
                "Create_Time": now,
                # 反范式字段（用于列表/报表）
                "Script_ID": sch.get("Script_ID"),
                "Script_Title": sch.get("Script_Title"),
                "Room_ID": sch.get("Room_ID"),
                "Room_Name": sch.get("Room_Name"),
                "DM_ID": sch.get("DM_ID"),
                "DM_Name": sch.get("DM_Name"),
                "Start_Time": sch.get("Start_Time"),
            }
            col("orders").insert_one(doc)

            if lock_id is not None:
                convert_lock_to_order(int(player_id), int(schedule_id))
                # Mongo：锁位状态转“已转订单”
                col("lock_records").update_many(
                    {
                        "Schedule_ID": int(schedule_id),
                        "Player_ID": int(player_id),
                        "Status": 0,
                        "ExpireTime": {"$gt": now},
                    },
                    {"$set": {"Status": 1}},
                )

            logger.info(f"订单创建成功: Order_ID={order_id}")
            return int(order_id)

        except Exception as e:
            # 若已抢占座位但写入失败，需要归还
            if "took_seat" in locals() and took_seat:
                try:
                    release_seat(int(schedule_id))
                except Exception:
                    pass
            logger.error(f"创建订单失败: {str(e)}")
            raise

    @staticmethod
    def pay_order(order_id: int, channel: int = 1) -> int:
        try:
            order_id = InputValidator.validate_id(order_id, "订单ID")
            channel = InputValidator.validate_enum(channel, [1, 2, 3], "支付渠道")

            order = col("orders").find_one({"_id": int(order_id)})
            if not order:
                raise ValueError(f"订单 {order_id} 不存在")
            if int(order.get("Pay_Status") or 0) == OrderModel.STATUS_PAID:
                raise ValueError("订单已支付，无需重复支付")
            if int(order.get("Pay_Status") or 0) == OrderModel.STATUS_CANCELLED:
                raise ValueError("订单已取消，无法支付")

            trans_id = OrderModel._gen_id()
            now = datetime.now()

            col("orders").update_one({"_id": int(order_id)}, {"$set": {"Pay_Status": OrderModel.STATUS_PAID}})
            col("transactions").insert_one(
                {
                    "_id": int(trans_id),
                    "Trans_ID": int(trans_id),
                    "Order_ID": int(order_id),
                    "Amount": float(order.get("Amount") or 0),
                    "Trans_Type": 1,
                    "Channel": int(channel),
                    "Trans_Time": now,
                    "Result": 1,
                    # 报表过滤字段
                    "DM_ID": order.get("DM_ID"),
                    "Schedule_ID": order.get("Schedule_ID"),
                }
            )
            return int(trans_id)
        except Exception as e:
            logger.error(f"支付订单失败: {str(e)}")
            raise

    @staticmethod
    def cancel_order(order_id: int, player_id: int) -> bool:
        try:
            order_id = InputValidator.validate_id(order_id, "订单ID")
            player_id = InputValidator.validate_id(player_id, "玩家ID")

            order = col("orders").find_one({"_id": int(order_id)})
            if not order:
                raise ValueError("订单不存在")

            if int(order.get("Player_ID")) != int(player_id):
                raise ValueError("无权取消他人订单")

            if int(order.get("Pay_Status") or 0) != OrderModel.STATUS_UNPAID:
                raise ValueError("仅未支付订单可取消")

            col("orders").update_one({"_id": int(order_id)}, {"$set": {"Pay_Status": OrderModel.STATUS_CANCELLED}})
            release_seat(int(order.get("Schedule_ID")))
            return True
        except Exception as e:
            logger.error(f"取消订单失败: {str(e)}")
            raise

    @staticmethod
    def get_orders_by_player(player_id: int) -> List[dict]:
        try:
            player_id = InputValidator.validate_id(player_id, "玩家ID")
            orders = list(
                col("orders")
                .find({"Player_ID": int(player_id)}, {"_id": 0})
                .sort("Create_Time", -1)
            )
            return orders if orders else []
        except Exception as e:
            logger.error(f"查询玩家订单失败: {str(e)}")
            raise

    @staticmethod
    def get_all_orders(dm_id: Optional[int] = None) -> List[dict]:
        try:
            query = {}
            if dm_id is not None:
                query["DM_ID"] = int(dm_id)
            orders = list(col("orders").find(query, {"_id": 0}).sort("Create_Time", -1))
            return orders if orders else []
        except Exception as e:
            logger.error(f"查询订单列表失败: {str(e)}")
            raise

