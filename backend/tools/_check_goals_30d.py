"""统计近30天 Model C 进球数预测命中情况（基于 snap_top2_c）"""
import asyncio
import asyncpg
import json
from datetime import datetime, timedelta, timezone
from collections import defaultdict


async def main():
    conn = await asyncpg.connect(
        "postgresql://postgres:postgres@localhost:5432/football_prediction"
    )

    BJ = timezone(timedelta(hours=8))
    today = datetime.now(BJ)
    start = (today - timedelta(days=30)).replace(tzinfo=None)

    # 查询近30天已结算的预测记录（有 actual_total_goals 的）
    rows = await conn.fetch(
        """
        SELECT p.expected_goals_c, p.snap_top2_c, p.snap_top2,
               p.actual_total_goals, l.name_zh AS league_name
        FROM predictions p
        JOIN matches m ON p.match_id = m.id
        JOIN leagues l ON m.league_id = l.id
        WHERE p.kickoff_time >= $1 AND p.actual_total_goals IS NOT NULL
        ORDER BY p.kickoff_time DESC
        """,
        start,
    )

    # Model C: 用 snap_top2_c 判定，无 C 则回退 snap_top2
    total = 0
    hit = 0
    by_league = defaultdict(lambda: {"total": 0, "hit": 0})

    for r in rows:
        raw = r["snap_top2_c"] if r["snap_top2_c"] else r["snap_top2"]
        if not raw:
            continue
        snap = json.loads(raw) if isinstance(raw, str) else raw
        if not snap or len(snap) < 2:
            continue
        total += 1
        act_capped = min(r["actual_total_goals"], 4)
        lg = r["league_name"] or "未知"
        by_league[lg]["total"] += 1
        if act_capped in snap:
            hit += 1
            by_league[lg]["hit"] += 1

    miss = total - hit
    acc = round(hit / total * 100, 1) if total > 0 else 0.0

    print("=" * 70)
    print(
        f"  Model C 进球数预测命中情况（近30天: {today.strftime('%Y-%m-%d')} 往前推30天）"
    )
    print("=" * 70)
    print(f"  已结算场次: {total}")
    print(f"  命中: {hit}")
    print(f"  未命中: {miss}")
    print(f"  命中率: {acc}%")
    print()

    sorted_leagues = sorted(
        by_league.items(), key=lambda x: x[1]["total"], reverse=True
    )

    header = f'  {"联赛":<14s} {"场次":>5s} {"命中":>5s} {"未命中":>5s} {"命中率":>7s}'
    print(header)
    print("  " + "-" * 44)
    for lg_name, stats in sorted_leagues:
        t = stats["total"]
        h = stats["hit"]
        m = t - h
        a = round(h / t * 100, 1) if t > 0 else 0.0
        if len(lg_name) > 12:
            display = lg_name[:11] + "…"
        else:
            display = lg_name
        print(f"  {display:<14s} {t:>5d} {h:>5d} {m:>5d} {a:>6.1f}%")

    await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
