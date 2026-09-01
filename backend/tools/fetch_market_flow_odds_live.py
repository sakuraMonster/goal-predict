"""从竞彩网 getMatchCalculatorV1.qry 拉取未来比赛的实时赔率（4 玩法齐全），写入 JczqPlayOddsSnapshot。

针对 08-21 及之后未开赛的比赛，固定奖金 API (getFixedBonusV1) 还没有记录，需要用实时计算器 API 拉取当前可售赔率。

API：https://webapi.sporttery.cn/gateway/jc/football/getMatchCalculatorV1.qry?poolCode=had,hhad,ttg,crs&channel=c

用法：
    python tools/fetch_market_flow_odds_live.py --dry-run
    python tools/fetch_market_flow_odds_live.py --start 2026-08-21 --end 2026-08-28
"""
from __future__ import annotations

import argparse
import asyncio
import re
import sys
import os
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

import requests
from sqlalchemy import select

from app.db.database import async_session
from app.db.models import JczqPlayOddsSnapshot, Match, Team

JCZQ_API = "https://webapi.sporttery.cn/gateway/jc/football/getMatchCalculatorV1.qry"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.sporttery.cn/jc/jsq/zqspf/",
    "Origin": "https://www.sporttery.cn",
}

CRS_RE = re.compile(r"^s(\d{2})s(\d{2})$")
TTG_KEYS = {"s0": "0", "s1": "1", "s2": "2", "s3": "3", "s4": "4", "s5": "5", "s6": "6", "s7": "7"}
HAFU_KEYS = ("hh", "hd", "ha", "dh", "dd", "da", "ah", "ad", "aa")


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None


def _line(v):
    if v is None or v == "":
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def parse_had(obj):
    if not isinstance(obj, dict):
        return None
    h, d, a = _num(obj.get("h")), _num(obj.get("d")), _num(obj.get("a"))
    if h is None or d is None or a is None:
        return None
    return {"home": h, "draw": d, "away": a}


def parse_hhad(obj):
    if not isinstance(obj, dict):
        return None
    h, d, a = _num(obj.get("h")), _num(obj.get("d")), _num(obj.get("a"))
    if h is None or d is None or a is None:
        return None
    return {"line": _line(obj.get("goalLine") or obj.get("goalLineValue")), "home": h, "draw": d, "away": a}


def parse_ttg(obj):
    if not isinstance(obj, dict):
        return {}
    out = {}
    for sk, g in TTG_KEYS.items():
        v = _num(obj.get(sk))
        if v is not None:
            out[g] = v
    return out


def parse_crs(obj):
    if not isinstance(obj, dict):
        return {}
    out = {}
    for k, v in obj.items():
        m = CRS_RE.match(k)
        if not m:
            continue
        odd = _num(v)
        if odd is None:
            continue
        home, away = int(m.group(1)), int(m.group(2))
        out[f"{home}-{away}"] = odd
    return out


def parse_hafu(obj):
    """半全场收盘赔率：胜胜hh/胜平hd/胜负ha/平胜dh/平平dd/平负da/负胜ah/负平ad/负负aa"""
    if not isinstance(obj, dict):
        return None
    out = {}
    for k in HAFU_KEYS:
        v = _num(obj.get(k))
        if v is not None:
            out[k] = v
    return out or None


def _update_time(obj):
    d = (obj.get("updateDate") or "").strip()
    t = (obj.get("updateTime") or "").strip()
    s = f"{d} {t}".strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return datetime.utcnow()


def fetch_live(pool_code: str = "had,hhad,ttg,crs,hafu") -> list[dict]:
    r = requests.get(JCZQ_API, params={"poolCode": pool_code, "channel": "c"}, headers=HEADERS, timeout=20)
    r.raise_for_status()
    data = r.json()
    if not data.get("success"):
        raise RuntimeError(f"竞彩网API返回错误 success=false: {data.get('errorMessage')}")
    out = []
    match_info_list = data.get("value", {}).get("matchInfoList", []) or []
    for day_group in match_info_list:
        for m in (day_group.get("subMatchList") or []):
            out.append(m)
    return out


def _snap_equal(snap: JczqPlayOddsSnapshot, had, hhad, ttg, crs):
    return (
        snap.had_home == had["home"]
        and snap.had_draw == had["draw"]
        and snap.had_away == had["away"]
        and abs(snap.hhad_line - hhad["line"]) < 1e-6
        and snap.hhad_home == hhad["home"]
        and snap.hhad_draw == hhad["draw"]
        and snap.hhad_away == hhad["away"]
        and snap.ttg_odds_json == ttg
        and snap.crs_odds_json == crs
    )


def _parse_match(raw: dict) -> dict | None:
    had = parse_had(raw.get("had"))
    hhad = parse_hhad(raw.get("hhad"))
    ttg = parse_ttg(raw.get("ttg"))
    crs = parse_crs(raw.get("crs"))
    if not (had and hhad and ttg and crs):
        return None
    hafu = parse_hafu(raw.get("hafu"))
    times = [_update_time(x) for x in [raw.get("had") or {}, raw.get("hhad") or {}, raw.get("ttg") or {}, raw.get("crs") or {}]]
    snapshot_time = max(times)
    match_date = (raw.get("matchDate") or "").strip()
    match_time = (raw.get("matchTime") or "").strip()
    kickoff = None
    if match_date and match_time:
        try:
            kickoff = datetime.strptime(f"{match_date} {match_time}", "%Y-%m-%d %H:%M:%S")
        except ValueError:
            try:
                kickoff = datetime.strptime(f"{match_date} {match_time}", "%Y-%m-%d %H:%M")
            except ValueError:
                kickoff = None
    return {
        "jc_match_id": str(raw.get("matchId", "")),
        "match_num": raw.get("matchNumStr", ""),
        "home_team": raw.get("homeTeamAllName", ""),
        "away_team": raw.get("awayTeamAllName", ""),
        "kickoff": kickoff,
        "had": had,
        "hhad": hhad,
        "ttg": ttg,
        "crs": crs,
        "hafu": hafu,
        "snapshot_time": snapshot_time,
    }


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=None, help="kickoff 起始 YYYY-MM-DD；默认=今天")
    ap.add_argument("--end", default=None, help="kickoff 截止 YYYY-MM-DD；默认=start+7 天")
    ap.add_argument("--source", default="sporttery")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    start_dt = datetime.fromisoformat(args.start) if args.start else today
    if args.end:
        end_dt = datetime.fromisoformat(args.end)
    else:
        end_dt = start_dt + timedelta(days=7)
    end_dt = end_dt.replace(hour=11, minute=59, second=59, microsecond=0)
    start_dt = start_dt.replace(hour=12, minute=0, second=0, microsecond=0)

    print(f"拉取窗口: {start_dt} ~ {end_dt}")
    live = fetch_live()
    print(f"API 返回 {len(live)} 场")

    parsed: list[dict] = []
    for raw in live:
        p = _parse_match(raw)
        if not p:
            continue
        if not (p["kickoff"] and start_dt <= p["kickoff"] <= end_dt):
            continue
        parsed.append(p)
    print(f"过滤后窗口内 {len(parsed)} 场（HAD/HHAD/TTG/CRS 齐全）")

    async with async_session() as db:
        # 匹配本地 Match：优先 jc_match_id，其次 match_num + kickoff ± 12h
        inserted = 0
        updated = 0
        skipped_dup = 0
        skipped_unmatched = 0
        skipped_parse = 0
        matched_local_ids: list[int] = []
        for p in parsed:
            matched = None
            r1 = await db.execute(select(Match).where(Match.jc_match_id == p["jc_match_id"]))
            matched = r1.scalar_one_or_none()
            if not matched and p["match_num"]:
                r2 = await db.execute(
                    select(Match).where(
                        Match.match_num == p["match_num"],
                        Match.kickoff_time >= (p["kickoff"] - timedelta(hours=12)) if p["kickoff"] else True,
                        Match.kickoff_time <= (p["kickoff"] + timedelta(hours=12)) if p["kickoff"] else True,
                    )
                )
                matched = r2.scalars().first()
            if not matched:
                skipped_unmatched += 1
                print(f"  [unmatched] {p['match_num']:<10}{str(p['kickoff']):<22}{p['home_team']} vs {p['away_team']}  jc={p['jc_match_id']}")
                continue
            # 若没有 team_id，也尝试占位更新（home/away_team_name 已在 Match 中）
            if matched.id in matched_local_ids:
                continue
            matched_local_ids.append(matched.id)

            # 已有同 source 的快照且内容完全一致 → 跳过
            existing = (await db.execute(
                select(JczqPlayOddsSnapshot).where(
                    JczqPlayOddsSnapshot.match_id == matched.id,
                    JczqPlayOddsSnapshot.source == args.source,
                )
            )).scalars().all()
            if any(_snap_equal(s, p["had"], p["hhad"], p["ttg"], p["crs"]) for s in existing):
                matched_snaps = [s for s in existing if _snap_equal(s, p["had"], p["hhad"], p["ttg"], p["crs"])]
                target = matched_snaps[0]
                # 历史快照 hafu 为空而本次拉到 hafu → 仅回填 hafu 字段
                if p["hafu"] and not target.hafu_odds_json:
                    target.hafu_odds_json = p["hafu"]
                    updated += 1
                    print(f"  [hafu-backfill] {p['match_num']:<10}{str(p['kickoff']):<22}{p['home_team']} vs {p['away_team']} hafu_n={len(p['hafu'])}")
                    continue
                skipped_dup += 1
                print(f"  [dup]       {p['match_num']:<10}{str(p['kickoff']):<22}{p['home_team']} vs {p['away_team']}")
                continue

            snap = JczqPlayOddsSnapshot(
                match_id=matched.id,
                snapshot_time=p["snapshot_time"],
                source=args.source,
                had_home=p["had"]["home"],
                had_draw=p["had"]["draw"],
                had_away=p["had"]["away"],
                hhad_line=p["hhad"]["line"],
                hhad_home=p["hhad"]["home"],
                hhad_draw=p["hhad"]["draw"],
                hhad_away=p["hhad"]["away"],
                ttg_odds_json=p["ttg"],
                crs_odds_json=p["crs"],
                hafu_odds_json=p["hafu"],
            )
            db.add(snap)
            inserted += 1
            print(f"  [insert]    {p['match_num']:<10}{str(p['kickoff']):<22}{p['home_team']} vs {p['away_team']}  ttg_n={len(p['ttg'])} crs_n={len(p['crs'])}")

        if not args.dry_run:
            await db.commit()
        else:
            await db.rollback()

    print(f"\n完成: 写入 {inserted} 条，回填 hafu {updated} 条，dup 跳过 {skipped_dup}，本地无匹配 {skipped_unmatched}，dry_run={args.dry_run}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
