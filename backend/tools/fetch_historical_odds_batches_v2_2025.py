""" V2 修复窗口起止时间 - 2025 全年版
从 2025-12-31 往前推到 2025-01-01，每批 29 天自然日 [00:00:00, 23:59:59]
跳过 2026-01-01 已覆盖的 29 条完整快照，避免 HTTP 请求后 [dup] 浪费。
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
END_DAY = datetime(2025, 12, 31)   # 截止日期（含）—— 2025 全年，跳过 2026-01-01 已覆盖的 29 条避免 [dup]
BEGIN_DAY = datetime(2025, 1, 1)   # 最旧窗口的最早起始日（含）—— 2025-01-01


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
    start_s = w_start.strftime("%Y%m%d")
    end_s = w_end.strftime("%Y%m%d")
    log_path = os.path.join(HERE, f"__fetch_hist_v2_2025_{start_s}_{end_s}.log")
    args = [
        PYTHON, FETCH_SCRIPT,
        "--start", fmt(w_start),
        "--end", fmt(w_end),
    ]
    sub_env = os.environ.copy()
    sub_env["PYTHONIOENCODING"] = "utf-8"
    sub_env["PYTHONUTF8"] = "1"
    sub_env["PYTHONUNBUFFERED"] = "1"
    label = f"[{idx:02d}]"
    print(f"{label} ================= 窗口 {fmt(w_start)} ~ {fmt(w_end)}  (log: {os.path.basename(log_path)}) =================", flush=True)
    log_f = open(log_path, "w", encoding="utf-8", buffering=1)
    created = updated = dup_cnt = parse_fail = http_fail = 0
    tag_prefix = f"[{idx:02d}] "
    proc = subprocess.Popen(
        args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        env=sub_env, cwd=BACKEND_ROOT, text=True, encoding="utf-8",
        errors="replace", bufsize=1, stdin=subprocess.DEVNULL,
    )
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip("\n")
            log_f.write(line + "\n")
            print(tag_prefix + line, flush=True)
            if "[insert]" in line:
                created += 1
            elif "[dup]" in line:
                dup_cnt += 1
            elif "[parse-fail]" in line:
                parse_fail += 1
            elif "[http-fail]" in line or "[fetch-fail]" in line:
                http_fail += 1
    finally:
        rc = proc.wait(timeout=60)
        log_f.close()
    return {
        "idx": idx, "rc": rc,
        "inserted": created, "dup": dup_cnt,
        "parse_fail": parse_fail, "http_fail": http_fail,
    }


async def main():
    wins = list(windows_from_newest_to_oldest(END_DAY, BEGIN_DAY, STEP_DAYS))
    print(f"2025 全年共 {len(wins)} 批：从 END=2025-12-31 往前推 29d 窗口，跳过 2026-01-01 已覆盖区间。")
    for i, (s, e) in enumerate(wins, 1):
        print(f"  #{i:02d} {s.date()} ~ {e.date()}  ({(e-s).days+1}d)")
    print(flush=True)
    total_ins = total_dup = total_pfail = total_hfail = 0
    for idx, (w_s, w_e) in enumerate(wins, 1):
        r = await run_one(idx, w_s, w_e)
        total_ins += r["inserted"]; total_dup += r["dup"]
        total_pfail += r["parse_fail"]; total_hfail += r["http_fail"]
        if r["rc"] != 0:
            print(f"[#{idx:02d}] 进程 rc={r['rc']}，请检查对应日志", flush=True)
    print()
    print(f"[FINISHED 2025] 窗口={len(wins)}  写入={total_ins}  重复跳过={total_dup}  parse-fail={total_pfail}  http-fail={total_hfail}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
