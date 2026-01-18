# -*- coding: utf-8 -*-
"""
用户认证模型 - MongoDB 版本
说明：
- 保持原有 API 入参与返回结构，尽量不影响前端页面与路由。
- staff/boss/player 都在 users 集合中统一存储。
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, timedelta

import jwt
from werkzeug.security import check_password_hash

from nosql.mongo import col, get_next_sequence, project
from security_utils import InputValidator

logger = logging.getLogger(__name__)

# JWT 配置
JWT_SECRET = "your-secret-key-change-in-production"
JWT_ALGORITHM = "HS256"
JWT_EXPIRATION_HOURS = 24


class AuthModel:
    @staticmethod
    def hash_password(password: str) -> str:
        salt = secrets.token_hex(16)
        pwd_hash = hashlib.sha256((password + salt).encode()).hexdigest()
        return f"{salt}${pwd_hash}"

    @staticmethod
    def verify_password(password: str, stored_hash: str) -> bool:
        try:
            if isinstance(stored_hash, str) and stored_hash.startswith(("pbkdf2:", "scrypt:")):
                return check_password_hash(stored_hash, password)
            if isinstance(stored_hash, str) and "$" in stored_hash:
                salt, pwd_hash = stored_hash.split("$", 1)
                check_hash = hashlib.sha256((password + salt).encode()).hexdigest()
                return check_hash == pwd_hash
            # 兼容：历史 staff 账号可能为明文
            return password == stored_hash
        except Exception:
            return False

    @staticmethod
    def generate_token(user_id: int, role: str) -> str:
        payload = {
            "user_id": int(user_id),
            "role": role,
            "exp": datetime.utcnow() + timedelta(hours=JWT_EXPIRATION_HOURS),
        }
        return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

    @staticmethod
    def verify_token(token: str) -> dict:
        try:
            payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
            return payload
        except jwt.ExpiredSignatureError:
            raise ValueError("Token已过期")
        except jwt.InvalidTokenError:
            raise ValueError("无效的Token")

    @staticmethod
    def register(username: str, phone: str, password: str, role: str = "player") -> int:
        username = InputValidator.validate_string(username, "用户名", min_length=3, max_length=50)
        phone = InputValidator.validate_phone(phone)
        if len(password) < 6:
            raise ValueError("密码长度不能少于6位")
        if role not in ("player", "staff", "boss"):
            raise ValueError("角色不合法")

        users = col("users")

        # 唯一性（索引兜底 + 业务提示）
        exists = users.find_one({"$or": [{"Username": username}, {"Phone": phone}]}, {"_id": 1})
        if exists:
            raise ValueError("用户名或手机号已存在")

        password_hash = AuthModel.hash_password(password)

        ref_id = None
        if role == "player":
            players = col("players")
            player_id = get_next_sequence("Player_ID", start=3001)
            players.insert_one(
                {
                    "_id": player_id,
                    "Player_ID": player_id,
                    "Open_ID": f"web_{secrets.token_hex(8)}",
                    "Nickname": username,
                    "Phone": phone,
                    "Create_Time": datetime.now(),
                }
            )
            ref_id = player_id

        user_id = get_next_sequence("User_ID", start=1)
        users.insert_one(
            {
                "_id": user_id,
                "User_ID": user_id,
                "Username": username,
                "Phone": phone,
                "Password_Hash": password_hash,
                "Role": role,
                "Ref_ID": ref_id,
                "Create_Time": datetime.now(),
                "Last_Login": None,
            }
        )
        return int(user_id)

    @staticmethod
    def login(username: str, password: str) -> dict:
        username = InputValidator.validate_string(username, "用户名", min_length=1, max_length=50)
        if len(password) < 1:
            raise ValueError("密码不能为空")

        users = col("users")
        user = users.find_one({"$or": [{"Username": username}, {"Phone": username}]})
        if not user:
            raise ValueError("用户名或密码错误")

        if not AuthModel.verify_password(password, user.get("Password_Hash") or ""):
            raise ValueError("用户名或密码错误")

        users.update_one({"_id": user["_id"]}, {"$set": {"Last_Login": datetime.now()}})

        token = AuthModel.generate_token(int(user["User_ID"]), str(user["Role"]))
        return {
            "user_id": int(user["User_ID"]),
            "username": user["Username"],
            "role": user["Role"],
            "ref_id": user.get("Ref_ID"),
            "token": token,
        }

    @staticmethod
    def get_current_user_info(user_id: int, role: str | None = None) -> dict:
        user_id = InputValidator.validate_id(user_id, "用户ID")
        user = col("users").find_one({"_id": int(user_id)})
        if not user:
            raise ValueError("用户不存在")

        if role and user.get("Role") != role:
            # 仅用于前端展示，不做强校验阻断
            pass

        return {
            "user_id": int(user["User_ID"]),
            "username": user["Username"],
            "role": user["Role"],
            "ref_id": user.get("Ref_ID"),
            "phone": user.get("Phone"),
        }

    @staticmethod
    def get_user_role_ref(user_id: int) -> dict:
        user_id = InputValidator.validate_id(user_id, "用户ID")
        user = col("users").find_one({"_id": int(user_id)}, {"Role": 1, "Ref_ID": 1, "User_ID": 1})
        if not user:
            raise ValueError("用户不存在")
        return {"Role": user.get("Role"), "Ref_ID": user.get("Ref_ID")}

