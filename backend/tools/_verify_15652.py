"""验证 15652 fixture 19629600 主客 + 1590/1608 对比"""
import asyncio
import json
import os
import sys
from dotenv import load_dotenv
load_dotenv(override=True)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.collector.sportmonks.client import SportMonksClient


async def main():
    sm = SportMonksClient()
    # 验证 fixture
    data = await sm.get_fixture_by_id(19629600, includes="participants")
    print("fixture 19629600:")
    print(f"  starting_at={data.get('starting_at')}")
    for p in (data.get("participants") or []):
        meta = p.get("meta") or {}
        print(f"  pid={p.get('id')} name={p.get('name')} location={meta.get('location')}")
    # 验证 2510 latest 最近比赛
    data2 = await sm.get_team_by_id(2510, includes="latest;latest.participants;latest.scores")
    latest = data2.get("latest") or []
    print(f"\nteam 2510 (Lillestrøm) latest: {len(latest)} 条")
    for m in latest[:5]:
        scores = m.get("scores") or []
        goals = {}
        for s in scores:
            if isinstance(s, dict) and s.get("description") == "CURRENT":
                goals[s.get("participant_id")] = (s.get("score") or {}).get("goals")
        lg = (m.get("league") or {}).get("name", "")
        print(f"  fx={m.get('id')} {m.get('starting_at','')[:10]} [{lg}] {goals}")
    await sm.close()


asyncio.run(main())
