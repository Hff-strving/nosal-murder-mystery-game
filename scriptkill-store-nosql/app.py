# -*- coding: utf-8 -*-
"""
Flask API 服务 - MongoDB + Redis 版本
目标：尽量不改前端接口与页面，完成从 MySQL 到 NoSQL 的替换。
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from functools import wraps

from flask import Flask, jsonify, request
from flask_cors import CORS

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from models.auth_model import AuthModel
from models.lock_model import LockModel
from models.order_model import OrderModel
from models.report_model import ReportModel
from models.schedule_model import ScheduleModel
from models.script_model import ScriptModel
from nosql.config import MONGO_DB_NAME
from nosql.json_utils import to_jsonable
from nosql.mongo import col, ensure_indexes, ping as mongo_ping
from nosql.redis_client import ping as redis_ping
from nosql.seat_lock_service import cleanup_expired_locks

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler("api.log", encoding="utf-8"), logging.StreamHandler()],
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)


def success_response(data=None, message="操作成功"):
    return jsonify({"code": 200, "message": message, "data": to_jsonable(data)})


def error_response(message="操作失败", code=400):
    return jsonify({"code": code, "message": message, "data": None}), code


def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get("Authorization")
        if not token:
            return error_response("缺少认证token", 401)
        try:
            if token.startswith("Bearer "):
                token = token[7:]
            payload = AuthModel.verify_token(token)
            request.current_user = payload
        except Exception as e:
            return error_response(str(e), 401)
        return f(*args, **kwargs)

    return decorated


def _require_staff_or_boss():
    role = request.current_user.get("role")
    if role not in ("staff", "boss"):
        return None, error_response("无权限访问", 403)
    return role, None


def _resolve_staff_dm_id(user_id: int):
    """
    staff 分域：优先 users.Ref_ID=DM_ID；若缺失则用 users.Phone 反查 dms.Phone。
    """
    try:
        user = col("users").find_one({"_id": int(user_id)}, {"Role": 1, "Ref_ID": 1, "Phone": 1})
        if user and user.get("Role") == "staff" and user.get("Ref_ID"):
            return int(user["Ref_ID"]), None
        if user and user.get("Phone"):
            dm = col("dms").find_one({"Phone": user["Phone"]}, {"DM_ID": 1})
            if dm and dm.get("DM_ID"):
                return int(dm["DM_ID"]), None
    except Exception:
        pass
    return None, error_response("员工账号未绑定DM（请配置 Ref_ID=DM_ID）", 403)


def _get_admin_scope_dm_id(role: str, user_id: int):
    if role == "staff":
        return _resolve_staff_dm_id(user_id)
    dm_id = request.args.get("dm_id", type=int)
    return dm_id, None


def _startup_init():
    try:
        if mongo_ping():
            ensure_indexes()
        else:
            logger.warning("MongoDB ping failed (is my-mongo running / port mapped?)")
        if not redis_ping():
            logger.warning("Redis ping failed (is my-redis running / port mapped?)")
    except Exception as e:
        logger.warning(f"startup init skipped: {e}")

    def _cleanup_worker():
        while True:
            try:
                cleanup_expired_locks(limit=200)
            except Exception as e:
                logger.warning(f"lock cleanup error: {e}")
            time.sleep(5)

    threading.Thread(target=_cleanup_worker, daemon=True).start()


_startup_init()


# ==================== 认证 ====================


@app.route("/api/auth/register", methods=["POST"])
def register():
    try:
        data = request.get_json() or {}
        if data.get("role") and data.get("role") != "player":
            return error_response("仅允许注册玩家账号", 403)
        user_id = AuthModel.register(data["username"], data["phone"], data["password"], "player")
        return success_response({"user_id": user_id}, "注册成功")
    except Exception as e:
        return error_response(str(e))


@app.route("/api/auth/login", methods=["POST"])
def login():
    try:
        data = request.get_json() or {}
        result = AuthModel.login(data["username"], data["password"])
        return success_response(result, "登录成功")
    except Exception as e:
        return error_response(str(e))


@app.route("/api/me", methods=["GET"])
@token_required
def me():
    try:
        user_id = request.current_user["user_id"]
        role = request.current_user.get("role")
        user = AuthModel.get_current_user_info(user_id, role)
        return success_response(user, "查询成功")
    except Exception as e:
        return error_response(str(e))


# ==================== 剧本/场次 ====================


@app.route("/api/scripts", methods=["GET"])
def get_scripts():
    try:
        status = request.args.get("status", type=int)
        scripts = ScriptModel.get_all_scripts(status)
        return success_response(scripts, "查询成功")
    except Exception as e:
        return error_response(str(e))


@app.route("/api/scripts/hot", methods=["GET"])
def get_hot_scripts():
    try:
        limit = request.args.get("limit", default=10, type=int)
        scripts = ScriptModel.get_hot_scripts(limit)
        return success_response(scripts, "查询成功")
    except Exception as e:
        return error_response(str(e))


@app.route("/api/scripts/<int:script_id>", methods=["GET"])
def get_script_detail(script_id: int):
    try:
        script = ScriptModel.get_script_by_id(script_id)
        return success_response(script, "查询成功")
    except Exception as e:
        return error_response(str(e))


@app.route("/api/scripts/<int:script_id>/schedules", methods=["GET"])
def get_schedules_by_script(script_id: int):
    try:
        player_id = request.args.get("player_id", type=int)
        schedules = ScheduleModel.get_schedules_by_script(script_id, player_id)
        return success_response(schedules, "查询成功")
    except Exception as e:
        return error_response(str(e))


# ==================== 订单 ====================


@app.route("/api/orders", methods=["POST"])
@token_required
def create_order():
    try:
        user_id = request.current_user["user_id"]
        user = AuthModel.get_user_role_ref(user_id)
        if user.get("Role") != "player":
            return error_response("只有玩家可以创建订单", 403)
        if not user.get("Ref_ID"):
            return error_response("用户信息不完整", 400)

        data = request.get_json() or {}
        schedule_id = data.get("schedule_id")
        if not schedule_id:
            return error_response("缺少场次ID", 400)
        order_id = OrderModel.create_order(int(user["Ref_ID"]), int(schedule_id))
        return success_response({"order_id": order_id}, "订单创建成功")
    except Exception as e:
        return error_response(str(e))


@app.route("/api/orders/<int:order_id>/pay", methods=["POST"])
@token_required
def pay_order(order_id: int):
    try:
        user_id = request.current_user["user_id"]
        user = AuthModel.get_user_role_ref(user_id)
        if user.get("Role") != "player":
            return error_response("只有玩家可以支付订单", 403)
        if not user.get("Ref_ID"):
            return error_response("用户信息不完整", 400)

        order = col("orders").find_one({"_id": int(order_id)}, {"Player_ID": 1})
        if not order:
            return error_response("订单不存在", 404)
        if int(order.get("Player_ID")) != int(user["Ref_ID"]):
            return error_response("无权支付他人订单", 403)

        data = request.get_json() or {}
        channel = data.get("channel", 1)
        trans_id = OrderModel.pay_order(order_id, channel)
        return success_response({"trans_id": trans_id}, "支付成功")
    except Exception as e:
        return error_response(str(e))


@app.route("/api/orders/<int:order_id>/cancel", methods=["POST"])
@token_required
def cancel_order(order_id: int):
    try:
        user_id = request.current_user["user_id"]
        user = AuthModel.get_user_role_ref(user_id)
        if user.get("Role") != "player":
            return error_response("只有玩家可以取消订单", 403)
        if not user.get("Ref_ID"):
            return error_response("用户信息不完整", 400)

        OrderModel.cancel_order(order_id, int(user["Ref_ID"]))
        return success_response(None, "订单已取消")
    except Exception as e:
        return error_response(str(e))


@app.route("/api/my/orders", methods=["GET"])
@token_required
def my_orders():
    try:
        user_id = request.current_user["user_id"]
        user = AuthModel.get_user_role_ref(user_id)
        if user.get("Role") != "player":
            return error_response("只有玩家可以查看订单", 403)
        if not user.get("Ref_ID"):
            return error_response("用户信息不完整", 400)
        orders = OrderModel.get_orders_by_player(int(user["Ref_ID"]))
        return success_response(orders, "查询成功")
    except Exception as e:
        return error_response(str(e))


@app.route("/api/players/<int:player_id>/orders", methods=["GET"])
def get_player_orders(player_id: int):
    try:
        orders = OrderModel.get_orders_by_player(player_id)
        return success_response(orders, "查询成功")
    except Exception as e:
        return error_response(str(e))


@app.route("/api/admin/orders", methods=["GET"])
@token_required
def admin_orders():
    try:
        user_id = request.current_user["user_id"]
        role, err = _require_staff_or_boss()
        if err:
            return err
        dm_id, err = _get_admin_scope_dm_id(role, user_id)
        if err:
            return err
        orders = OrderModel.get_all_orders(dm_id=dm_id)
        return success_response(orders, "查询成功")
    except Exception as e:
        return error_response(str(e))


# ==================== 锁位 ====================


@app.route("/api/locks", methods=["POST"])
@token_required
def create_lock():
    try:
        user_id = request.current_user["user_id"]
        user = AuthModel.get_user_role_ref(user_id)
        if user.get("Role") != "player":
            return error_response("只有玩家可以锁位", 403)
        if not user.get("Ref_ID"):
            return error_response("用户信息不完整", 400)

        data = request.get_json() or {}
        schedule_id = data.get("schedule_id")
        if not schedule_id:
            return error_response("缺少场次ID", 400)

        lock_id = LockModel.create_lock(int(user["Ref_ID"]), int(schedule_id))
        return success_response({"lock_id": lock_id}, "锁位成功")
    except Exception as e:
        return error_response(str(e))


@app.route("/api/locks/<int:lock_id>/cancel", methods=["POST"])
@token_required
def cancel_lock(lock_id: int):
    try:
        user_id = request.current_user["user_id"]
        user = AuthModel.get_user_role_ref(user_id)
        if user.get("Role") != "player":
            return error_response("只有玩家可以取消锁位", 403)
        if not user.get("Ref_ID"):
            return error_response("用户信息不完整", 400)
        LockModel.cancel_lock(lock_id, int(user["Ref_ID"]))
        return success_response(None, "取消成功")
    except Exception as e:
        return error_response(str(e))


@app.route("/api/my/locks", methods=["GET"])
@token_required
def my_locks():
    try:
        user_id = request.current_user["user_id"]
        user = AuthModel.get_user_role_ref(user_id)
        if user.get("Role") != "player":
            return error_response("只有玩家可以查看锁位", 403)
        if not user.get("Ref_ID"):
            return error_response("用户信息不完整", 400)
        locks = LockModel.get_locks_by_player(int(user["Ref_ID"]))
        return success_response(locks, "查询成功")
    except Exception as e:
        return error_response(str(e))


@app.route("/api/admin/locks", methods=["GET"])
@token_required
def admin_locks():
    try:
        user_id = request.current_user["user_id"]
        role, err = _require_staff_or_boss()
        if err:
            return err
        dm_id, err = _get_admin_scope_dm_id(role, user_id)
        if err:
            return err
        locks = LockModel.get_all_locks(dm_id=dm_id)
        return success_response(locks, "查询成功")
    except Exception as e:
        return error_response(str(e))


# ==================== 管理端：场次 ====================


@app.route("/api/admin/schedules", methods=["GET"])
@token_required
def admin_schedules():
    try:
        user_id = request.current_user["user_id"]
        role, err = _require_staff_or_boss()
        if err:
            return err
        dm_id, err = _get_admin_scope_dm_id(role, user_id)
        if err:
            return err

        date = request.args.get("date")
        room_id = request.args.get("room_id", type=int)
        script_id = request.args.get("script_id", type=int)
        status = request.args.get("status", type=int)
        schedules = ScheduleModel.get_all_schedules(date, room_id, script_id, status, dm_id=dm_id)
        return success_response(schedules, "查询成功")
    except Exception as e:
        return error_response(str(e))


@app.route("/api/admin/schedules", methods=["POST"])
@token_required
def admin_create_schedule():
    try:
        user_id = request.current_user["user_id"]
        role, err = _require_staff_or_boss()
        if err:
            return err
        dm_id, err = _get_admin_scope_dm_id(role, user_id)
        if err:
            return err

        data = request.get_json() or {}
        if role == "staff":
            data = dict(data)
            data["dm_id"] = dm_id

        schedule_id = ScheduleModel.create_schedule(
            data["script_id"], data["room_id"], data["dm_id"], data["start_time"], data["end_time"], data["real_price"]
        )
        return success_response({"schedule_id": schedule_id}, "场次创建成功")
    except Exception as e:
        return error_response(str(e))


@app.route("/api/admin/schedules/<int:schedule_id>", methods=["PUT"])
@token_required
def admin_update_schedule(schedule_id: int):
    try:
        user_id = request.current_user["user_id"]
        role, err = _require_staff_or_boss()
        if err:
            return err
        dm_id, err = _get_admin_scope_dm_id(role, user_id)
        if err:
            return err

        data = request.get_json() or {}
        if role == "staff":
            sch = ScheduleModel.get_schedule_basic(schedule_id)
            if int(sch.get("DM_ID")) != int(dm_id):
                return error_response("无权限更新其他员工的场次", 403)
            data = dict(data)
            data["dm_id"] = dm_id

        ScheduleModel.update_schedule(
            schedule_id,
            script_id=data.get("script_id"),
            room_id=data.get("room_id"),
            dm_id=data.get("dm_id"),
            start_time=data.get("start_time"),
            end_time=data.get("end_time"),
            real_price=data.get("real_price"),
            status=data.get("status"),
        )
        return success_response(None, "场次更新成功")
    except Exception as e:
        return error_response(str(e))


@app.route("/api/admin/schedules/<int:schedule_id>/cancel", methods=["POST"])
@token_required
def admin_cancel_schedule(schedule_id: int):
    try:
        user_id = request.current_user["user_id"]
        role, err = _require_staff_or_boss()
        if err:
            return err
        dm_id, err = _get_admin_scope_dm_id(role, user_id)
        if err:
            return err

        if role == "staff":
            sch = ScheduleModel.get_schedule_basic(schedule_id)
            if int(sch.get("DM_ID")) != int(dm_id):
                return error_response("无权限取消其他员工的场次", 403)

        ScheduleModel.cancel_schedule(schedule_id)
        return success_response(None, "场次已取消")
    except Exception as e:
        return error_response(str(e))


# ==================== 管理端：下拉/自检 ====================


@app.route("/api/admin/dms", methods=["GET"])
@token_required
def admin_dms():
    try:
        if request.current_user.get("role") != "boss":
            return error_response("只有老板可以查看DM列表", 403)
        dms = list(col("dms").find({}, {"_id": 0, "DM_ID": 1, "Name": 1, "Phone": 1, "Star_Level": 1}).sort("DM_ID", 1))
        return success_response(dms, "查询成功")
    except Exception as e:
        return error_response(str(e))


@app.route("/api/admin/rooms", methods=["GET"])
@token_required
def admin_rooms():
    try:
        _, err = _require_staff_or_boss()
        if err:
            return err
        rooms = list(col("rooms").find({}, {"_id": 0, "Room_ID": 1, "Room_Name": 1}).sort("Room_ID", 1))
        return success_response(rooms, "查询成功")
    except Exception as e:
        return error_response(str(e))


@app.route("/api/admin/db-objects", methods=["GET"])
@token_required
def admin_db_objects():
    try:
        _, err = _require_staff_or_boss()
        if err:
            return err

        # Mongo 索引自检（ensure_indexes 内创建的索引名）
        def _has_index(collection: str, index_name: str) -> bool:
            try:
                info = col(collection).index_information()
                return index_name in info
            except Exception:
                return False

        indexes = {
            "users.uk_users_username": _has_index("users", "uk_users_username"),
            "users.uk_users_phone": _has_index("users", "uk_users_phone"),
            "scripts.idx_scripts_status": _has_index("scripts", "idx_scripts_status"),
            "schedules.idx_sch_script_start": _has_index("schedules", "idx_sch_script_start"),
            "orders.idx_orders_player_time": _has_index("orders", "idx_orders_player_time"),
            "lock_records.idx_locks_expire": _has_index("lock_records", "idx_locks_expire"),
        }

        objects = {
            # NoSQL 版本没有 MySQL 触发器/视图/存储过程，这里用“等价能力点”映射给前端展示
            "triggers": {
                "redis_lua_lock": True,
                "redis_lua_take_seat": True,
            },
            "views": {
                "orders_denormalized_fields": True,
                "lock_records_denormalized_fields": True,
            },
            "procedures": {
                "agg_top_scripts": True,
                "agg_room_utilization": True,
            },
            "functions": {
                "calc_occupancy_rate": True,
            },
            "events": {
                "lock_expire_cleanup_thread": True,
            },
            "indexes": indexes,
            "role_enum": "player/staff/boss (application-level)",
        }
        return success_response({"schema": MONGO_DB_NAME, "current_role": request.current_user.get("role"), "objects": objects}, "查询成功")
    except Exception as e:
        return error_response(str(e))


# ==================== 管理端：报表 ====================


@app.route("/api/admin/dashboard", methods=["GET"])
@token_required
def admin_dashboard():
    try:
        user_id = request.current_user["user_id"]
        role, err = _require_staff_or_boss()
        if err:
            return err
        dm_id, err = _get_admin_scope_dm_id(role, user_id)
        if err:
            return err
        stats = ReportModel.get_dashboard_stats(dm_id=dm_id)
        return success_response(stats, "查询成功")
    except Exception as e:
        return error_response(str(e))


@app.route("/api/admin/reports/top-scripts", methods=["GET"])
@token_required
def admin_report_top_scripts():
    try:
        user_id = request.current_user["user_id"]
        role, err = _require_staff_or_boss()
        if err:
            return err
        dm_id, err = _get_admin_scope_dm_id(role, user_id)
        if err:
            return err
        start_date = request.args.get("start")
        end_date = request.args.get("end")
        limit = request.args.get("limit", default=5, type=int)
        rows = ReportModel.get_top_scripts(start_date, end_date, limit, dm_id=dm_id)
        return success_response(rows, "查询成功")
    except Exception as e:
        return error_response(str(e))


@app.route("/api/admin/reports/room-utilization", methods=["GET"])
@token_required
def admin_report_room_utilization():
    try:
        user_id = request.current_user["user_id"]
        role, err = _require_staff_or_boss()
        if err:
            return err
        dm_id, err = _get_admin_scope_dm_id(role, user_id)
        if err:
            return err
        start_date = request.args.get("start")
        end_date = request.args.get("end")
        rows = ReportModel.get_room_utilization(start_date, end_date, dm_id=dm_id)
        return success_response(rows, "查询成功")
    except Exception as e:
        return error_response(str(e))


@app.route("/api/admin/reports/lock-conversion", methods=["GET"])
@token_required
def admin_report_lock_conversion():
    try:
        user_id = request.current_user["user_id"]
        role, err = _require_staff_or_boss()
        if err:
            return err
        dm_id, err = _get_admin_scope_dm_id(role, user_id)
        if err:
            return err
        start_date = request.args.get("start")
        end_date = request.args.get("end")
        row = ReportModel.get_lock_conversion_rate(start_date, end_date, dm_id=dm_id)
        return success_response(row, "查询成功")
    except Exception as e:
        return error_response(str(e))


@app.route("/api/admin/reports/dm-performance", methods=["GET"])
@token_required
def admin_report_dm_performance():
    try:
        if request.current_user.get("role") != "boss":
            return error_response("只有老板可以查看DM业绩", 403)
        start_date = request.args.get("start")
        end_date = request.args.get("end")
        rows = ReportModel.get_dm_performance(start_date, end_date)
        return success_response(rows, "查询成功")
    except Exception as e:
        return error_response(str(e))


if __name__ == "__main__":
    logger.info("启动 Flask API 服务（MongoDB + Redis）...")
    app.run(host="0.0.0.0", port=5000, debug=False)

