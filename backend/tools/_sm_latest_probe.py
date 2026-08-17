"""SM latest 实测：近期状态为空/异常的球队，SM 端是否有 latest 数据
判定：数据源缺失 vs 同步未跑
"""
import asyncio
import os
import sys
from dotenv import load_dotenv
load_dotenv(override=True)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.collector.sportmonks.client import SportMonksClient

# (队名, team_id, sm_id, 现象)
TEAMS = [
    ("博尔顿", 340, 16, "recent 空"),
    ("普雷斯顿", 326, 99, "recent 空"),
    ("布赖代合作", 458, 2392, "recent 空"),
    ("赛哈特海湾", 435, 12216, "recent 空"),
    ("谢菲尔德联", 368, 21, "recent 空"),
    ("吉达联合", 464, 476, "recent 空"),
    ("拉斯永恒", 439, 232744, "recent 空"),
    ("利雅得胜利", 436, 2506, "recent 空"),
    ("穆拜赖兹征服", 432, 5891, "recent 空"),
    ("明尼苏达联", 201, 3639, "recent 空"),
    ("伯恩利", 296, 27, "recent 空"),
    ("布兰", 171, 2474, "recent 空列表"),
    ("马里迪莫", 1683, 5931, "无统计记录"),
    ("利勒斯特罗姆", 1608, 269, "recent 最新2002-09-18 异常"),
]


async def main():
    out = []
    def log(s=""):
        print(s)
        out.append(s)
    sm = SportMonksClient()
    log(f"API key: {sm.api_key[:6]}...")
    for name, tid, smid, issue in TEAMS:
        try:
            data = await sm.get_team_by_id(smid, includes="latest;latest.participants;latest.scores")
            latest = data.get("latest") or []
            log(f"  {name}(id={tid},sm={smid}) {issue}: SM latest 返回 {len(latest)} 条")
            for m in latest[:5]:
                participants = m.get("participants") or []
                pnames = {}
                for p in participants:
                    if isinstance(p, dict):
                        pnames[p.get("id")] = p.get("name")
                scores = m.get("scores") or []
                goals = {}
                for s in scores:
                    if isinstance(s, dict) and s.get("description") == "CURRENT":
                        goals[s.get("participant_id")] = (s.get("score") or {}).get("goals")
                lg = (m.get("league") or {}).get("name", "")
                hname = pnames.get(next(iter(goals), None), "?") if goals else "?"
                aname = "?"
                log(f"      fx={m.get('id')} {m.get('starting_at','')[:10]} [{lg}] {goals} ({hname}...)")
        except Exception as e:
            log(f"  {name}(id={tid},sm={smid}) {issue}: 异常 {type(e).__name__}: {e}")
    await sm.close()

    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "_out_sm_latest.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    print("\n[saved to _out_sm_latest.txt]")


asyncio.run(main())
