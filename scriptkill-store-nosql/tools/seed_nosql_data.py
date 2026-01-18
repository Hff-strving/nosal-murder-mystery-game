# -*- coding: utf-8 -*-
"""
MongoDB + Redis 造数脚本（保证 >= 1000 条数据，并保持关联一致）

用法：
  python tools/seed_nosql_data.py --min-orders 1200

说明：
  - 若你已完成 MySQL 迁移，本脚本会在现有数据基础上补齐
  - 优先补充 orders/transactions（对报表与压测最有价值）
"""

from __future__ import annotations

import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import argparse
import random
from datetime import datetime, timedelta
from collections import defaultdict

from nosql.mongo import col, get_db, get_next_sequence, ensure_indexes
from nosql.redis_client import get_redis


def _ensure_basic_accounts():
    users = col("users")
    dms = col("dms")

    from models.auth_model import AuthModel

    # boss（保留中文 + 额外提供纯英文用户名，解决终端中文编码问题）
    boss = users.find_one({"Role": "boss"})
    if not boss:
        uid = get_next_sequence("User_ID", start=1)
        users.insert_one(
            {
                "_id": uid,
                "User_ID": uid,
                "Username": "郝飞帆",
                "Phone": f"186{random.randint(10000000, 99999999)}",
                "Password_Hash": AuthModel.hash_password("123456"),
                "Role": "boss",
                "Ref_ID": None,
                "Create_Time": datetime.now(),
                "Last_Login": None,
            }
        )

    if not users.find_one({"Username": "boss_demo"}):
        uid = get_next_sequence("User_ID", start=1)
        users.insert_one(
            {
                "_id": uid,
                "User_ID": uid,
                "Username": "boss_demo",
                "Phone": "19900000001",
                "Password_Hash": AuthModel.hash_password("123456"),
                "Role": "boss",
                "Ref_ID": None,
                "Create_Time": datetime.now(),
                "Last_Login": None,
            }
        )

    # 至少 1 个 DM + staff
    dm = dms.find_one({})
    if not dm:
        dm_id = get_next_sequence("DM_ID", start=2001)
        dms.insert_one({"_id": dm_id, "DM_ID": dm_id, "Name": "DM_张三", "Phone": "13800000001", "Star_Level": 5})

    dm = dms.find_one({})
    staff = users.find_one({"Role": "staff"})
    if not staff:
        uid = get_next_sequence("User_ID", start=1)
        users.insert_one(
            {
                "_id": uid,
                "User_ID": uid,
                "Username": "staff_demo",
                "Phone": "19800000001",
                "Password_Hash": AuthModel.hash_password("123456"),
                "Role": "staff",
                "Ref_ID": int(dm["DM_ID"]),
                "Create_Time": datetime.now(),
                "Last_Login": None,
            }
        )

    if not users.find_one({"Username": "staff_demo"}):
        uid = get_next_sequence("User_ID", start=1)
        users.insert_one(
            {
                "_id": uid,
                "User_ID": uid,
                "Username": "staff_demo",
                "Phone": "19800000001",
                "Password_Hash": AuthModel.hash_password("123456"),
                "Role": "staff",
                "Ref_ID": int(dm["DM_ID"]),
                "Create_Time": datetime.now(),
                "Last_Login": None,
            }
        )


def _ensure_rooms_scripts(min_scripts: int = 12, min_rooms: int = 3):
    rooms = col("rooms")
    scripts = col("scripts")

    # rooms
    if rooms.count_documents({}) < min_rooms:
        start = 1
        existing = set(r["Room_ID"] for r in rooms.find({}, {"Room_ID": 1}))
        for i in range(start, start + min_rooms * 2):
            if i in existing:
                continue
            rooms.insert_one({"_id": i, "Room_ID": i, "Room_Name": f"推理房{i}"})
            if rooms.count_documents({}) >= min_rooms:
                break

    # scripts
    while scripts.count_documents({}) < min_scripts:
        sid = get_next_sequence("Script_ID", start=1001)
        scripts.insert_one(
            {
                "_id": sid,
                "Script_ID": sid,
                "Title": f"剧本_{sid}",
                "Type": random.choice(["推理", "情感", "欢乐", "恐怖"]),
                "Min_Players": random.choice([4, 5, 6]),
                "Max_Players": random.choice([6, 7, 8]),
                "Duration": random.choice([180, 210, 240]),
                "Base_Price": float(random.choice([128, 148, 168, 188])),
                "Status": 1,
                "Cover_Image": "",
                "Group_Category": random.choice(["古风", "现代", "科幻", "悬疑"]),
                "Sub_Category": random.choice(["硬核", "还原", "阵营"]),
                "Difficulty": random.choice(["新手", "进阶", "硬核"]),
                "Gender_Config": random.choice(["不限", "3男3女", "4男3女", "4男4女"]),
                "Allow_Gender_Bend": 1,
                "Synopsis": "自动生成数据，用于压测与报表演示。",
            }
        )


def _ensure_schedules(min_future: int = 30, min_past: int = 80):
    schedules = col("schedules")
    scripts = list(col("scripts").find({"Status": 1}, {"Script_ID": 1, "Title": 1, "Max_Players": 1, "Cover_Image": 1}))
    rooms = list(col("rooms").find({}, {"Room_ID": 1, "Room_Name": 1}))
    dms = list(col("dms").find({}, {"DM_ID": 1, "Name": 1}))
    if not scripts or not rooms or not dms:
        return

    now = datetime.now()
    future_cnt = schedules.count_documents({"Start_Time": {"$gt": now}})
    past_cnt = schedules.count_documents({"Start_Time": {"$lte": now}})

    # 目标：历史数据尽量集中在 2024-2025，未来场次不超过 2026-03（避免“太假”）
    past_start = datetime(2024, 1, 1)
    past_end = datetime(2025, 12, 31, 23, 0, 0)
    hard_future_end = datetime(2026, 3, 15, 23, 0, 0)
    future_end = min(hard_future_end, now + timedelta(days=70))
    future_start = now + timedelta(days=1)

    def _new_schedule(start_time: datetime, script, room, dm):
        sid = get_next_sequence("Schedule_ID", start=4001)
        end_time = start_time + timedelta(hours=4)
        schedules.insert_one(
            {
                "_id": sid,
                "Schedule_ID": sid,
                "Script_ID": int(script["Script_ID"]),
                "Room_ID": int(room["Room_ID"]),
                "DM_ID": int(dm["DM_ID"]),
                "Start_Time": start_time,
                "End_Time": end_time,
                "Status": 0 if start_time > now else 1,
                "Real_Price": float(random.choice([128, 148, 168, 188])),
                "Room_Name": room["Room_Name"],
                "DM_Name": dm["Name"],
                "Script_Title": script["Title"],
                "Max_Players": int(script.get("Max_Players") or 6),
                "Script_Cover": script.get("Cover_Image") or "",
            }
        )

    # future schedules
    # 若当前时间已超过 hard_future_end，则只补历史场次，不再额外补未来
    if future_start < future_end:
        for i in range(max(0, min_future - future_cnt)):
            script = random.choice(scripts)
            room = random.choice(rooms)
            dm = random.choice(dms)
            span_days = max(1, (future_end - future_start).days)
            start_time = future_start + timedelta(days=random.randint(0, span_days), hours=random.choice([10, 14, 19]))
            start_time = start_time.replace(minute=0, second=0, microsecond=0)
            if start_time > future_end:
                start_time = future_end.replace(hour=random.choice([10, 14, 19]), minute=0, second=0, microsecond=0)
            _new_schedule(start_time, script, room, dm)

    # past schedules (2024-2025)
    for i in range(max(0, min_past - past_cnt)):
        script = random.choice(scripts)
        room = random.choice(rooms)
        dm = random.choice(dms)
        span_days = max(1, (past_end - past_start).days)
        start_time = past_start + timedelta(days=random.randint(0, span_days), hours=random.choice([10, 14, 19]))
        start_time = start_time.replace(minute=0, second=0, microsecond=0)
        _new_schedule(start_time, script, room, dm)


def _ensure_players(min_players: int = 120):
    players = col("players")
    users = col("users")
    from models.auth_model import AuthModel

    while players.count_documents({}) < min_players:
        player_id = get_next_sequence("Player_ID", start=3001)
        phone = f"137{random.randint(10000000, 99999999)}"
        players.insert_one({"_id": player_id, "Player_ID": player_id, "Open_ID": f"web_{player_id}", "Nickname": f"player_{player_id}", "Phone": phone, "Create_Time": datetime.now()})
        uid = get_next_sequence("User_ID", start=1)
        users.insert_one({"_id": uid, "User_ID": uid, "Username": f"player_{player_id}", "Phone": phone, "Password_Hash": AuthModel.hash_password("123456"), "Role": "player", "Ref_ID": player_id, "Create_Time": datetime.now(), "Last_Login": None})


def _seed_orders(min_orders: int = 1200):
    orders = col("orders")
    txs = col("transactions")
    schedules = list(col("schedules").find({}, {"Schedule_ID": 1, "Script_ID": 1, "Script_Title": 1, "Room_ID": 1, "Room_Name": 1, "DM_ID": 1, "DM_Name": 1, "Start_Time": 1, "Real_Price": 1}))
    players = list(col("players").find({}, {"Player_ID": 1}))
    if not schedules or not players:
        return

    # 避免高并发插入时 timestamp+random 冲突：用 counters 做自增
    max_order = orders.find_one(sort=[("_id", -1)], projection={"_id": 1})
    max_trans = txs.find_one(sort=[("_id", -1)], projection={"_id": 1})
    if max_order and max_order.get("_id"):
        col("counters").update_one({"_id": "Order_ID"}, {"$set": {"seq": int(max_order["_id"])}}, upsert=True)
    if max_trans and max_trans.get("_id"):
        col("counters").update_one({"_id": "Trans_ID"}, {"$set": {"seq": int(max_trans["_id"])}}, upsert=True)

    now = datetime.now()

    # 预计算每个场次的剩余可预约名额（只统计 Pay_Status 0/1）
    booked = defaultdict(int)
    for row in orders.aggregate(
        [
            {"$match": {"Pay_Status": {"$in": [0, 1]}}},
            {"$group": {"_id": "$Schedule_ID", "cnt": {"$sum": 1}}},
        ]
    ):
        booked[int(row["_id"])] = int(row["cnt"])

    sch_map = {int(s["Schedule_ID"]): s for s in schedules}

    def _max_players(schedule_id: int) -> int:
        sch = col("schedules").find_one({"_id": schedule_id}, {"Max_Players": 1})
        return int((sch or {}).get("Max_Players") or 0)

    # 为了把订单“前移到 2024-2025”，优先在历史场次填充订单
    past_sids = [sid for sid, s in sch_map.items() if s.get("Start_Time") and s["Start_Time"] <= datetime(2025, 12, 31, 23, 59, 59)]
    future_sids = [sid for sid, s in sch_map.items() if s.get("Start_Time") and s["Start_Time"] > datetime(2025, 12, 31, 23, 59, 59)]
    random.shuffle(past_sids)
    random.shuffle(future_sids)

    def _fill_schedule(schedule_id: int, want: int) -> int:
        sch = sch_map.get(schedule_id)
        if not sch:
            return 0

        cap = _max_players(schedule_id)
        current = booked.get(schedule_id, 0)
        remain = max(0, cap - current)
        if remain <= 0:
            return 0

        fill = min(remain, want)
        start_time = sch["Start_Time"]
        amount = float(sch.get("Real_Price") or random.choice([128, 148, 168, 188]))

        # 防止同一场次同一玩家重复订单：尽量用不重复玩家
        player_ids = [int(p["Player_ID"]) for p in players]
        if fill <= len(player_ids):
            chosen = random.sample(player_ids, fill)
        else:
            chosen = random.sample(player_ids, len(player_ids)) + [random.choice(player_ids) for _ in range(fill - len(player_ids))]

        inserted = 0
        min_create_time = datetime(2024, 1, 1)
        for pid in chosen:
            # 兜底：同一玩家同一场次已有有效订单就跳过
            if orders.find_one({"Schedule_ID": schedule_id, "Player_ID": pid, "Pay_Status": {"$in": [0, 1]}}, {"_id": 1}):
                continue

            order_id = get_next_sequence("Order_ID", start=2024010100000000)

            # 订单创建时间：历史场次 -> 开场前 1~14 天；未来场次 -> 最近 0~7 天
            if start_time <= now:
                create_time = start_time - timedelta(days=random.randint(1, 14), minutes=random.randint(1, 180))
            else:
                create_time = now - timedelta(days=random.randint(0, 7), minutes=random.randint(0, 180))
                if create_time > start_time:
                    create_time = start_time - timedelta(minutes=random.randint(10, 180))
            if create_time < min_create_time:
                create_time = min_create_time + timedelta(minutes=random.randint(0, 360))

            # 支付状态：历史更偏“已支付”，未来更偏“待支付”
            if start_time <= now:
                pay_status = random.choices([1, 0, 3], weights=[80, 15, 5])[0]
            else:
                pay_status = random.choices([0, 1, 3], weights=[60, 35, 5])[0]

            doc = {
                "_id": order_id,
                "Order_ID": order_id,
                "Player_ID": pid,
                "Schedule_ID": schedule_id,
                "Amount": amount,
                "Pay_Status": int(pay_status),
                "Create_Time": create_time,
                "Script_ID": sch.get("Script_ID"),
                "Script_Title": sch.get("Script_Title"),
                "Room_ID": sch.get("Room_ID"),
                "Room_Name": sch.get("Room_Name"),
                "DM_ID": sch.get("DM_ID"),
                "DM_Name": sch.get("DM_Name"),
                "Start_Time": start_time,
                "Seeded": True,
                "SeededAt": now,
            }
            try:
                orders.insert_one(doc)
            except Exception:
                continue

            inserted += 1
            if pay_status == 1:
                trans_id = get_next_sequence("Trans_ID", start=2024010100000000)
                txs.insert_one(
                    {
                        "_id": trans_id,
                        "Trans_ID": trans_id,
                        "Order_ID": order_id,
                        "Amount": amount,
                        "Trans_Type": 1,
                        "Channel": random.choice([1, 2, 3]),
                        "Trans_Time": create_time + timedelta(minutes=random.randint(1, 60)),
                        "Result": 1,
                        "DM_ID": sch.get("DM_ID"),
                        "Schedule_ID": schedule_id,
                        "Seeded": True,
                        "SeededAt": now,
                    }
                )

        booked[schedule_id] = booked.get(schedule_id, 0) + inserted
        return inserted

    # 若总体容量不足以达到 min_orders，则先补充更多历史场次
    def _total_capacity(sids) -> int:
        total = 0
        for sid in sids:
            total += max(0, _max_players(sid) - booked.get(sid, 0))
        return total

    need = max(0, int(min_orders) - int(orders.count_documents({})))

    # 优先填充历史场次
    for sid in past_sids:
        if need <= 0:
            break
        got = _fill_schedule(sid, want=need)
        need -= got

    # 再填充未来场次（少量即可）
    for sid in future_sids:
        if need <= 0:
            break
        got = _fill_schedule(sid, want=need)
        need -= got

    # 如果仍不够：动态补更多历史场次，再继续填（保证不会出现 booked > Max_Players）
    while need > 0:
        _ensure_schedules(min_future=0, min_past=col("schedules").count_documents({}) + 50)
        schedules = list(col("schedules").find({}, {"Schedule_ID": 1, "Script_ID": 1, "Script_Title": 1, "Room_ID": 1, "Room_Name": 1, "DM_ID": 1, "DM_Name": 1, "Start_Time": 1, "Real_Price": 1}))
        sch_map = {int(s["Schedule_ID"]): s for s in schedules}
        past_sids = [sid for sid, s in sch_map.items() if s.get("Start_Time") and s["Start_Time"] <= datetime(2025, 12, 31, 23, 59, 59)]
        random.shuffle(past_sids)
        for sid in past_sids:
            if need <= 0:
                break
            got = _fill_schedule(sid, want=need)
            need -= got
        # 兜底：避免死循环
        if _total_capacity(past_sids) <= 0:
            break


def _rebuild_seats():
    db = get_db()
    r = get_redis()
    now = datetime.now()
    for sch in db["schedules"].find({}, {"Schedule_ID": 1, "Max_Players": 1}):
        schedule_id = int(sch["Schedule_ID"])
        max_players = int(sch.get("Max_Players") or 0)
        booked = db["orders"].count_documents({"Schedule_ID": schedule_id, "Pay_Status": {"$in": [0, 1]}})
        locked = db["lock_records"].count_documents({"Schedule_ID": schedule_id, "Status": 0, "ExpireTime": {"$gt": now}})
        seats = max_players - int(booked) - int(locked)
        if seats < 0:
            seats = 0
        r.set(f"seats:{schedule_id}", seats)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-orders", type=int, default=1200)
    args = ap.parse_args()

    ensure_indexes()
    _ensure_basic_accounts()
    _ensure_rooms_scripts()
    _ensure_schedules()
    _ensure_players()
    _seed_orders(min_orders=args.min_orders)
    _rebuild_seats()

    db = get_db()
    print("[OK] seed done")
    print(f"  users={db['users'].count_documents({})}")
    print(f"  players={db['players'].count_documents({})}")
    print(f"  dms={db['dms'].count_documents({})}")
    print(f"  rooms={db['rooms'].count_documents({})}")
    print(f"  scripts={db['scripts'].count_documents({})}")
    print(f"  schedules={db['schedules'].count_documents({})}")
    print(f"  orders={db['orders'].count_documents({})}")
    print(f"  transactions={db['transactions'].count_documents({})}")
    print(f"  lock_records={db['lock_records'].count_documents({})}")


if __name__ == "__main__":
    main()
