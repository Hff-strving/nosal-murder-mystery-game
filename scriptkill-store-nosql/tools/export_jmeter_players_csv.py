# -*- coding: utf-8 -*-
"""
导出 JMeter 用玩家账号 CSV（从 MongoDB users 集合读取）

用法：
  python tools/export_jmeter_players_csv.py --out tools/jmeter_players.csv --limit 100
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nosql.mongo import get_db


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="tools/jmeter_players.csv")
    ap.add_argument("--limit", type=int, default=120)
    args = ap.parse_args()

    db = get_db()
    rows = list(
        db["users"]
        .find({"Role": "player"}, {"_id": 0, "Username": 1})
        .sort("User_ID", 1)
        .limit(int(args.limit))
    )

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="") as f:
        f.write("username,password\n")
        for r in rows:
            f.write(f"{r['Username']},123456\n")

    print(f"[OK] exported {len(rows)} players -> {args.out}")


if __name__ == "__main__":
    main()

