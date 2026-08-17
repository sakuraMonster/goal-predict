"""直接调用 SyncPipeline.sync_team_info() 补齐 H2H + 近期状态，输出到 UTF-8 文件"""
import asyncio
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from dotenv import load_dotenv

load_dotenv(r"e:\zhangxuejun\new-thinking\ricking-03\backend\.env", override=True)

OUT = "e:/zhangxuejun/new-thinking/ricking-03/backend/tools/_out_sync_teams.txt"
_log = []


def p(s=""):
    print(s, flush=True)
    _log.append(str(s))


class Tee:
    def write(self, data):
        _log.append(str(data))

    def flush(self):
        pass


async def main():
    from app.collector.pipeline import SyncPipeline

    pipeline = SyncPipeline()
    try:
        await pipeline.sync_team_info()
        p("[OK] sync_team_info 完成")
    except Exception as e:
        p(f"[FAIL] sync_team_info: {e}")
        traceback.print_exc(file=Tee())
    finally:
        try:
            await pipeline.sm.close()
        except Exception:
            pass
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(_log))
    p(f"\n[written] {OUT}")


asyncio.run(main())
