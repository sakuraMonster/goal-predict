"""SM sidelined 结构细看：不同 include 写法的返回差异
输出：_out_sm_sidelined_raw.txt
"""
import asyncio, os, sys, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv(override=True)
from app.collector.sportmonks.client import SportMonksClient

OUT = []
def log(s=""):
    OUT.append(str(s))
    print(s, flush=True)

async def main():
    sm = SportMonksClient()
    for inc in ("sidelined", "sidelined.player", "player;sidelined"):
        try:
            raw = await sm._get("/teams/8", {"include": inc})
            d = raw.get("data") or {}
            sd = d.get("sidelined") or []
            log(f"include={inc!r}: {len(sd)} 条")
            if sd:
                log(f"  记录完整 JSON: {json.dumps(sd[0], ensure_ascii=False, default=str)[:900]}")
            log("")
        except Exception as ex:
            log(f"include={inc!r}: 异常 {type(ex).__name__}: {str(ex)[:200]}")
            log("")
    await sm.close()

    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "_out_sm_sidelined_raw.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(OUT))
    print("\n[saved to _out_sm_sidelined_raw.txt]")

asyncio.run(main())
