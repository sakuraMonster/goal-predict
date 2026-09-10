"""从竞彩网 getFixedBonusV1.qry 拉取已完场比赛的固定奖金（收盘赔率），写入 jczq_play_odds_snapshots。

数据源: https://webapi.sporttery.cn/gateway/uniform/football/getFixedBonusV1.qry?matchId=<竞彩matchId>
- 按本地 matches 表（jc_match_id 已映射、且已完场）逐个拉取
- 各玩法历史列表取最后一条作为收盘赔率
- 解析 had/hhad/ttg/crs 四种玩法（半全场 hafu 暂不入库）

用法:
    python tools/fetch_market_flow_odds.py --start 2026-08-19 --end 2026-08-20 --dry-run
    python tools/fetch_market_flow_odds.py --jc-match-ids 2040932,2040933
"""
import argparse
import asyncio
import re
import sys
from datetime import datetime

sys.path.insert(0, ".")
from dotenv import load_dotenv

load_dotenv()

import requests
from sqlalchemy import select

from app.db.database import async_session
from app.db.models import JczqPlayOddsSnapshot, Match

FIXED_BONUS_API = "https://webapi.sporttery.cn/gateway/uniform/football/getFixedBonusV1.qry"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.sporttery.cn/jc/zqdz/index.html",
    "Origin": "https://www.sporttery.cn",
}

CRS_RE = re.compile(r"^s(\d{2})s(\d{2})$")


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
    return {"line": _line(obj.get("goalLine")), "home": h, "draw": d, "away": a}


def parse_ttg(obj):
    if not isinstance(obj, dict):
        return {}
    out = {}
    for k in ("s0", "s1", "s2", "s3", "s4", "s5", "s6", "s7"):
        v = _num(obj.get(k))
        if v is not None:
            out[str(int(k[1]))] = v
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


HAFU_KEYS = ("hh", "hd", "ha", "dh", "dd", "da", "ah", "ad", "aa")


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
    return datetime.now()


def fetch_fixed_bonus(jc_match_id: str):
    r = requests.get(FIXED_BONUS_API, params={"matchId": jc_match_id}, headers=HEADERS, timeout=20)
    r.raise_for_status()
    data = r.json()
    if str(data.get("errorCode")) != "0":
        raise RuntimeError(f"errorCode={data.get('errorCode')} msg={data.get('errorMessage')}")
    return data.get("value") or {}


def parse_snapshot(value: dict):
    oh = value.get("oddsHistory") or {}
    had_list = oh.get("hadList") or []
    hhad_list = oh.get("hhadList") or []
    ttg_list = oh.get("ttgList") or []
    crs_list = oh.get("crsList") or []
    hafu_list = oh.get("hafuList") or []

    # 进球链路只需 TTG/CRS 完整即可；HAD/HHAD 允许缺失（竞彩对强弱悬殊场次不售胜平负盘）
    if not (ttg_list and crs_list):
        return None
    ttg = parse_ttg(ttg_list[-1])
    crs = parse_crs(crs_list[-1])
    if not (ttg and crs):
        return None

    had = parse_had(had_list[-1]) if had_list else None
    hhad = parse_hhad(hhad_list[-1]) if hhad_list else None
    hafu = parse_hafu(hafu_list[-1]) if hafu_list else None
    # 收盘时间取最早开售池的最后一笔更新时间
    snapshot_time = None
    for lst in (had_list, hhad_list, ttg_list, crs_list):
        if lst:
            snapshot_time = _update_time(lst[-1])
            break
    if snapshot_time is None:
        return None

    return {
        "had": had,
        "hhad": hhad,
        "ttg": ttg,
        "crs": crs,
        "hafu": hafu,
        "snapshot_time": snapshot_time,
    }


def _has_direction_data(snap) -> bool:
    """快照是否携带可用方向定价（HAD 三向完整 或 HHAD 有盘口线）"""
    return bool(snap.had_home and snap.had_draw and snap.had_away) or snap.hhad_line is not None


def _can_insert(existing, had, hhad) -> bool:
    """防降级：新快照缺某方向盘但历史已有 → 视为该盘未开售/抓取遗漏，禁止用缺盘快照覆盖。"""
    if not had and any(s.had_home and s.had_draw and s.had_away for s in existing):
        return False
    if not hhad and any(s.hhad_line is not None for s in existing):
        return False
    return True


def _snap_equal(snap, had, hhad, ttg, crs):
    """内容相等判定。had/hhad 允许 None：某列两边都缺视为一致；都有值时精确比较。"""

    def _seg(cur, new):
        if new is None:
            return all(v is None for v in cur)
        return all(
            (c is None and n is None)
            or (c is not None and n is not None and abs(float(c) - float(n)) < 1e-6)
            for c, n in zip(cur, new)
        )

    had_cur = (snap.had_home, snap.had_draw, snap.had_away)
    had_new = tuple(had[k] for k in ("home", "draw", "away")) if had else None
    hh_cur = (snap.hhad_line, snap.hhad_home, snap.hhad_draw, snap.hhad_away)
    hh_new = tuple(hhad[k] for k in ("line", "home", "draw", "away")) if hhad else None
    return (
        _seg(had_cur, had_new)
        and _seg(hh_cur, hh_new)
        and snap.ttg_odds_json == ttg
        and snap.crs_odds_json == crs
    )


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=None, help="kickoff_time 下界 YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS")
    ap.add_argument("--end", default=None, help="kickoff_time 上界")
    ap.add_argument("--jc-match-ids", default=None, help="逗号分隔的竞彩 matchId，直接拉取指定场次")
    ap.add_argument("--source", default="sporttery")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    def _dt(s):
        if not s:
            return None
        s = s.strip()
        if len(s) == 10:
            s += " 00:00:00"
        return datetime.fromisoformat(s)

    async with async_session() as db:
        stmt = select(Match).where(Match.jc_match_id.isnot(None))
        if args.jc_match_ids:
            ids = [x.strip() for x in args.jc_match_ids.split(",") if x.strip()]
            stmt = stmt.where(Match.jc_match_id.in_(ids))
        else:
            stmt = stmt.where(Match.home_score.isnot(None), Match.away_score.isnot(None))
            start_dt, end_dt = _dt(args.start), _dt(args.end)
            if start_dt:
                stmt = stmt.where(Match.kickoff_time >= start_dt)
            if end_dt:
                stmt = stmt.where(Match.kickoff_time <= end_dt)
        stmt = stmt.order_by(Match.kickoff_time.asc())
        matches = (await db.execute(stmt)).scalars().all()

    print(f"待拉取 {len(matches)} 场")

    inserted = 0
    updated = 0
    skipped_fetch = 0
    skipped_parse = 0
    skipped_dup = 0

    async with async_session() as db:
        for m in matches:
            jc = str(m.jc_match_id)
            label = f"match_id={m.id} jc={jc} ({m.match_num or ''})"
            try:
                value = fetch_fixed_bonus(jc)
            except Exception as e:
                skipped_fetch += 1
                print(f"  [fetch-fail] {label}: {e}")
                continue

            snap_data = parse_snapshot(value)
            if not snap_data:
                skipped_parse += 1
                print(f"  [parse-fail] {label}: 数据不完整或未开售")
                continue

            had, hhad, ttg, crs = snap_data["had"], snap_data["hhad"], snap_data["ttg"], snap_data["crs"]
            hafu = snap_data["hafu"]
            snapshot_time = snap_data["snapshot_time"]

            existing_r = await db.execute(
                select(JczqPlayOddsSnapshot).where(
                    JczqPlayOddsSnapshot.match_id == m.id,
                    JczqPlayOddsSnapshot.source == args.source,
                )
            )
            existing = existing_r.scalars().all()
            matched = [s for s in existing if _snap_equal(s, had, hhad, ttg, crs)]
            if matched:
                # 历史快照 hafu 为空而本次拉取到 hafu → 仅回填 hafu 字段
                target = matched[0]
                if hafu and not target.hafu_odds_json:
                    target.hafu_odds_json = hafu
                    updated += 1
                    print(f"  [hafu-backfill] {label} hafu_n={len(hafu)}")
                    continue
                skipped_dup += 1
                print(f"  [dup] {label} 已存在相同快照")
                continue

            # 防降级：新快照方向盘缺失但历史快照已有（盘未开售/抓取遗漏）→ 不写入
            if not _can_insert(existing, had, hhad):
                skipped_dup += 1
                print(f"  [no-downgrade] {label} 方向盘数据缺失，跳过缺盘快照")
                continue

            snap = JczqPlayOddsSnapshot(
                match_id=m.id,
                snapshot_time=snapshot_time,
                source=args.source,
                had_home=(had or {}).get("home"),
                had_draw=(had or {}).get("draw"),
                had_away=(had or {}).get("away"),
                hhad_line=(hhad or {}).get("line"),
                hhad_home=(hhad or {}).get("home"),
                hhad_draw=(hhad or {}).get("draw"),
                hhad_away=(hhad or {}).get("away"),
                ttg_odds_json=ttg,
                crs_odds_json=crs,
                hafu_odds_json=hafu,
            )
            db.add(snap)
            inserted += 1
            print(f"  [insert] {label} snapshot_time={snapshot_time} ttg_n={len(ttg)} crs_n={len(crs)}")

        if not args.dry_run:
            await db.commit()
        else:
            await db.rollback()

    print(f"\n完成: 写入 {inserted} 条，回填 hafu {updated} 条，拉取失败 {skipped_fetch}，解析失败 {skipped_parse}，重复 {skipped_dup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
