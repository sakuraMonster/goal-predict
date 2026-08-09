"""拉取 2025/2026 赛季数据——逐日拉取，稳定可靠"""
import asyncio, sys
sys.path.insert(0, "e:/zhangxuejun/new-thinking/ricking-03/backend")
from app.collector.sportmonks.client import SportMonksClient
from datetime import datetime, timedelta
from dotenv import load_dotenv
import os
env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
load_dotenv(env_path)

TARGETS = [
    ("葡超", 462, "2025-08-08", "2026-05-16"),
    ("英冠", 9, "2025-08-08", "2026-05-23"),
    ("荷乙", 74, "2025-08-08", "2026-04-24"),
    ("德乙", 85, "2025-08-01", "2026-05-17"),
]

async def pull_league(sm, name, sm_lg_id, d1, d2):
    print(f"\n{'='*50}")
    print(f"  {name} 2025/2026  ({d1} ~ {d2})")
    print(f"{'='*50}")

    cur = datetime.strptime(d1, "%Y-%m-%d")
    end_dt = datetime.strptime(d2, "%Y-%m-%d")
    total_days = (end_dt - cur).days
    all_fx = []
    day_count = 0

    while cur <= end_dt:
        date_str = cur.strftime("%Y-%m-%d")
        try:
            batch = await sm.get_fixtures_by_date(date_str, includes="scores")
            batch = [f for f in batch if f.get("league_id") == sm_lg_id]
            all_fx.extend(batch)
        except Exception:
            pass

        day_count += 1
        cur += timedelta(days=1)

        # 每 7 天汇报进度
        if day_count % 7 == 0:
            pct = day_count / total_days * 100
            print(f"    进度: {day_count}/{total_days}天 ({pct:.0f}%), 已获取 {len(all_fx)} 场", flush=True)

    # 提取完场
    goals = []
    for fx in all_fx:
        scores = fx.get("scores", [])
        cur_scores = [s for s in scores if s.get("description") == "CURRENT"]
        if not cur_scores:
            continue
        home_g = away_g = None
        for sc in cur_scores:
            p = sc.get("score", {}).get("participant", "")
            g = sc.get("score", {}).get("goals")
            if p == "home": home_g = g
            elif p == "away": away_g = g
        if home_g is not None and away_g is not None:
            goals.append(int(home_g) + int(away_g))

    n = len(goals)
    if not n:
        return None

    avg = sum(goals) / n
    dist = {}
    for g in goals:
        dg = min(g, 4)
        dist[dg] = dist.get(dg, 0) + 1

    return {"n": n, "avg": round(avg, 2), "ratio": round(avg / 2.5, 4),
            "dist": {k: round(v/n*100, 1) for k, v in dist.items()}}

async def main():
    sm = SportMonksClient()
    results = {}

    for name, lid, d1, d2 in TARGETS:
        r = await pull_league(sm, name, lid, d1, d2)
        if r:
            results[name] = r
            print(f"  => {r['n']}场, 场均{r['avg']}, actual/2.5={r['ratio']}")
        else:
            print(f"  => 无数据")

    # 汇总
    print(f"\n{'='*70}")
    print(f"  2025/2026 赛季汇总")
    print(f"{'='*70}")
    h = f"  {'联赛':<6s} {'场次':>5s} {'场均':>6s} {'calib':>6s}  {'0球':>5s} {'1球':>5s} {'2球':>5s} {'3球':>5s} {'4+球':>5s}"
    print(h)
    print(f"  {'-'*60}")
    for name, r in results.items():
        d = r["dist"]
        print(f"  {name:<6s} {r['n']:>5d} {r['avg']:>6.2f} {r['ratio']:>6.4f}  {d.get(0,0):>4.1f}% {d.get(1,0):>4.1f}% {d.get(2,0):>4.1f}% {d.get(3,0):>4.1f}% {d.get(4,0):>4.1f}%")

    await sm.close()

if __name__ == "__main__":
    asyncio.run(main())
