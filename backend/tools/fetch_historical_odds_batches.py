""" 分批拉取 2026-01-01 ~ 2026-08-03 历史固定奖金赔率到 jczq_play_odds_snapshots.

每次 29 天窗口，从新到旧依次推进，每个窗口调用 tools/fetch_market_flow_odds.py。
- START_BOUND : "2026-08-03 23:59:59" (含之前的数据，不再重复 08-03~08-14)
- STOP_DATE   : "2026-01-01 00:00:00"
- STEP_DAYS   : 29
输出: tools/__fetch_hist_YYYYMMDD.log 每批一个最终汇总，stdout 实时打印
"""
import asyncio
import subprocess
import sys
from datetime import datetime, timedelta

import os
HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND_ROOT = os.path.dirname(HERE)
PYTHON = sys.executable
FETCH_SCRIPT = os.path.join(HERE, "fetch_market_flow_odds.py")

STEP_DAYS = 29
# 注意：按用户要求每次29天，从08-03向前推到01-01
# 窗口闭区间 [window_start, window_end]，fetch_market_flow_odds 用 >= start 和 <= end
START_BOUND = datetime(2026, 8, 3, 23, 59, 59)
STOP_BOUND  = datetime(2026, 1, 1, 0, 0, 0)

def fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")

def windows():
    cur_end = START_BOUND
    while cur_end >= STOP_BOUND:
        cur_start = cur_end - timedelta(days=STEP_DAYS - 1)
        if cur_start < STOP_BOUND:
            cur_start = STOP_BOUND
        # 窗口不要跨 STOP 太多：如果 cur_end < STOP 就退出
        if cur_end < STOP_BOUND or cur_start > cur_end:
            break
        yield (cur_start, cur_end)
        cur_end = cur_start - timedelta(seconds=1)  # 避免重复重叠 1 秒边界

async def run_one(idx: int, w_start: datetime, w_end: datetime) -> dict:
    tag = f"[{idx:02d}]"
    args = [
        PYTHON, FETCH_SCRIPT,
        "--start", fmt(w_start),
        "--end",   fmt(w_end),
    ]
    log_file = os.path.join(HERE, f"__fetch_hist_{w_start.strftime('%Y%m%d')}_{w_end.strftime('%Y%m%d')}.log")
    print(f"\n{tag} ========================================================")
    print(f"{tag} 窗口: {fmt(w_start)}  ~  {fmt(w_end)}   (共 {(w_end-w_start).days+1} 个自然日)")
    print(f"{tag} CMD: {' '.join(args)}")
    print(f"{tag} 日志: {log_file}")
    with open(log_file, "w", encoding="utf-8") as fo:
        sub_env = os.environ.copy()
        sub_env.setdefault("PYTHONIOENCODING", "utf-8")
        sub_env.setdefault("PYTHONUTF8", "1")
        proc = subprocess.Popen(
            args, cwd=BACKEND_ROOT,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            bufsize=1, text=True, encoding="utf-8", errors="replace",
            env=sub_env,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            fo.write(line); fo.flush()
            # 只把最后一行统计 / 失败行打印到外层
            stripped = line.rstrip()
            if stripped.startswith("待拉取") or stripped.startswith("完成:") or \
               stripped.startswith("  [fetch-fail") or stripped.startswith("  [parse-fail"):
                print(f"{tag} {stripped}")
        rc = proc.wait(timeout=3600*3)
    summary_line = "(unknown)"
    try:
        with open(log_file, "r", encoding="utf-8") as fi:
            for line in fi:
                if line.strip().startswith("完成:"):
                    summary_line = line.strip()
    except Exception:
        pass
    print(f"{tag} RC={rc}  {summary_line}")
    return {"idx": idx, "start": w_start, "end": w_end, "rc": rc, "summary": summary_line, "log": log_file}

async def main():
    ws = list(windows())
    print(f"总共 {len(ws)} 批窗口，每次 {STEP_DAYS} 天，从 {fmt(START_BOUND)} 推回到 {fmt(STOP_BOUND)}")
    for i, (s, e) in enumerate(ws, 1):
        print(f"  #{i:02d}  {fmt(s)} ~ {fmt(e)}")

    all_results = []
    for i, (s, e) in enumerate(ws, 1):
        r = await run_one(i, s, e)
        all_results.append(r)
        if r["rc"] != 0:
            print(f"  !!!! #{i} 窗口返回异常 RC={r['rc']}，日志见 {r['log']}")

    print("\n" + "=" * 80)
    print("全部窗口完成，汇总：")
    print("=" * 80)
    inserted = skipped_parse = skipped_fetch = skipped_dup = 0
    n_ok = 0
    for r in all_results:
        print(f"  #{r['idx']:02d} {fmt(r['start'])} ~ {fmt(r['end'])}  RC={r['rc']}  {r['summary']}")
        # 解析 "完成: 写入 X 条，拉取失败 Y，解析失败 Z，重复 W"
        import re
        m = re.search(r"写入\s*(\d+)\s*条.*拉取失败\s*(\d+).*解析失败\s*(\d+).*重复\s*(\d+)", r["summary"])
        if m:
            inserted += int(m.group(1))
            skipped_fetch += int(m.group(2))
            skipped_parse += int(m.group(3))
            skipped_dup += int(m.group(4))
            n_ok += 1
    print(f"\n累计: 写入 {inserted} 条，拉取失败 {skipped_fetch}，解析失败 {skipped_parse}，重复 {skipped_dup} (解析成功批次 {n_ok}/{len(all_results)})")

if __name__ == "__main__":
    asyncio.run(main())
