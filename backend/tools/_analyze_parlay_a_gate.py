# -*- coding: utf-8 -*-
"""方案A gate门控分级选场模拟：strict档进球腿优先，standard档降级（只读）。

对比排序模式（组合逻辑均用生产三级降级）：
  base: tier优先（当前生产排序）
  gate: 判小 > strict判大 > standard判大 > tier > p_hat（新）
输出: 各模式 出串/命中/p_hit/均赔/ROI + 逐日命中对照
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
MODE = os.getenv("MODE", "curr")


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

        from app.predictor.models.ou_direction import DEFAULT_TIER
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

        # ===== 重建每日候选（含 gate 门控档位） =====
        by_day = {}
        for it in items:
            d = by_day.setdefault(
                _matchday_date(it["kickoff_time"], match_num=it.get("match_num")),
                {"goals": [], "dirs": []},
            )
            settled = it.get("actual_outcome") is not None
            actual_tg = it.get("actual_total_goals")
            actual_outcome = it.get("actual_outcome")
            had = it.get("had_odds") or {}
            pool = it.get("pool")

            # 门控：各盘口 strict/standard 方向（strict优先退standard）
            ttg_all = it.get("ou_all") or {}
            ttg_strict = (ttg_all.get("strict") or {}).get("direction")
            ttg_std = (ttg_all.get("standard") or {}).get("direction")
            sm_tiers = ((it.get("ou_sm") or {}).get("tiers") or {})
            sm_strict = sm_tiers.get("strict")
            sm_std = sm_tiers.get("standard")
            ttg_valid = ttg_strict if ttg_strict in ("over", "under") else ttg_std if ttg_std in ("over", "under") else None
            sm_valid = sm_strict if sm_strict in ("over", "under") else sm_std if sm_std in ("over", "under") else None
            if ttg_valid and sm_valid and ttg_valid != sm_valid:
                dirn = None
            else:
                dirn = ttg_valid or sm_valid
            gate = "strict" if (dirn and (ttg_strict == dirn or sm_strict == dirn)) else "standard"
            _go = os.getenv("GOALS_ONLY", "")
            if _go == "over" and dirn != "over":
                dirn = None  # 仅大球：剔除判小

            if dirn:
                ttg_odds = _parse_ttg(it.get("ttg_odds"))
                mkt = _implied(ttg_odds) if ttg_odds else None
                if mkt:
                    cand = [k for k in mkt if (k > 2.5 if dirn == "over" else k <= 2)]
                    if dirn == "over":
                        if _go == "over":
                            # 方案B：盘口 3.5 线分层（SM O/U P(>=4球)，缺线回退 exp），与生产一致
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
                        elif MODE == "all345":
                            chosen = [3, 4, 5]
                        elif MODE == "345plus456":
                            exp = _exp_total(it)
                            chosen = [4, 5, 6] if (exp is not None and exp >= 3.6) else [3, 4, 5]
                        else:
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
                    if len(pick) >= 3:
                        inv = {k: 1.0 / v for k, v in ttg_odds.items() if v > 0}
                        tier = 0
                        if dirn == "over":
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
                            "pick": "/".join(str(x) for x in pick),
                            "dir": dirn,
                            "gate": gate,
                            "tier": tier,
                            "p_hat": round(sum(mkt[c] for c in pick), 4),
                            "odds": round(1.0 / sum(inv[c] for c in pick), 4),
                            "hit": (actual_tg in pick) if settled and actual_tg is not None else None,
                            "actual": str(actual_tg) if settled and actual_tg is not None else None,
                        })

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
                            "actual": (f"半{it.get('half_home_score')}-{it.get('half_away_score')} 全{it.get('actual_score')}"
                                       if settled and hd is not None else None),
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
                        "actual": DIR_ZH.get(actual_outcome, actual_outcome) if settled else None,
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

        def _pl_hit(legs):
            if not all(l["hit"] is not None for l in legs):
                return None
            return all(l["hit"] for l in legs)

        def _build(grp, sort_mode):
            """返回生产三级降级组合 (legs, level)。sort_mode: base/gate"""
            gs = grp["goals"]
            if sort_mode == "gate":
                gs = sorted(gs, key=lambda x: (
                    0 if x["dir"] == "under" else 1,
                    0 if x.get("gate") == "strict" else 1,
                    x.get("tier", 0), -x["p_hat"],
                ))
            else:
                gs = sorted(gs, key=lambda x: (x.get("tier", 0), -x["p_hat"]))
            ds = sorted(grp["dirs"], key=lambda x: x["p_hat"], reverse=True)
            if len(gs) < 2 or not ds:
                return None, None
            d0 = ds[0]
            def _not_same(g, dd):
                return not (
                    (g["match_num"] and dd.get("match_num") and g["match_num"] == dd.get("match_num"))
                    or (not (g["match_num"] and dd.get("match_num")) and g["kickoff_time"] == dd.get("kickoff_time"))
                )
            avail = [g for g in gs if _not_same(g, d0)]
            if len(avail) < 2:
                return None, None
            def _find_pair(avail_):
                for i in range(len(avail_)):
                    for j in range(i + 1, len(avail_)):
                        if avail_[i].get("league_name") != avail_[j].get("league_name"):
                            return (avail_[i], avail_[j])
                return None
            chosen = None
            level = "strict"
            for dd in ds:
                aa_s = [g for g in gs if _not_same(g, dd) and g.get("league_name") != dd.get("league_name")]
                pair = _find_pair(aa_s)
                if pair:
                    chosen = (pair[0], pair[1], dd)
                    break
            if chosen is None:
                pair = _find_pair(avail)
                if pair:
                    level = "loose"
                    chosen = (pair[0], pair[1], d0)
                else:
                    level = "fallback"
                    chosen = (avail[0], avail[1], d0)
            return list(chosen), level

        def _build_noleg(grp, sort_mode):
            """无约束组合（方案B当前default）：avail[:2] + d0"""
            gs = grp["goals"]
            if sort_mode == "gate":
                gs = sorted(gs, key=lambda x: (
                    0 if x["dir"] == "under" else 1,
                    0 if x.get("gate") == "strict" else 1,
                    x.get("tier", 0), -x["p_hat"],
                ))
            else:
                gs = sorted(gs, key=lambda x: (x.get("tier", 0), -x["p_hat"]))
            ds = sorted(grp["dirs"], key=lambda x: x["p_hat"], reverse=True)
            if len(gs) < 2 or not ds:
                return None, None
            d0 = ds[0]
            def _not_same(g, dd):
                return not (
                    (g["match_num"] and dd.get("match_num") and g["match_num"] == dd.get("match_num"))
                    or (not (g["match_num"] and dd.get("match_num")) and g["kickoff_time"] == dd.get("kickoff_time"))
                )
            avail = [g for g in gs if _not_same(g, d0)]
            if len(avail) < 2:
                return None, None
            return avail[:2] + [d0], "default"

        lines = []
        for sort_mode, name in [("base", "base排序"), ("gate", "gate排序")]:
            for combo_mode, cname in [("ladder", "三级降级组合"), ("noleg", "无约束组合")]:
                rows = []
                levels = {}
                for matchday, grp in sorted(by_day.items()):
                    if combo_mode == "ladder":
                        legs, lv = _build(grp, sort_mode)
                    else:
                        legs, lv = _build_noleg(grp, sort_mode)
                    if legs is None:
                        continue
                    levels[lv] = levels.get(lv, 0) + 1
                    h = _pl_hit(legs)
                    rows.append((h, _pl_odds(legs)))
                settled = [r for r in rows if r[0] is not None]
                n = len(settled)
                hit = sum(1 for h, _ in settled if h)
                ph = hit / n if n else 0
                av = sum(o for _, o in settled) / n if n else 0
                lv_s = " ".join(f"{k}={v}" for k, v in sorted(levels.items()))
                lines.append(f"{name}|{cname}: 出串={len(rows)}天 已结算={n} 命中={hit} p_hit={ph:.4f} 均赔={av:.2f} ROI={av*ph-1:+.4f} | {lv_s}")

        # gate 分级的候选结构
        lines.append("\n===== 进球腿候选 gate 分布 =====")
        gate_dist = {"strict": 0, "standard": 0}
        for matchday, grp in sorted(by_day.items()):
            for g in grp["goals"]:
                if g["dir"] == "over":
                    gate_dist[g["gate"]] += 1
        lines.append(f"判大候选: strict={gate_dist['strict']} standard={gate_dist['standard']}")

        # 选中腿 gate 分布 + 逐日对照
        lines.append("\n===== 选中判大腿 gate 分布 + 逐日命中对照 =====")
        sel_gate = {"base": {"strict": 0, "standard": 0}, "gate": {"strict": 0, "standard": 0}}
        day_lines = []
        for matchday, grp in sorted(by_day.items()):
            lv_base = _build(grp, "base")
            lv_gate = _build(grp, "gate")
            if lv_base[0] is None:
                continue
            hb = _pl_hit(lv_base[0])
            hg = _pl_hit(lv_gate[0])
            gb = [g for g in lv_base[0] if g.get("dir") == "over"]
            gg = [g for g in lv_gate[0] if g.get("dir") == "over"]
            for g in gb:
                sel_gate["base"][g["gate"]] += 1
            for g in gg:
                sel_gate["gate"][g["gate"]] += 1
            diff = "" if hb == hg else f" ★差异(base={hb}, gate={hg})"
            day_lines.append(f"  {matchday}: base={'中' if hb else '未中' if hb is False else '-'}"
                             f" gate={'中' if hg else '未中' if hg is False else '-'}{diff}")
        lines.extend(day_lines)
        for k in ("base", "gate"):
            s = sel_gate[k]
            lines.append(f"选中判大腿[{k}]: strict={s['strict']} standard={s['standard']}")

        out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"_parlay_a_gate_{MODE}_{_win}.txt")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        print("report ->", out_path)


if __name__ == "__main__":
    asyncio.run(main())
