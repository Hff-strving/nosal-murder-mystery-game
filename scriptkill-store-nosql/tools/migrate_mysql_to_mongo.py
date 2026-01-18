# -*- coding: utf-8 -*-
"""
MySQL -> MongoDB 迁移脚本（并初始化 Redis seats/lock 相关键）

用法：
  python tools/migrate_mysql_to_mongo.py --drop

说明：
  - MySQL 连接参数可通过命令行参数传入
  - MongoDB/Redis 使用 nosql.config（可用环境变量覆盖）
"""

from __future__ import annotations

import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import argparse
from datetime import datetime

import pymysql
from pymysql.cursors import DictCursor
from decimal import Decimal

from nosql.config import MONGO_DB_NAME
from nosql.mongo import get_db, ensure_indexes, get_next_sequence
from nosql.redis_client import get_redis


def _mysql_conn(host: str, port: int, user: str, password: str, database: str, charset: str):
    return pymysql.connect(
        host=host,
        port=int(port),
        user=user,
        password=password,
        database=database,
        charset=charset or "utf8mb4",
        cursorclass=DictCursor,
        autocommit=True,
    )


def _fetch_all(cursor, sql: str, params=None):
    cursor.execute(sql, params or ())
    return cursor.fetchall()


def _normalize_value(v):
    if isinstance(v, Decimal):
        return float(v)
    return v


def _normalize_docs(docs):
    for d in docs:
        for k in list(d.keys()):
            d[k] = _normalize_value(d[k])


def _upsert_many(collection, docs, id_field: str):
    if not docs:
        return 0
    for d in docs:
        d["_id"] = int(d[id_field])
    collection.insert_many(docs, ordered=False)
    return len(docs)


def _reset_nosql(db, flush_redis: bool):
    db.client.drop_database(db.name)
    if flush_redis:
        r = get_redis()
        for pattern in ("seats:*", "lock:*"):
            for k in r.scan_iter(match=pattern, count=1000):
                r.delete(k)
        r.delete("locks:exp")
        r.delete("lock:id")


def _set_counter(db, name: str, max_value: int):
    db["counters"].update_one({"_id": name}, {"$set": {"seq": int(max_value), "updated_at": datetime.utcnow()}}, upsert=True)


def _init_seats_and_lock_id(db):
    r = get_redis()
    now = datetime.now()

    # 初始化 seats:{schedule_id}
    schedules = list(db["schedules"].find({}, {"Schedule_ID": 1, "Max_Players": 1}))
    for s in schedules:
        schedule_id = int(s["Schedule_ID"])
        max_players = int(s.get("Max_Players") or 0)
        booked = db["orders"].count_documents({"Schedule_ID": schedule_id, "Pay_Status": {"$in": [0, 1]}})
        locked = db["lock_records"].count_documents({"Schedule_ID": schedule_id, "Status": 0, "ExpireTime": {"$gt": now}})
        seats = max_players - int(booked) - int(locked)
        if seats < 0:
            seats = 0
        r.set(f"seats:{schedule_id}", seats)

    # 初始化 lock:id（用于 Redis INCR 生成 LockID）
    max_lock = db["lock_records"].find_one(sort=[("LockID", -1)], projection={"LockID": 1})
    if max_lock and max_lock.get("LockID"):
        r.set("lock:id", int(max_lock["LockID"]))


def _sanitize_overbooked_orders(db):
    """
    MySQL 演示数据里可能存在“同一场次订单数 > Max_Players”的情况。
    为避免前端出现 (16/5) 这类不合理展示，这里把超出的订单标记为已取消（Pay_Status=3），
    并把对应交易流水 Result 置为 0（避免报表把它计入营收）。
    """
    now = datetime.now()
    overfixed = 0
    for sch in db["schedules"].find({}, {"Schedule_ID": 1, "Max_Players": 1}):
        schedule_id = int(sch["Schedule_ID"])
        max_players = int(sch.get("Max_Players") or 0)
        if max_players <= 0:
            continue

        active = list(
            db["orders"]
            .find({"Schedule_ID": schedule_id, "Pay_Status": {"$in": [0, 1]}}, {"Order_ID": 1, "Pay_Status": 1, "Create_Time": 1})
            .sort([("Pay_Status", -1), ("Create_Time", 1)])
        )
        if len(active) <= max_players:
            continue

        extras = active[max_players:]
        extra_ids = [int(o["Order_ID"]) for o in extras]
        if not extra_ids:
            continue

        db["orders"].update_many(
            {"Order_ID": {"$in": extra_ids}},
            {"$set": {"Pay_Status": 3, "Sanitized": True, "SanitizedAt": now}},
        )
        db["transactions"].update_many(
            {"Order_ID": {"$in": extra_ids}},
            {"$set": {"Result": 0, "Sanitized": True, "SanitizedAt": now}},
        )
        overfixed += len(extra_ids)

    return overfixed


def _ensure_ascii_demo_accounts(db):
    """
    为 Windows 终端中文编码不稳定提供“纯英文用户名”备用账号。
    - boss_demo / 123456
    - staff_demo / 123456 （绑定第一个 DM）
    """
    from models.auth_model import AuthModel

    users = db["users"]
    dms = db["dms"]

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

    dm = dms.find_one({}, {"DM_ID": 1})
    if dm and not users.find_one({"Username": "staff_demo"}):
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--drop", action="store_true", help="清空 MongoDB/Redis（仅本项目相关键）后再迁移")
    ap.add_argument("--no-flush-redis", action="store_true", help="drop 时不清理 Redis 键")
    ap.add_argument("--mysql-host", default="localhost")
    ap.add_argument("--mysql-port", type=int, default=3306)
    ap.add_argument("--mysql-user", default="root")
    ap.add_argument("--mysql-password", default="123456")
    ap.add_argument("--mysql-db", default="剧本杀店务管理系统")
    ap.add_argument("--mysql-charset", default="utf8mb4")
    args = ap.parse_args()

    db = get_db()
    if args.drop:
        _reset_nosql(db, flush_redis=(not args.no_flush_redis))

    ensure_indexes()

    with _mysql_conn(
        host=args.mysql_host,
        port=args.mysql_port,
        user=args.mysql_user,
        password=args.mysql_password,
        database=args.mysql_db,
        charset=args.mysql_charset,
    ) as conn:
        cur = conn.cursor()

        # users / players / dms / rooms
        users = _fetch_all(cur, "SELECT * FROM t_user")
        players = _fetch_all(cur, "SELECT * FROM t_player")
        dms = _fetch_all(cur, "SELECT * FROM t_dm")
        rooms = _fetch_all(cur, "SELECT * FROM t_room")
        _normalize_docs(users)
        _normalize_docs(players)
        _normalize_docs(dms)
        _normalize_docs(rooms)

        # scripts（合并 profile）
        scripts = _fetch_all(
            cur,
            """
            SELECT
              s.Script_ID, s.Title, s.Type, s.Min_Players, s.Max_Players,
              s.Duration, s.Base_Price, s.Status, s.Cover_Image,
              p.Group_Category, p.Sub_Category, p.Difficulty,
              p.Duration_Min_Minutes, p.Duration_Max_Minutes,
              p.Gender_Config, p.Allow_Gender_Bend, p.Synopsis
            FROM t_script s
            LEFT JOIN t_script_profile p ON s.Script_ID = p.Script_ID
            """,
        )
        _normalize_docs(scripts)

        # schedules（反范式）
        schedules = _fetch_all(
            cur,
            """
            SELECT
              sch.Schedule_ID, sch.Script_ID, sch.Room_ID, sch.DM_ID,
              sch.Start_Time, sch.End_Time, sch.Status, sch.Real_Price,
              r.Room_Name,
              d.Name AS DM_Name,
              sc.Title AS Script_Title,
              sc.Max_Players,
              sc.Cover_Image AS Script_Cover
            FROM t_schedule sch
            JOIN t_room r ON sch.Room_ID = r.Room_ID
            JOIN t_dm d ON sch.DM_ID = d.DM_ID
            JOIN t_script sc ON sch.Script_ID = sc.Script_ID
            """,
        )
        _normalize_docs(schedules)

        orders = _fetch_all(
            cur,
            """
            SELECT
              o.Order_ID, o.Player_ID, o.Schedule_ID, o.Amount, o.Pay_Status, o.Create_Time,
              sch.Script_ID, sch.Room_ID, sch.DM_ID, sch.Start_Time,
              sch.Real_Price,
              r.Room_Name,
              d.Name AS DM_Name,
              sc.Title AS Script_Title
            FROM t_order o
            JOIN t_schedule sch ON o.Schedule_ID = sch.Schedule_ID
            JOIN t_room r ON sch.Room_ID = r.Room_ID
            JOIN t_dm d ON sch.DM_ID = d.DM_ID
            JOIN t_script sc ON sch.Script_ID = sc.Script_ID
            """,
        )
        _normalize_docs(orders)

        txs = _fetch_all(
            cur,
            """
            SELECT t.*
            FROM t_transaction t
            """,
        )
        _normalize_docs(txs)

        locks = _fetch_all(
            cur,
            """
            SELECT
              l.LockID, l.Schedule_ID, l.Player_ID, l.LockTime, l.ExpireTime, l.Status,
              sch.Script_ID, sch.Room_ID, sch.DM_ID, sch.Start_Time,
              r.Room_Name,
              d.Name AS DM_Name,
              sc.Title AS Script_Title
            FROM t_lock_record l
            JOIN t_schedule sch ON l.Schedule_ID = sch.Schedule_ID
            JOIN t_room r ON sch.Room_ID = r.Room_ID
            JOIN t_dm d ON sch.DM_ID = d.DM_ID
            JOIN t_script sc ON sch.Script_ID = sc.Script_ID
            """,
        )
        _normalize_docs(locks)

    # 写入 Mongo
    if users:
        for u in users:
            u["_id"] = int(u["User_ID"])
        db["users"].insert_many(users, ordered=False)
    if players:
        for p in players:
            p["_id"] = int(p["Player_ID"])
        db["players"].insert_many(players, ordered=False)
    if dms:
        for d in dms:
            d["_id"] = int(d["DM_ID"])
        db["dms"].insert_many(dms, ordered=False)
    if rooms:
        for r in rooms:
            r["_id"] = int(r["Room_ID"])
        db["rooms"].insert_many(rooms, ordered=False)

    if scripts:
        for s in scripts:
            s["_id"] = int(s["Script_ID"])
        db["scripts"].insert_many(scripts, ordered=False)

    if schedules:
        for s in schedules:
            s["_id"] = int(s["Schedule_ID"])
        db["schedules"].insert_many(schedules, ordered=False)

    if orders:
        for o in orders:
            o["_id"] = int(o["Order_ID"])
        db["orders"].insert_many(orders, ordered=False)

    if txs:
        # 补足 DM_ID / Schedule_ID 便于报表过滤
        order_map = {int(o["Order_ID"]): o for o in orders} if orders else {}
        for t in txs:
            t["_id"] = int(t["Trans_ID"])
            o = order_map.get(int(t.get("Order_ID") or 0))
            if o:
                t["DM_ID"] = o.get("DM_ID")
                t["Schedule_ID"] = o.get("Schedule_ID")
        db["transactions"].insert_many(txs, ordered=False)

    if locks:
        for l in locks:
            l["_id"] = int(l["LockID"])
            # 为 lock 列表统一字段名（与前端展示一致）
            l["Script_Title"] = l.get("Script_Title")
            l["DM_Name"] = l.get("DM_Name")
        db["lock_records"].insert_many(locks, ordered=False)

    # counters（避免后续自增冲突）
    if users:
        _set_counter(db, "User_ID", max(int(u["User_ID"]) for u in users))
    if players:
        _set_counter(db, "Player_ID", max(int(p["Player_ID"]) for p in players))
    if schedules:
        _set_counter(db, "Schedule_ID", max(int(s["Schedule_ID"]) for s in schedules))
    if scripts:
        _set_counter(db, "Script_ID", max(int(s["Script_ID"]) for s in scripts))
    if dms:
        _set_counter(db, "DM_ID", max(int(d["DM_ID"]) for d in dms))
    if rooms:
        _set_counter(db, "Room_ID", max(int(r["Room_ID"]) for r in rooms))
    if orders:
        _set_counter(db, "Order_ID", max(int(o["Order_ID"]) for o in orders))
    if txs:
        _set_counter(db, "Trans_ID", max(int(t["Trans_ID"]) for t in txs))
    if locks:
        _set_counter(db, "LockID", max(int(l["LockID"]) for l in locks))

    fixed = _sanitize_overbooked_orders(db)
    if fixed:
        print(f"[OK] sanitized overbooked orders: {fixed}")

    _ensure_ascii_demo_accounts(db)

    _init_seats_and_lock_id(db)

    print(f"[OK] migrated to MongoDB db={MONGO_DB_NAME}")
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
    try:
        main()
    except Exception as e:
        print(f"[FAIL] {e}", file=sys.stderr)
        raise
