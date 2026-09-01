# -*- coding: utf-8 -*-
"""方案A进球腿特征挖掘：联赛/球队特性/盘口特征 × 命中率（8月全量，只读）。

为每个进球腿候选（判大+判小）提取特征，交叉统计命中率，
寻找可前瞻用于选场的高命中特征组合。
"""
import asyncio
import json
import os
import sys
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from sqlalchemy import select, text

from app.api.market_flow import _market_flow_query, _matchday_date
from app.db.database import async_session
from app.db.models import TeamSeasonStats
from app.predictor.models.ou_market import ou_market_from_rows

DIR_ZH = {"home": "主", "draw": "平", "away": "客"}


def _parse_ttg(raw):
    if raw is None:
        return {}
    data = raw
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except (ValueError, TypeError):
            return {}
    if not isinstance(data, dict):
        return {}
    out = {}
    for k, v in data.items():
        try:
            o = float(v)
        except (TypeError, ValueError):
            continue
        if o > 0:
            out[int(k)] = o
    return out


def _implied(odds_map):
    inv = {k: 1.0 / v for k, v in odds_map.items() if v and v > 0}
    if not inv:
        return None
    tot = sum(inv.values())
    return {k: v / tot for k, v in inv.items()}


def _ttg_pbig(ttg, thr):
    inv = {k: 1.0 / v for k, v in ttg.items() if v > 0}
    if not inv:
        return None
    tot = sum(inv.values())
    return sum(inv[k] / tot for k in inv if k > thr)


async def main():
    import os as _os
    _win = _os.getenv("WIN", "aug")  # aug=2026-08 / jul=2026-07
    if _win == "jul":
        start = datetime(2026, 7, 1, 12, 0, 0)
        end = datetime(2026, 8, 1, 12, 0, 0)
    else:
        start = datetime(2026, 8, 1, 12, 0, 0)
        end = datetime(2026, 9, 1, 12, 0, 0)
    async with async_session() as db:
        items, _os, _osm = await _market_flow_query(
            db, start=start, end=end, ou_tier="standard", ou_sm_tier="standard"
        )
        # SM 多线数据
        mids = [it["match_id"] for it in items]
        snap = {}
        snap_rows = (await db.execute(
            text("SELECT match_id, bookmaker, goal_line, over_odds, under_odds "
                 "FROM odds_snapshots WHERE match_id = ANY(:ids) ORDER BY match_id, snapshot_time"),
            {"ids": mids},
        )).all()
        for r in snap_rows:
            snap.setdefault(r[0], []).append(
                (r[1], r[2], r[3], r[4])
            )
        sm = {}
        for it in items:
            m = ou_market_from_rows(snap.get(it["match_id"]) or [])
            if m and m.get("lines"):
                sm[it["match_id"]] = {float(gl): info["p_big"] for gl, info in m["lines"].items()}

        # 球队攻守特性
        _team_avg = {}
        _tids = set()
        for it in items:
            if it.get("home_team_id"):
                _tids.add(int(it["home_team_id"]))
            if it.get("away_team_id"):
                _tids.add(int(it["away_team_id"]))
        if _tids:
            _tss = (await db.execute(
                select(TeamSeasonStats.team_id, TeamSeasonStats.goals_for,
                       TeamSeasonStats.goals_against, TeamSeasonStats.played)
                .where(TeamSeasonStats.team_id.in_(_tids), TeamSeasonStats.played > 0)
            )).all()
            _best = {}
            for _tid, _gf, _ga, _played in _tss:
                _tid = int(_tid)
                if _tid not in _best or _played > _best[_tid][2]:
                    _best[_tid] = (_gf, _ga, _played)
            for _tid, (_gf, _ga, _played) in _best.items():
                if _played and _played > 0:
                    _team_avg[_tid] = (_gf / _played, _ga / _played)

        def _home_attack(it):
            tid = it.get("home_team_id")
            if tid is None:
                return None
            avg = _team_avg.get(int(tid))
            return (avg[0] - avg[1] >= 0) if avg else None

        def _exp_total(it):
            h = _team_avg.get(int(it["home_team_id"])) if it.get("home_team_id") else None
            a = _team_avg.get(int(it["away_team_id"])) if it.get("away_team_id") else None
            if not h or not a:
                return None
            return (h[0] + a[1]) / 2 + (a[0] + h[1]) / 2

        # ===== 构建进球腿候选 + 特征 =====
        legs = []
        for it in items:
            settled = it.get("actual_outcome") is not None
            actual_tg = it.get("actual_total_goals")
            had = it.get("had_odds") or {}
            fav = it.get("fav")

            ttg_p_big = (it.get("ou_all") or {}).get("standard", {}).get("p_big")
            ttg_dir = ("over" if (ttg_p_big is not None and ttg_p_big >= 0.62)
                       else "under" if (ttg_p_big is not None and ttg_p_big <= 0.38) else None)
            sm_dir = ((it.get("ou_sm") or {}).get("tiers") or {}).get("standard")
            ttg_valid = ttg_dir if ttg_dir in ("over", "under") else None
            sm_valid = sm_dir if sm_dir in ("over", "under") else None
            if ttg_valid and sm_valid and ttg_valid != sm_valid:
                dirn = None
            else:
                dirn = ttg_valid or sm_valid
            if not dirn:
                continue
            ttg_odds = _parse_ttg(it.get("ttg_odds"))
            mkt = _implied(ttg_odds) if ttg_odds else None
            if not mkt:
                continue
            cand = [k for k in mkt if (k > 2.5 if dirn == "over" else k <= 2)]
            if dirn == "over":
                exp = _exp_total(it)
                if exp is not None and exp >= 3.6:
                    chosen = [5, 6, 7]
                elif exp is not None and exp >= 3.0:
                    chosen = [4, 5, 6]
                else:
                    chosen = [3, 4, 5]
                pick = [c for c in chosen if c in mkt]
                if len(pick) < 3:
                    extra = [c for c in cand if c not in pick]
                    extra.sort(key=lambda c: mkt[c], reverse=True)
                    pick = (pick + extra)[:3]
            else:
                pick = sorted(cand, key=mkt.get, reverse=True)[:3]
            if len(pick) < 3:
                continue
            inv = {k: 1.0 / v for k, v in ttg_odds.items() if v > 0}
            sml = sm.get(it["match_id"], {})
            hit = (actual_tg in pick) if settled and actual_tg is not None else None
            legs.append({
                "league": it.get("league_name"),
                "home": it.get("home_team"),
                "away": it.get("away_team"),
                "dir": dirn,
                "pick": "/".join(str(x) for x in pick),
                "tier": 0,
                "p_hat": round(sum(mkt[c] for c in pick), 4),
                "odds": round(1.0 / sum(inv[c] for c in pick), 4),
                "exp": _exp_total(it),
                "sm25": sml.get(2.5),
                "sm35": sml.get(3.5),
                "ttg25": _ttg_pbig(ttg_odds, 2.5),
                "ttg35": _ttg_pbig(ttg_odds, 3.5),
                "fav": fav,
                "home_attack": _home_attack(it),
                "hit": hit,
            })

        settled_legs = [l for l in legs if l["hit"] is not None]
        print(f"进球腿候选(已结算): {len(settled_legs)}")

        lines = []

        def cross(feature_fn, label, min_n=5):
            groups = defaultdict(list)
            for l in settled_legs:
                groups[feature_fn(l)].append(l)
            out = []
            for key, grp in sorted(groups.items(), key=lambda kv: -sum(1 for g in kv[1] if g["hit"]) / len(kv[1]) if len(kv[1]) else 0):
                if len(grp) < min_n:
                    continue
                h = sum(1 for g in grp if g["hit"])
                rate = h / len(grp)
                out.append(f"{str(key):<34} n={len(grp):<4} 命中={h:<3} p={rate:.3f}")
            lines.append(f"\n===== {label}（n≥{min_n}，按命中率降序） =====")
            lines.extend(out)

        cross(lambda l: f"{l['dir']}/{l['pick']}", "方向×选数", 3)
        cross(lambda l: l["league"], "联赛（全部）", 8)
        cross(lambda l: f"{l['dir']}|{l['league']}", "方向×联赛", 5)
        cross(lambda l: f"主队让球={l['fav']}|攻强={l['home_attack']}", "球队特性（让球×攻强守弱）", 5)
        cross(lambda l: f"{l['dir']}|主队让球={l['fav']}", "方向×让球方", 5)
        cross(lambda l: f"{l['dir']}|攻强={l['home_attack']}", "方向×主队攻强守弱", 5)

        # 盘口特征分箱
        def bucket(v, edges, labels):
            if v is None:
                return "None"
            for i, e in enumerate(edges):
                if v < e:
                    return labels[i]
            return labels[-1]

        edges = [0.3, 0.4, 0.5, 0.6]
        labs = ["<0.30", "0.30-0.40", "0.40-0.50", "0.50-0.60", "≥0.60"]
        cross(lambda l: f"{l['dir']}|sm35={bucket(l['sm35'], edges, labs)}", "方向×SM 3.5线", 5)
        cross(lambda l: f"{l['dir']}|sm25={bucket(l['sm25'], edges, labs)}", "方向×SM 2.5线", 5)
        cross(lambda l: f"{l['dir']}|exp={bucket(l['exp'], [3.0, 3.6], ['<3.0', '3.0-3.6', '≥3.6'])}", "方向×exp", 5)

        # 高命中画像（联赛×方向×选数 组合）
        cross(lambda l: f"{l['league']}|{l['dir']}|{l['pick']}", "联赛×方向×选数", 3)

        # ===== 联赛特征补充：各联赛平均进球（用实际结果） =====
        lines.append("\n===== 联赛实际进球风格（已结算场次） =====")
        by_league = defaultdict(list)
        for l in settled_legs:
            by_league[l["league"]].append(l)
        league_stats = []
        for lg, grp in by_league.items():
            if len(grp) < 8:
                continue
            # 需要 actual_tg，从 hit 判定只能知道是否命中…… 补充用外部数据不可行，这里给出判大/判小命中即可
        # 输出判大场次最多的联赛排行
        lines.append("（判大场次按联赛的命中率已在'方向×联赛'展示）")

        out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"_feature_report_{_win}.txt")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        print(f"报告已写入 {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
