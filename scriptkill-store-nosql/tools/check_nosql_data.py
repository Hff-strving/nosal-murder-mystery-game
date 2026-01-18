# -*- coding: utf-8 -*-
"""
检查 MongoDB 数据一致性（场次人数/时间范围）

用法：
  python tools/check_nosql_data.py
"""

from __future__ import annotations

import os
import sys
from datetime import datetime

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nosql.mongo import get_db


def main():
    db = get_db()
    now = datetime.now()

    min_s = db["schedules"].find_one(sort=[("Start_Time", 1)], projection={"Start_Time": 1})
    max_s = db["schedules"].find_one(sort=[("Start_Time", -1)], projection={"Start_Time": 1})
    min_o = db["orders"].find_one(sort=[("Create_Time", 1)], projection={"Create_Time": 1})
    max_o = db["orders"].find_one(sort=[("Create_Time", -1)], projection={"Create_Time": 1})

    overfull = []
    for row in db["orders"].aggregate(
        [
            {"$match": {"Pay_Status": {"$in": [0, 1]}}},
            {"$group": {"_id": "$Schedule_ID", "booked": {"$sum": 1}}},
        ]
    ):
        sid = int(row["_id"])
        booked = int(row["booked"])
        sch = db["schedules"].find_one({"_id": sid}, {"Max_Players": 1, "Script_Title": 1, "Start_Time": 1})
        if not sch:
            continue
        cap = int(sch.get("Max_Players") or 0)
        if cap > 0 and booked > cap:
            overfull.append((sid, booked, cap, sch.get("Start_Time"), sch.get("Script_Title")))

    print("now =", now)
    print("schedule start range:", (min_s or {}).get("Start_Time"), "->", (max_s or {}).get("Start_Time"))
    print("order create range:", (min_o or {}).get("Create_Time"), "->", (max_o or {}).get("Create_Time"))
    print("overfull schedules:", len(overfull))
    for sid, booked, cap, st, title in overfull[:10]:
        print(" ", sid, title, st, f"{booked}/{cap}")


if __name__ == "__main__":
    main()
