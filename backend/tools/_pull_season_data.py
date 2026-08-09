"""从 SportMonks 拉取联赛上赛季完赛数据，计算历史 calib"""
import asyncio, sys, json
sys.path.insert(0, "e:/zhangxuejun/new-thinking/ricking-03/backend")
from app.collector.sportmonks.client import SportMonksClient
from datetime import datetime, timedelta
from dotenv import load_dotenv
import os
env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
load_dotenv(env_path)

TARGETS = [
    # ("葡超", 462),  # 已完成: calib=1.0405
    ("英冠", 9),
    ("荷乙", 74),
    ("德乙", 85),
]

async def pull_league(sm, name, sm_lg_id):
    print(f"\n{'='*60}")
    print(f"  {name} (SM id={sm_lg_id})")
    print(f"{'='*60}")

    # 1. 获取联赛，找上个已结束赛季
    print(f"  [1] 联赛&赛季...")
    try:
        lg = await sm.get_league_by_id(sm_lg_id)
    except Exception as e:
        print(f"    API失败: {e}")
        return None

    seasons = lg.get("seasons", [])
    last = None
    for s in seasons:
        if s.get("finished"):
            last = s; break
    if not last:
        print(f"    无已结束赛季. available: {[(s['id'],s.get('name',''),s.get('finished')) for s in seasons[:3]]}")
        return None

    sid, sname = last["id"], last.get("name","?")
    d1, d2 = last.get("starting_at","")[:10], last.get("ending_at","")[:10]
    print(f"    赛季: {sname} ({d1} ~ {d2})")

    # 2. 按月拉取 fixtures
    print(f"  [2] 拉取 fixtures...")
    all_fx = []
    cur = datetime.strptime(d1, "%Y-%m-%d")
    end_dt = datetime.strptime(d2, "%Y-%m-%d")
    while cur < end_dt:
        nxt = min(cur + timedelta(days=7), end_dt)  # 7天一批，避免超时
        a, b = cur.strftime("%Y-%m-%d"), nxt.strftime("%Y-%m-%d")
        try:
            batch = await sm.get_fixtures_between(a, b, includes="scores;participants")
            # 客户端过滤联赛
            batch = [f for f in batch if f.get("league_id") == sm_lg_id]
            all_fx.extend(batch)
            print(f"    {a}~{b}: {len(batch)} 场")
        except Exception as e:
            # 时间太长就二分
            mid = cur + timedelta(days=14)
            try:
                b1 = await sm.get_fixtures_between(a, mid.strftime("%Y-%m-%d"), includes="scores;participants")
                b2 = await sm.get_fixtures_between(mid.strftime("%Y-%m-%d"), b, includes="scores;participants")
                b1 = [f for f in b1 if f.get("league_id") == sm_lg_id]
                b2 = [f for f in b2 if f.get("league_id") == sm_lg_id]
                all_fx.extend(b1); all_fx.extend(b2)
                print(f"    {a}~{mid.strftime('%Y-%m-%d')}: {len(b1)}, {mid.strftime('%Y-%m-%d')}~{b}: {len(b2)}")
            except Exception as ee:
                print(f"    {a}~{b}: skip ({ee})")
        cur = nxt

    print(f"    总计: {len(all_fx)} fixtures")

    # 3. 提取完场数据
    finished = []
    for fx in all_fx:
        # 通过 scores 判断完场
        scores = fx.get("scores", [])
        cur_scores = [s for s in scores if s.get("description") == "CURRENT"]
        if not cur_scores:
            continue

        home_g, away_g = None, None
        for sc in cur_scores:
            p = sc.get("score", {}).get("participant", "")
            g = sc.get("score", {}).get("goals")
            if p == "home": home_g = g
            elif p == "away": away_g = g

        if home_g is None or away_g is None:
            continue

        parts = fx.get("participants", [])
        home_name = away_name = ""
        for p in parts:
            loc = p.get("meta", {}).get("location", "")
            if loc == "home": home_name = p.get("name", "")
            elif loc == "away": away_name = p.get("name", "")

        finished.append({
            "date": fx.get("starting_at", "")[:10],
            "home": home_name, "away": away_name,
            "home_score": int(home_g), "away_score": int(away_g),
            "total": int(home_g) + int(away_g),
        })

    n = len(finished)
    if not n:
        print(f"    无完场数据")
        return None

    avg = sum(m["total"] for m in finished) / n
    dist = {}
    for m in finished:
        g = min(m["total"], 4)
        dist[g] = dist.get(g, 0) + 1

    ratio = avg / 2.5  # actual vs 2.5 盘口基线

    print(f"  [3] 完场: {n} 场, 场均进球: {avg:.2f}")
    print(f"      actual/2.5 = {ratio:.4f}  → 建议 calib = {ratio:.4f}")
    print(f"      分布: ", end="")
    for g in sorted(dist):
        print(f"{g}+球={dist[g]/n*100:.1f}%  " if g >= 4 else f"{g}球={dist[g]/n*100:.1f}%  ", end="")
    print()

    return {"name": name, "season": sname, "n": n, "avg": round(avg,2), "ratio": round(ratio,4)}

async def main():
    sm = SportMonksClient()
    results = []
    for name, lid in TARGETS:
        r = await pull_league(sm, name, lid)
        if r:
            results.append(r)

    # 汇总
    print(f"\n{'='*60}")
    print(f"  汇总")
    print(f"{'='*60}")
    print(f"  {'联赛':<6s} {'赛季':<12s} {'场次':>5s} {'场均':>6s} {'actual/2.5':>9s} {'建议calib':>8s}")
    print(f"  {'-'*50}")
    for r in results:
        print(f"  {r['name']:<6s} {r['season']:<12s} {r['n']:>5d} {r['avg']:>6.2f} {r['ratio']:>9.4f} {r['ratio']:>8.4f}")

    await sm.close()

if __name__ == "__main__":
    asyncio.run(main())
