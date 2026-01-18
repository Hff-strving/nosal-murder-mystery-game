# -*- coding: utf-8 -*-
"""
Make recent/future schedules not fully booked.

Purpose:
  - Ensure schedules in a given date range have at least N free seats.
  - Prefer adjusting unpaid orders; if needed, refund paid orders by marking
    their transactions Result=0 (so reports won't count them).

Usage (PowerShell):
  python tools/adjust_recent_availability.py --from-date 2026-01-09
"""

from __future__ import annotations

import argparse
import os
import random
import sys
from datetime import datetime
from typing import Dict, List, Tuple

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nosql.mongo import col
from nosql.redis_client import get_redis


def _parse_date(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d")


def _schedule_range(from_dt: datetime, to_dt: datetime) -> List[dict]:
    return list(
        col("schedules").find(
            {"Start_Time": {"$gte": from_dt, "$lte": to_dt}, "Status": {"$in": [0, 1]}},
            {"Schedule_ID": 1, "Start_Time": 1, "Max_Players": 1, "Script_Title": 1},
        )
    )


def _booked_counts(schedule_ids: List[int]) -> Dict[int, int]:
    if not schedule_ids:
        return {}
    booked: Dict[int, int] = {}
    for row in col("orders").aggregate(
        [
            {"$match": {"Schedule_ID": {"$in": schedule_ids}, "Pay_Status": {"$in": [0, 1]}}},
            {"$group": {"_id": "$Schedule_ID", "cnt": {"$sum": 1}}},
        ]
    ):
        booked[int(row["_id"])] = int(row["cnt"])
    return booked


def _pick_orders_to_adjust(schedule_id: int, need: int) -> List[dict]:
    """
    Prefer unpaid (0), then paid (1).
    """
    if need <= 0:
        return []
    cursor = (
        col("orders")
        .find(
            {"Schedule_ID": int(schedule_id), "Pay_Status": {"$in": [0, 1]}},
            {"_id": 1, "Order_ID": 1, "Pay_Status": 1},
        )
        .sort([("Pay_Status", 1), ("Create_Time", 1)])
        .limit(int(need))
    )
    return list(cursor)


def adjust_range(
    from_dt: datetime,
    to_dt: datetime,
    min_free: int = 1,
    max_free: int = 2,
    dry_run: bool = False,
) -> Tuple[int, int]:
    schedules = _schedule_range(from_dt, to_dt)
    schedule_ids = [int(s["Schedule_ID"]) for s in schedules]
    booked = _booked_counts(schedule_ids)

    now = datetime.now()
    adjusted_schedules = 0
    adjusted_orders = 0

    r = get_redis()

    for sch in sorted(schedules, key=lambda x: x.get("Start_Time") or datetime.min):
        schedule_id = int(sch["Schedule_ID"])
        cap = int(sch.get("Max_Players") or 0)
        if cap <= 0:
            continue

        current = int(booked.get(schedule_id, 0))
        free = max(0, cap - current)
        if free >= int(min_free):
            continue

        want_free = random.randint(int(min_free), int(max_free))
        target = cap - want_free
        if target < 0:
            target = 0
        if target >= cap:
            target = cap - 1
        if target > current:
            continue

        need_remove = current - target
        picks = _pick_orders_to_adjust(schedule_id, need_remove)
        if len(picks) < need_remove:
            # 兜底：数据不一致时，尽量做到能改多少改多少
            need_remove = len(picks)

        if need_remove <= 0:
            continue

        adjusted_schedules += 1

        for od in picks[:need_remove]:
            order_id = int(od.get("_id") or od.get("Order_ID"))
            pay_status = int(od.get("Pay_Status") or 0)

            if pay_status == 0:
                # unpaid -> cancelled
                if not dry_run:
                    col("orders").update_one(
                        {"_id": order_id},
                        {
                            "$set": {
                                "Pay_Status": 3,
                                "Adjusted": True,
                                "AdjustedAt": now,
                                "AdjustedReason": "make_recent_schedules_not_full",
                                "AdjustedType": "cancel_unpaid",
                            }
                        },
                    )
                adjusted_orders += 1
                continue

            # paid -> refunded (and mark transaction Result=0 so report won't count)
            if not dry_run:
                col("orders").update_one(
                    {"_id": order_id},
                    {
                        "$set": {
                            "Pay_Status": 2,
                            "Adjusted": True,
                            "AdjustedAt": now,
                            "AdjustedReason": "make_recent_schedules_not_full",
                            "AdjustedType": "refund_paid",
                        }
                    },
                )
                col("transactions").update_many(
                    {"Order_ID": order_id, "Trans_Type": 1, "Result": 1},
                    {
                        "$set": {
                            "Result": 0,
                            "Adjusted": True,
                            "AdjustedAt": now,
                            "AdjustedReason": "make_recent_schedules_not_full",
                        }
                    },
                )
            adjusted_orders += 1

        # Redis seats key may already exist; delete it so runtime will re-init from Mongo
        if not dry_run:
            r.delete(f"seats:{schedule_id}")

    return adjusted_schedules, adjusted_orders


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-date", default="2026-01-09", help="YYYY-MM-DD (inclusive)")
    ap.add_argument("--to-date", default=None, help="YYYY-MM-DD (inclusive), default=max schedule date")
    ap.add_argument("--min-free", type=int, default=1, help="minimum free seats per schedule")
    ap.add_argument("--max-free", type=int, default=2, help="maximum free seats per schedule")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    from_dt = _parse_date(args.from_date)

    if args.to_date:
        to_dt = _parse_date(args.to_date)
    else:
        last = col("schedules").find_one({}, {"Start_Time": 1}, sort=[("Start_Time", -1)])
        to_dt = (last or {}).get("Start_Time") or datetime.now()

    schedules = _schedule_range(from_dt, to_dt)
    if not schedules:
        print("[SKIP] no schedules in range")
        return

    adjusted_schedules, adjusted_orders = adjust_range(
        from_dt=from_dt,
        to_dt=to_dt,
        min_free=args.min_free,
        max_free=args.max_free,
        dry_run=bool(args.dry_run),
    )

    mode = "DRY-RUN" if args.dry_run else "APPLIED"
    print(f"[{mode}] adjusted schedules={adjusted_schedules}, adjusted orders={adjusted_orders}")
    print(f"  range={from_dt.strftime('%Y-%m-%d')} -> {to_dt.strftime('%Y-%m-%d')}")


if __name__ == "__main__":
    main()
