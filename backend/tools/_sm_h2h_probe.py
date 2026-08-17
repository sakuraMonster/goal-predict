"""SM H2H API 实测：缺 H2H 对位是否有数据 + 009 是否可补齐 2026 交锋
判定：数据源缺失 vs 同步未跑
"""
import asyncio
import os
import sys
from dotenv import load_dotenv
load_dotenv(override=True)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.collector.sportmonks.client import SportMonksClient

# (比赛id, match_num, 说明, sm1, sm2)
PAIRS = [
    (15622, "周六002", "秋田蓝色闪电 vs 富山胜利 (日乙)", 18263, 17798),
    (15627, "周六007", "柏林赫塔 vs 海登海姆 (德乙)", 3317, 2831),
    (15649, "周六008", "博尔顿 vs 普雷斯顿 (英冠)", 16, 99),
    (15650, "周六009", "米亚尔比 vs 天狼星 (瑞典超)", 411, 2678),
    (15652, "周六011", "奥斯陆KFUM vs 利勒斯特罗姆 (挪超)", 11914, 269),
    (15631, "周六018", "维塞乌 vs 圣克拉拉 (葡超)", 8297, 2628),
    (15633, "周六022", "SBV精英 vs PSV埃因霍温 (荷甲)", None, 682),
    (15634, "周六023", "福图纳锡塔德 vs 坎布尔 (荷甲)", 1459, 1435),
    (15637, "周日001", "德岛漩涡 vs 鸟栖沙岩 (日乙)", 5402, 639),
    (15638, "周日002", "海牙 vs 格罗宁根 (荷甲)", 1128, 2345),
    (15663, "周日010", "奥勒松 vs 瓦勒伦加 (挪超)", 393, 502),
    (15644, "周日013", "葡萄牙国民 vs 埃斯托里尔 (葡超)", 828, 1198),
    (15665, "周日014", "卡尔马 vs 哈马比 (瑞典超)", 432, 2353),
    (15673, "周日026", "法马利康 vs 马里迪莫 (葡超)", 3161, 5931),
]


async def main():
    out = []
    def log(s=""):
        print(s)
        out.append(s)
    sm = SportMonksClient()
    log(f"API key: {sm.api_key[:6]}...")
    for mid, num, desc, sm1, sm2 in PAIRS:
        if not sm1 or not sm2:
            log(f"  #{mid} {num} {desc}: sm_id 缺失(sm1={sm1},sm2={sm2}) 跳过")
            continue
        try:
            data = await sm.get_head_to_head(sm1, sm2)
            if not isinstance(data, list) or len(data) == 0:
                log(f"  #{mid} {num} {desc}: SM 返回 0 条")
                continue
            log(f"  #{mid} {num} {desc}: SM 返回 {len(data)} 条")
            for h in data[:8]:
                pid1 = None
                for p in (h.get("participants") or []):
                    meta = p.get("meta") or {}
                    if meta.get("location") == "home":
                        pid1 = p.get("id")
                        break
                pid2 = None
                for p in (h.get("participants") or []):
                    meta = p.get("meta") or {}
                    if meta.get("location") == "away":
                        pid2 = p.get("id")
                        break
                scores = h.get("scores") or []
                goals = {}
                for s in scores:
                    if isinstance(s, dict) and s.get("description") == "CURRENT":
                        goals[s.get("participant_id")] = (s.get("score") or {}).get("goals")
                lg = (h.get("league") or {}).get("name", "")
                log(f"      fx={h.get('id')} {h.get('starting_at','')[:10]} [{lg}] {pid1} {goals.get(pid1)}:{goals.get(pid2)} {pid2}")
        except Exception as e:
            log(f"  #{mid} {num} {desc}: 异常 {type(e).__name__}: {e}")
    await sm.close()

    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "_out_sm_h2h.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    print("\n[saved to _out_sm_h2h.txt]")


asyncio.run(main())
