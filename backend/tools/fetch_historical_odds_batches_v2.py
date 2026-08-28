""" V2 修复窗口起止时间:
- 原 bug: START_BOUND = datetime(2026,8,3,23,59,59), cur_start = cur_end - 28 days, cur_end 每次 = cur_start - 1s
  → 窗口日期越往前越错开 1 天, 导致 01-01~05-11 待拉取 0 场.
- 修复: 按自然日 [YYYY-MM-DD 00:00:00, YYYY-MM-DD 23:59:59] 整 29 天, 从 08-03 往前推.
  - 窗口 1: 2026-07-06 00:00:00 ~ 2026-08-03 23:59:59
  - 窗口 2: 2026-06-07 00:00:00 ~ 2026-07-05 23:59:59
  - 窗口 3: 2026-05-09 00:00:00 ~ 2026-06-06 23:59:59
  - ...
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from datetime import datetime, timedelta, time

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND_ROOT = os.path.dirname(HERE)
PYTHON = sys.executable
FETCH_SCRIPT = os.path.join(HERE, "fetch_market_flow_odds.py")

STEP_DAYS = 29
# 覆盖最旧日期(完整4玩法) = 2026-04-10，04-09 及之前一场没覆盖
END_DAY = datetime(2026, 4, 9)  # 截止日期（含）
BEGIN_DAY = datetime(2026, 1, 1)  # 最旧窗口的最早起始日（含）


def windows_from_newest_to_oldest(end_day: datetime, begin_day: datetime, step_days: int):
    cur_end_day = end_day
    while cur_end_day >= begin_day:
        cur_start_day = cur_end_day - timedelta(days=step_days - 1)
        if cur_start_day < begin_day:
            cur_start_day = begin_day
        w_start = datetime.combine(cur_start_day.date(), time.min)
        w_end   = datetime.combine(cur_end_day.date(), time.max).replace(microsecond=0)
        yield w_start, w_end
        cur_end_day = cur_start_day - timedelta(days=1)


def fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


async def run_one(idx: int, w_start: datetime, w_end: datetime) -> dict:
    tag = f"[{idx:02d}]"
    args = [PYTHON, FETCH_SCRIPT, "--start", fmt(w_start), "--end", fmt(w_end)]
    w_start_str = w_start.strftime("%Y%m%d")
    w_end_str = w_end.strftime("%Y%m%d")
    log_file = os.path.join(HERE, f"__fetch_hist_v2_{w_start_str}_{w_end_str}.log")
    print(f"\n{tag} ========================================================")
    print(f"{tag} 窗口: {fmt(w_start)}  ~  {fmt(w_end)}   (共 {(w_end-w_start).days+1} 个自然日)")
    print(f"{tag} CMD: {' '.join(args)}")
    print(f"{tag} 日志: {log_file}")
    with open(log_file, "w", encoding="utf-8") as fo:
        sub_env = os.environ.copy()
        sub_env.setdefault("PYTHONIOENCODING", "utf-8")
        sub_env.setdefault("PYTHONUTF8", "1")
        sub_env.setdefault("PYTHONUNBUFFERED", "1")
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
            stripped = line.rstrip()
            if stripped:
                print(f"{tag} {stripped}", flush=True)
        rc = proc.wait(timeout=3600 * 6)
    summary_line = "(unknown)"
    try:
        with open(log_file, "r", encoding="utf-8") as fi:
            for line in fi:
                if line.strip().startswith("完成:") or line.strip().startswith("待拉取 0"):
                    summary_line = line.strip()
    except Exception:
        pass
    print(f"{tag} RC={rc}  {summary_line}")
    return {"idx": idx, "start": w_start, "end": w_end, "rc": rc, "summary": summary_line, "log": log_file}


async def main():
    import re
    ws = list(windows_from_newest_to_oldest(END_DAY, BEGIN_DAY, STEP_DAYS))
    print(f"总共 {len(ws)} 批窗口，每次 {STEP_DAYS} 天，从 {fmt(datetime.combine(END_DAY.date(), time.max))} 推回到 {fmt(datetime.combine(BEGIN_DAY.date(), time.min))}")
    for i, (s, e) in enumerate(ws, 1):
        print(f"  #{i:02d}  {fmt(s)} ~ {fmt(e)}  ({(e-s).days+1}d)")

    all_results = []
    for i, (s, e) in enumerate(ws, 1):
        r = await run_one(i, s, e)
        all_results.append(r)
        if r["rc"] != 0:
            print(f"  !!!! #{i} 窗口返回异常 RC={r['rc']}，日志见 {r['log']}")

    print("\n" + "=" * 90)
    print("全部窗口完成，汇总：")
    print("=" * 90)
    inserted = skipped_parse = skipped_fetch = skipped_dup = 0
    pending_zero = n_ok = 0
    for r in all_results:
        print(f"  #{r['idx']:02d} {fmt(r['start'])} ~ {fmt(r['end'])}  RC={r['rc']}  {r['summary']}")
        m = re.search(r"写入\s*(\d+)\s*条.*拉取失败\s*(\d+).*解析失败\s*(\d+).*重复\s*(\d+)", r["summary"])
        if m:
            inserted += int(m.group(1))
            skipped_fetch += int(m.group(2))
            skipped_parse += int(m.group(3))
            skipped_dup += int(m.group(4))
            n_ok += 1
        elif r["summary"].startswith("待拉取 0"):
            pending_zero += 1
            n_ok += 1
    print(f"\n累计: 写入 {inserted} 条，拉取失败 {skipped_fetch}，解析失败 {skipped_parse}，重复 {skipped_dup} (成功批次 {n_ok}/{len(all_results)}，待拉取 0 场的空批 {pending_zero})")

if __name__ == "__main__":
    asyncio.run(main())
