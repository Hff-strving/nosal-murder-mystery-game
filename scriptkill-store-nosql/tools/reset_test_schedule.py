# -*- coding: utf-8 -*-
"""
重置测试场次数据（用于 JMeter 重复测试）
"""
import sys
import os
from datetime import datetime, timedelta
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nosql.redis_client import get_redis
from nosql.mongo import get_db

def reset_schedule(schedule_id, max_players=5):
    """重置指定场次的数据"""
    r = get_redis()
    db = get_db()

    # 兼容旧的“测试场次”格式：确保该场次文档符合系统主 schema（否则管理端列表会报错）
    sch = db.schedules.find_one({"_id": int(schedule_id)})
    if sch:
        updates = {"Schedule_ID": int(schedule_id), "Max_Players": int(max_players)}
        if "Start_Time" not in sch:
            raw = sch.get("Schedule_Time")
            if isinstance(raw, str):
                try:
                    start_time = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
                except Exception:
                    start_time = datetime.now() + timedelta(days=1)
            else:
                start_time = datetime.now() + timedelta(days=1)
            start_time = start_time.replace(minute=0, second=0, microsecond=0)
            updates["Start_Time"] = start_time
            updates["End_Time"] = start_time + timedelta(hours=4)
        if "Real_Price" not in sch:
            updates["Real_Price"] = float(sch.get("Price") or 100)
        # Status 统一为 int：0 正常；1 结束；2 取消
        try:
            updates["Status"] = int(sch.get("Status")) if isinstance(sch.get("Status"), int) else 0
        except Exception:
            updates["Status"] = 0

        # 反范式字段尽量补齐（用于前端展示/报表）
        script_id = int(sch.get("Script_ID") or 1001)
        room_id = int(sch.get("Room_ID") or 101)
        dm_id = int(sch.get("DM_ID") or 2001)
        script = db.scripts.find_one({"_id": script_id}) or {}
        room = db.rooms.find_one({"_id": room_id}) or {}
        dm = db.dms.find_one({"_id": dm_id}) or {}
        updates.update(
            {
                "Script_ID": script_id,
                "Room_ID": room_id,
                "DM_ID": dm_id,
                "Room_Name": room.get("Room_Name") or sch.get("Room_Name"),
                "DM_Name": dm.get("Name") or dm.get("DM_Name") or sch.get("DM_Name"),
                "Script_Title": script.get("Title") or sch.get("Script_Title"),
                "Script_Cover": script.get("Cover_Image") or sch.get("Script_Cover"),
            }
        )

        db.schedules.update_one({"_id": int(schedule_id)}, {"$set": updates, "$unset": {"Schedule_Time": "", "Booked_Players": "", "Price": ""}})

    # 重置 Redis 库存
    r.set(f'seats:{schedule_id}', max_players)

    # 删除所有锁位键
    lock_keys = r.keys(f'lock:{schedule_id}:*')
    for key in lock_keys:
        r.delete(key)

    # 删除 MongoDB 锁位记录
    result = db.lock_records.delete_many({'Schedule_ID': schedule_id})

    print(f'✅ 场次 {schedule_id} 数据已重置')
    print(f'   - Redis seats:{schedule_id} = {max_players}')
    print(f'   - 删除了 {len(lock_keys)} 个 Redis 锁位键')
    print(f'   - 删除了 {result.deleted_count} 条 MongoDB 锁位记录')
    print(f'\n现在可以重新运行 JMeter 测试了！')

if __name__ == '__main__':
    schedule_id = int(sys.argv[1]) if len(sys.argv) > 1 else 9001
    max_players = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    reset_schedule(schedule_id, max_players)
