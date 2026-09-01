# -*- coding: utf-8 -*-
"""方案D 测算：1进球数(判大3选) + 1半全场 + 1方向 的3串1，串关赔率>=5（只读）。

设计：
  - 进球腿：判大 3选（盘口3.5分层选数，同方案B），gate 排序
  - 半全场腿：不限池，选市场隐含概率最高且赔率>=2.0 的选项（9选1，同方案C）
  - 方向腿：favorite池→半全场hh/aa(>=1.8) / ambiguous池→fav正路(>=1.8)（同方案A）
  - 组合：三腿不同场，三级降级（strict三腿异联赛→loose→fallback），串关赔率>=5
输出：8月 出串/命中/p_hit/均赔/ROI + 逐日明细
"""
import asyncio
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from sqlalchemy import select

from app.api.market_flow import _market_flow_query, _matchday_date
from app.db.database import async_session
from app.db.models import TeamSeasonStats

DIR_ZH = {"home": "主", "draw": "平", "away": "客"}
HAFU_ZH = {"hh": "胜胜", "hd": "胜平", "ha": "胜负", "dh": "平胜", "dd": "平平",
           "da": "平负", "ah": "负胜", "ad": "负平", "aa": "负负"}
_DIR_KEY = {"home": "h", "draw": "d", "away": "a"}
MIN_ODDS = 5.0
MAX_ODDS = 15.0


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


def _parse_hafu(raw):
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
    return {k: float(v) for k, v in data.items() if v and float(v) > 0}


def _half_dir(it):
    hh, ha = it.get("half_home_score"), it.get("half_away_score")
    if not isinstance(hh, int) or not isinstance(ha, int):
        return None
    return "home" if hh > ha else "away" if hh < ha else "draw"


def _actual_hafu_key(it):
    hd = _half_dir(it)
    fo = it.get("actual_outcome")
    if hd is None or fo not in ("home", "draw", "away"):
        return None
    return _DIR_KEY[hd] + _DIR_KEY[fo]


async def main():
    _win = os.getenv("WIN", "aug")
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

        def _home_attack_strong(it):
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

        # ===== 重建三类腿候选 =====
        by_day = {}
        for it in items:
            d = by_day.setdefault(
                _matchday_date(it["kickoff_time"], match_num=it.get("match_num")),
                {"goals": [], "hafu": [], "dirs": []},
            )
            settled = it.get("actual_outcome") is not None
            actual_tg = it.get("actual_total_goals")
            actual_outcome = it.get("actual_outcome")
            had = it.get("had_odds") or {}
            pool = it.get("pool")

            # ---- 进球腿（判大3选） ----
            _ttg_all = it.get("ou_all") or {}
            ttg_strict = (_ttg_all.get("strict") or {}).get("direction")
            ttg_dir = ttg_strict if ttg_strict in ("over", "under") else (_ttg_all.get("standard") or {}).get("direction")
            _sm_tiers = ((it.get("ou_sm") or {}).get("tiers") or {})
            sm_dir = _sm_tiers.get("strict")
            if sm_dir not in ("over", "under"):
                sm_dir = _sm_tiers.get("standard")
            ttg_valid = ttg_dir if ttg_dir in ("over", "under") else None
            sm_valid = sm_dir if sm_dir in ("over", "under") else None
            if ttg_valid and sm_valid and ttg_valid != sm_valid:
                dirn = None
            else:
                dirn = ttg_valid or sm_valid
            gate = "standard"
            if dirn and (ttg_strict == dirn or _sm_tiers.get("strict") == dirn):
                gate = "strict"
            if dirn == "over":
                ttg_odds = _parse_ttg(it.get("ttg_odds"))
                mkt = _implied(ttg_odds) if ttg_odds else None
                if mkt:
                    cand = [k for k in mkt if k > 2.5]
                    # 方案B选数：盘口3.5分层
                    _p35 = None
                    _l35 = ((it.get("ou_sm") or {}).get("lines") or {}).get("3.5")
                    if _l35:
                        _p35 = _l35.get("p_big")
                    if _p35 is None:
                        exp = _exp_total(it)
                        if exp is not None and exp >= 3.6:
                            chosen = [5, 6, 7]
                        elif exp is not None and exp >= 3.0:
                            chosen = [4, 5, 6]
                        else:
                            chosen = [3, 4, 5]
                    elif _p35 >= 0.45:
                        chosen = [4, 5, 6]
                    else:
                        chosen = [3, 4, 5]
                    pick = [c for c in chosen if c in mkt]
                    if len(pick) < 3:
                        extra = [c for c in cand if c not in pick]
                        extra.sort(key=lambda c: mkt[c], reverse=True)
                        pick = (pick + extra)[:3]
                    if len(pick) >= 3:
                        inv = {k: 1.0 / v for k, v in ttg_odds.items() if v > 0}
                        tier = 0
                        home_fav = it.get("fav") == "home"
                        attack = _home_attack_strong(it)
                        if home_fav and attack is True:
                            tier = 0
                        elif attack is True:
                            tier = 1
                        elif home_fav:
                            tier = 2
                        else:
                            tier = 3
                        d["goals"].append({
                            "match_num": it.get("match_num"),
                            "home_team": it.get("home_team"),
                            "away_team": it.get("away_team"),
                            "league_name": it.get("league_name"),
                            "kickoff_time": it["kickoff_time"],
                            "kind": "goals",
                            "pick": "/".join(str(x) for x in pick),
                            "gate": gate,
                            "tier": tier,
                            "p_hat": round(sum(mkt[c] for c in pick), 4),
                            "odds": round(1.0 / sum(inv[c] for c in pick), 4),
                            "hit": (actual_tg in pick) if settled and actual_tg is not None else None,
                            "actual": str(actual_tg) if settled and actual_tg is not None else None,
                        })

            # ---- 半全场腿（不限池，隐含最高且>=2.0，9选1，同方案C） ----
            hafu = _parse_hafu(it.get("hafu_odds"))
            if hafu:
                ip = _implied(hafu)
                if ip:
                    cand = [(k, v) for k, v in hafu.items() if v >= 2.0]
                    if cand:
                        key = max(cand, key=lambda kv: ip.get(kv[0], 0.0))[0]
                        odd = hafu[key]
                        ahk = _actual_hafu_key(it)
                        hit = (ahk == key) if (settled and ahk is not None) else None
                        d["hafu"].append({
                            "match_num": it.get("match_num"),
                            "home_team": it.get("home_team"),
                            "away_team": it.get("away_team"),
                            "league_name": it.get("league_name"),
                            "kickoff_time": it["kickoff_time"],
                            "kind": "hafu",
                            "pick": HAFU_ZH[key],
                            "hafu_key": key,
                            "p_hat": round(ip[key], 4),
                            "odds": round(odd, 4),
                            "hit": hit,
                        })

            # ---- 方向腿（同方案A：favorite→hh/aa >=1.8；ambiguous→fav >=1.8） ----
            if pool == "favorite":
                fav = it.get("fav")
                hafu = _parse_hafu(it.get("hafu_odds"))
                if fav in ("home", "away") and hafu:
                    key = "hh" if fav == "home" else "aa"
                    odd = hafu.get(key)
                    ip = _implied(hafu) if hafu else None
                    if odd and odd >= 1.8 and ip and ip.get(key):
                        hd = _half_dir(it)
                        hit = None
                        if settled:
                            hit = (hd == "home" and actual_outcome == "home") if fav == "home" else (hd == "away" and actual_outcome == "away")
                            if hd is None:
                                hit = None
                        d["dirs"].append({
                            "match_num": it.get("match_num"),
                            "home_team": it.get("home_team"),
                            "away_team": it.get("away_team"),
                            "league_name": it.get("league_name"),
                            "kickoff_time": it["kickoff_time"],
                            "kind": "dir",
                            "source": "favorite_hafu",
                            "pick": "胜胜" if key == "hh" else "负负",
                            "p_hat": round(ip[key], 4),
                            "odds": round(odd, 4),
                            "hit": hit,
                        })
            elif pool == "ambiguous":
                fav = it.get("fav")
                fav_ip = it.get("fav_ip")
                if fav and fav in had and had.get(fav) and had[fav] >= 1.8 and isinstance(fav_ip, (int, float)):
                    d["dirs"].append({
                        "match_num": it.get("match_num"),
                        "home_team": it.get("home_team"),
                        "away_team": it.get("away_team"),
                        "league_name": it.get("league_name"),
                        "kickoff_time": it["kickoff_time"],
                        "kind": "dir",
                        "source": "ambiguous_had",
                        "pick": DIR_ZH[fav],
                        "p_hat": round(float(fav_ip), 4),
                        "odds": round(had[fav], 4),
                        "hit": (actual_outcome == fav) if settled else None,
                    })

        def _same_match(a, b):
            if a.get("match_num") and b.get("match_num"):
                return a["match_num"] == b["match_num"]
            return a.get("kickoff_time") == b.get("kickoff_time")

        def _pl_odds(legs):
            o = 1.0
            for l in legs:
                o *= l["odds"]
            return o

        def _pl_phat(legs):
            p = 1.0
            for l in legs:
                p *= l["p_hat"]
            return p

        def _pl_hit(legs):
            if not all(l["hit"] is not None for l in legs):
                return None
            return all(l["hit"] for l in legs)

        # ===== 组合：三腿不同场，三级降级 =====
        def _build(grp):
            gs = sorted(grp["goals"], key=lambda x: (0 if x.get("gate") == "strict" else 1, x.get("tier", 0), -x["p_hat"]))
            hs = sorted(grp["hafu"], key=lambda x: -x["p_hat"])
            ds = sorted(grp["dirs"], key=lambda x: -x["p_hat"])
            if not gs or not hs or not ds:
                return None, None
            def _diff(a, b):
                return a.get("match_num") != b.get("match_num") or a.get("kickoff_time") != b.get("kickoff_time")
            # 先找三腿全异联赛（strict）
            best = None
            level = "strict"
            for g in gs:
                for h in hs:
                    if _same_match(g, h):
                        continue
                    for dd in ds:
                        if _same_match(g, dd) or _same_match(h, dd):
                            continue
                        legs = [g, h, dd]
                        if _pl_odds(legs) < MIN_ODDS:
                            continue
                        if len({g.get("league_name"), h.get("league_name"), dd.get("league_name")}) == 3:
                            if best is None or _pl_phat(legs) > _pl_phat(best):
                                best = legs
            if best is not None:
                return best, "strict"
            # loose：仅进球腿与方向腿异联赛（或半全场与进球异联赛）
            for g in gs:
                for h in hs:
                    if _same_match(g, h):
                        continue
                    for dd in ds:
                        if _same_match(g, dd) or _same_match(h, dd):
                            continue
                        legs = [g, h, dd]
                        if _pl_odds(legs) < MIN_ODDS:
                            continue
                        if g.get("league_name") != dd.get("league_name"):
                            if best is None or _pl_phat(legs) > _pl_phat(best):
                                best = legs
            if best is not None:
                return best, "loose"
            # fallback：无约束
            for g in gs:
                for h in hs:
                    if _same_match(g, h):
                        continue
                    for dd in ds:
                        if _same_match(g, dd) or _same_match(h, dd):
                            continue
                        legs = [g, h, dd]
                        if _pl_odds(legs) < MIN_ODDS:
                            continue
                        if best is None or _pl_phat(legs) > _pl_phat(best):
                            best = legs
            if best is not None:
                return best, "fallback"
            return None, None

        lines = []
        rows = []
        lines.append(f"===== 方案D（1进球+1半全场+1方向，串关>=5倍）{_win} =====")
        for matchday, grp in sorted(by_day.items()):
            legs, level = _build(grp)
            if legs is None:
                continue
            h = _pl_hit(legs)
            rows.append((h, _pl_odds(legs), legs, level, matchday))
        settled = [r for r in rows if r[0] is not None]
        n = len(settled)
        hit = sum(1 for h, _o, _l, _lv, _md in settled if h)
        av = sum(o for _h, o, _l, _lv, _md in settled) / n if n else 0
        lines.append(f"\n出串={len(rows)}天 已结算={n} 命中={hit} p_hit={hit/n if n else 0:.4f} 均赔={av:.2f} ROI={av*hit/n-1 if n else 0:+.4f}")
        for h, o, legs, level, matchday in rows:
            line = f"[{matchday}] 串={'命中' if h else '未中' if h is False else '未结算'} 赔率={o:.2f} combo={level}"
            for lg in legs:
                line += f" | {lg['kind']}: {lg['home_team']}vs{lg['away_team']} {lg['pick']}@{lg['odds']} p_hat={lg['p_hat']}"
            lines.append(line)

        # 腿级命中
        lines.append(f"\n===== 腿级命中率 =====")
        for kind in ("goals", "hafu", "dir"):
            legs_all = [lg for r in rows for lg in r[2] if lg["kind"] == kind]
            s = [x for x in legs_all if x["hit"] is not None]
            if s:
                hh = sum(1 for x in s if x["hit"])
                lines.append(f"  {kind}: n={len(s)} 命中={hh} p={hh/len(s):.3f}")

        # 方案D 与 方案B 对比
        out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"_plan_d_{_win}.txt")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        print("report ->", out_path)


if __name__ == "__main__":
    asyncio.run(main())
