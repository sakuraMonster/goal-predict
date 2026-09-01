# -*- coding: utf-8 -*-
"""方案A 3串1 参数化优化：7+8月合并网格搜索（只读）。

维度：
  pick_mode  : curr=exp分层 / all345=判大固定345 / mild=exp>=3.6才升档
  dir_mode   : all=现状(favorite_hafu+ambiguous) / ambi=只用ambiguous_had
  gate       : none=不过滤 / phat45=进球腿p_hat>=0.45 / phat50=进球腿p_hat>=0.50
  combo      : none=无约束 / difflg=进球腿与方向腿不同联赛
输出：每个组合 n/p_hit/均赔/ROI，按 p_hit 与 ROI 排序。
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
    start = datetime(2026, 7, 1, 12, 0, 0)
    end = datetime(2026, 9, 1, 12, 0, 0)
    async with async_session() as db:
        items, _os, _osm = await _market_flow_query(
            db, start=start, end=end, ou_tier="standard", ou_sm_tier="standard"
        )
        # 球队攻守
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

        def _exp_total(it):
            h = _team_avg.get(int(it["home_team_id"])) if it.get("home_team_id") else None
            a = _team_avg.get(int(it["away_team_id"])) if it.get("away_team_id") else None
            if not h or not a:
                return None
            return (h[0] + a[1]) / 2 + (a[0] + h[1]) / 2

        # ===== 按 pick_mode 构建每日候选 =====
        def build(pick_mode):
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

                # 进球腿
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
                if dirn:
                    ttg_odds = _parse_ttg(it.get("ttg_odds"))
                    mkt = _implied(ttg_odds) if ttg_odds else None
                    if mkt:
                        cand = [k for k in mkt if (k > 2.5 if dirn == "over" else k <= 2)]
                        if dirn == "over":
                            if pick_mode == "all345":
                                chosen = [3, 4, 5]
                            elif pick_mode == "mild":
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
                                # 判大分级：主队让球+攻强守弱 0 > 攻强守弱 1 > 主队让球 2 > 其他 3
                                home_fav = it.get("fav") == "home"
                                tid = it.get("home_team_id")
                                attack = None
                                if tid is not None:
                                    avg = _team_avg.get(int(tid))
                                    attack = (avg[0] - avg[1] >= 0) if avg else None
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
                                "league": it.get("league_name"),
                                "kickoff_time": it["kickoff_time"],
                                "pick": "/".join(str(x) for x in pick),
                                "dir": dirn,
                                "tier": tier,
                                "p_hat": round(sum(mkt[c] for c in pick), 4),
                                "odds": round(1.0 / sum(inv[c] for c in pick), 4),
                                "hit": (actual_tg in pick) if settled and actual_tg is not None else None,
                            })

                # 方向腿
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
                                "league": it.get("league_name"),
                                "kickoff_time": it["kickoff_time"],
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
                            "league": it.get("league_name"),
                            "kickoff_time": it["kickoff_time"],
                            "source": "ambiguous_had",
                            "pick": DIR_ZH[fav],
                            "p_hat": round(float(fav_ip), 4),
                            "odds": round(had[fav], 4),
                            "hit": (actual_outcome == fav) if settled else None,
                        })
            return by_day

        def _same_match(a, b):
            if a.get("match_num") and b.get("match_num"):
                return a["match_num"] == b["match_num"]
            return a.get("kickoff_time") == b.get("kickoff_time")

        def _find_pair(avail):
            """返回第一个'两场不同联赛'的进球腿 pair"""
            for i in range(len(avail)):
                for j in range(i + 1, len(avail)):
                    if avail[i]["league"] != avail[j]["league"] and not _same_match(avail[i], avail[j]):
                        return (avail[i], avail[j])
            return None

        def run(by_day, dir_mode, gate, combo):
            results = []
            for matchday, grp in sorted(by_day.items()):
                ds = sorted(grp["dirs"], key=lambda x: x["p_hat"], reverse=True)
                if dir_mode == "ambi":
                    ds = [d for d in ds if d["source"] == "ambiguous_had"]
                if not ds:
                    continue
                gs = sorted(grp["goals"], key=lambda x: (x.get("tier", 0), -x["p_hat"]))
                # gate
                if gate == "phat45":
                    gs = [g for g in gs if g["p_hat"] >= 0.45]
                elif gate == "phat50":
                    gs = [g for g in gs if g["p_hat"] >= 0.50]
                if len(gs) < 2:
                    continue
                # ladder：三级降级 严格(三腿异联赛)→宽松(两进球腿异联赛)→无约束兜底
                if combo == "ladder":
                    chosen = None
                    for d0 in ds:
                        avail_ = [g for g in gs if not _same_match(g, d0)]
                        avail_s = [g for g in avail_ if g["league"] != d0["league"]]
                        pair = _find_pair(avail_s)
                        if pair:
                            chosen = (pair[0], pair[1], d0)
                            break
                    if chosen is None:
                        d0 = ds[0]
                        avail_ = [g for g in gs if not _same_match(g, d0)]
                        pair = _find_pair(avail_)
                        if pair:
                            chosen = (pair[0], pair[1], d0)
                        elif len(avail_) >= 2:
                            chosen = (avail_[0], avail_[1], d0)
                    if chosen:
                        legs = list(chosen)
                        for lg in legs:
                            lg["md"] = matchday
                        results.append(legs)
                    continue
                # 选方向腿（每串一条，先试最优）
                done = False
                for d0 in ds:
                    avail = [g for g in gs if not _same_match(g, d0)]
                    if combo == "difflg":
                        avail = [g for g in avail if g["league"] != d0["league"]]
                        # 两进球腿也要求不同联赛
                        keep = []
                        for i, g in enumerate(avail):
                            for g2 in avail[i + 1:]:
                                if g["league"] != g2["league"] and not _same_match(g, g2):
                                    keep.append((g, g2))
                        if keep:
                            g1, g2 = keep[0]
                            legs = [g1, g2, d0]
                            for lg in legs:
                                lg["md"] = matchday
                            results.append(legs)
                            done = True
                            break
                    elif combo == "difflg_loose":
                        # 宽松：仅要求两进球腿不同联赛（方向腿不约束）
                        keep = []
                        for i, g in enumerate(avail):
                            for g2 in avail[i + 1:]:
                                if g["league"] != g2["league"] and not _same_match(g, g2):
                                    keep.append((g, g2))
                        if keep:
                            g1, g2 = keep[0]
                            legs = [g1, g2, d0]
                            for lg in legs:
                                lg["md"] = matchday
                            results.append(legs)
                            done = True
                            break
                    else:
                        if len(avail) >= 2:
                            legs = avail[:2] + [d0]
                            for lg in legs:
                                lg["md"] = matchday
                            results.append(legs)
                            done = True
                            break
                # 无方向腿可配则跳过
            return results

        # ===== 网格搜索 =====
        lines = []
        best_by_phit = []
        best_by_roi = []
        for pick_mode in ("curr", "all345", "mild"):
            by_day = build(pick_mode)
            for dir_mode in ("all", "ambi"):
                for gate in ("none", "phat45", "phat50"):
                    for combo in ("none", "difflg", "difflg_loose", "ladder"):
                        legs_list = run(by_day, dir_mode, gate, combo)
                        settled = [L for L in legs_list if all(l["hit"] is not None for l in L)]
                        if not settled:
                            continue
                        n = len(settled)
                        hit = sum(1 for L in settled if all(l["hit"] for l in L))
                        ph = hit / n
                        av = sum(__import__("functools").reduce(lambda a, b: a * b, (l["odds"] for l in L)) for L in settled) / n
                        roi = av * ph - 1
                        cov = len(legs_list)
                        # 按月分解（matchday 前 7 位 = YYYY-MM）
                        months = {}
                        for L in legs_list:
                            if all(l["hit"] is not None for l in L):
                                m = L[0]["md"][:7]
                                s = months.setdefault(m, [0, 0])
                                s[0] += 1
                                if all(l["hit"] for l in L):
                                    s[1] += 1
                        rec = {
                            "cfg": f"{pick_mode}|{dir_mode}|{gate}|{combo}",
                            "n": n, "hit": hit, "ph": ph, "av": av, "roi": roi, "cov": cov,
                            "months": months,
                        }
                        best_by_phit.append(rec)
                        best_by_roi.append(rec)

        best_by_phit.sort(key=lambda r: -r["ph"])
        best_by_roi.sort(key=lambda r: -r["roi"])
        lines.append("===== 方案A 3串1 优化网格（7+8月合并，全部组合按 p_hit 排序） =====")
        lines.append("cfg = 选数|方向腿|选场门槛|组合约束  [月份: 出串/命中]")
        for r in best_by_phit:
            md = " ".join(f"{m}:{s[0]}/{s[1]}" for m, s in sorted(r["months"].items()))
            lines.append(f"{r['cfg']:<38} n={r['n']:<3} 命中={r['hit']:<3} p_hit={r['ph']:.3f} 均赔={r['av']:.2f} ROI={r['roi']:+.3f} 出串={r['cov']}天 [{md}]")
        lines.append("\n===== 按 ROI 排序 =====")
        for r in best_by_roi[:10]:
            lines.append(f"{r['cfg']:<38} n={r['n']:<3} 命中={r['hit']:<3} p_hit={r['ph']:.3f} 均赔={r['av']:.2f} ROI={r['roi']:+.3f} 出串={r['cov']}天")
        out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_optimize_report.txt")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        print(f"报告已写入 {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
