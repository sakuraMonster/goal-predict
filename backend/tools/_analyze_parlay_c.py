# -*- coding: utf-8 -*-
"""方案C（2串1 = 1 半全场 + 1 胜平负）逐场选场分析：重建每日候选池，对比实际选择 vs 未选候选命中（只读）。

输出：
  1) 每个比赛日的已选组合（半全场腿 + 胜平负腿）+ 全部候选（含未选），标注命中
  2) 失败日枚举当日所有替代组合，找出"未选但命中"的可优化组合
  3) 汇总统计：
     - 串关 p_hit / 均赔 / ROI（仅已结算）
     - 半全场腿命中率：按 hafu_key（胜胜/负负/...）、p_hat 区间、赔率区间、联赛
     - 半全场腿深度拆解：半场方向命中 vs 全场方向命中
     - 胜平负腿命中率：按来源 ambiguous_had/had_upset、按 fallback
  4) 每日多串：当日全部合法组合，每日至少一中率（每日命中稳定性）
"""
import asyncio
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from app.api.market_flow import _market_flow_query, _matchday_date
from app.db.database import async_session

DIR_ZH = {"home": "主", "draw": "平", "away": "客"}
HAFU_ZH = {"hh": "胜胜", "hd": "胜平", "ha": "胜负", "dh": "平胜", "dd": "平平",
           "da": "平负", "ah": "负胜", "ad": "负平", "aa": "负负"}
_DIR_KEY = {"home": "h", "draw": "d", "away": "a"}
HAFU_HALF = {"h": "半主", "d": "半平", "a": "半客"}
HAFU_FULL = {"h": "全主", "d": "全平", "a": "全客"}
MIN_ODDS, MAX_ODDS = 4.0, 10.0


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


def _implied(odds_map):
    inv = {k: 1.0 / v for k, v in odds_map.items() if v and v > 0}
    if not inv:
        return None
    tot = sum(inv.values())
    return {k: v / tot for k, v in inv.items()}


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


def _same_match(a, b):
    if a.get("match_num") and b.get("match_num"):
        return a["match_num"] == b["match_num"]
    return a.get("kickoff_time") == b.get("kickoff_time")


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

        # ===== 重建每日候选（与生产 parlay-dir 一致） =====
        by_day = {}
        for it in items:
            d = by_day.setdefault(
                _matchday_date(it["kickoff_time"], match_num=it.get("match_num")),
                {"hafu": [], "had": []},
            )
            settled = it.get("actual_outcome") is not None
            actual_outcome = it.get("actual_outcome")
            pool = it.get("pool")
            had = it.get("had_odds") or {}

            # ---- 半全场腿：不限池，选市场隐含概率最高且赔率>=2.0 的选项（9选1） ----
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
                        half_hit = full_hit = None
                        if ahk is not None:
                            hd = _half_dir(it)
                            half_hit = (hd == "home") if key[0] == "h" else (hd == "away") if key[0] == "a" else (hd == "draw")
                            full_hit = (actual_outcome == "home") if key[1] == "h" else (actual_outcome == "away") if key[1] == "a" else (actual_outcome == "draw")
                        d["hafu"].append({
                            "match_num": it.get("match_num"),
                            "home_team": it.get("home_team"),
                            "away_team": it.get("away_team"),
                            "league_name": it.get("league_name"),
                            "kickoff_time": it["kickoff_time"],
                            "kind": "dir",
                            "source": "hafu",
                            "pick": HAFU_ZH[key],
                            "hafu_key": key,
                            "p_hat": round(ip[key], 4),
                            "odds": round(odd, 4),
                            "hit": hit,
                            "half_hit": half_hit,
                            "full_hit": full_hit,
                            "actual": (f"半{it.get('half_home_score')}-{it.get('half_away_score')} 全{it.get('actual_score')}"
                                       if settled and ahk is not None else None),
                        })

            # ---- 胜平负腿：模糊池正路 / 冷门池高置信冷门，HAD>=1.8 ----
            if pool in ("ambiguous", "upset"):
                fallback = False
                if pool == "upset":
                    direction = it.get("preferred_outcome")
                    if direction is None:
                        direction = it.get("cold_dir")
                        fallback = True
                    src = "had_upset"
                else:
                    direction = it.get("fav")
                    src = "ambiguous_had"
                if direction and direction in had and had.get(direction) and had[direction] >= 1.8:
                    ip_had = _implied(had) if had else None
                    p = ip_had.get(direction) if ip_had else None
                    d["had"].append({
                        "match_num": it.get("match_num"),
                        "home_team": it.get("home_team"),
                        "away_team": it.get("away_team"),
                        "league_name": it.get("league_name"),
                        "kickoff_time": it["kickoff_time"],
                        "kind": "dir",
                        "source": src,
                        "pick": DIR_ZH[direction],
                        "pref": direction,
                        "confidence": round(float(p), 4) if isinstance(p, (int, float)) else None,
                        "p_hat": round(float(p), 4) if isinstance(p, (int, float)) else round(float(had[direction]), 4),
                        "odds": round(had[direction], 4),
                        "fallback": fallback,
                        "hit": (actual_outcome == direction) if settled else None,
                        "actual": DIR_ZH.get(actual_outcome, actual_outcome) if settled else None,
                    })

        # ===== 逐日组合（与生产一致）+ 替代组合枚举 =====
        def _pl_odds(legs):
            o = 1.0
            for l in legs:
                o *= l["odds"]
            return o

        def _pl_hit(legs):
            if not all(l["hit"] is not None for l in legs):
                return None
            return all(l["hit"] for l in legs)

        out = []
        for matchday, grp in sorted(by_day.items()):
            hafu = sorted(grp["hafu"], key=lambda x: x["p_hat"], reverse=True)
            had = sorted(grp["had"], key=lambda x: x["p_hat"], reverse=True)
            if not hafu or not had:
                continue
            had_normal = [x for x in had if not x.get("fallback")]
            had_pool = had_normal if had_normal else had
            legs = None
            for a in hafu:
                b = next((x for x in had_pool if not _same_match(a, x)), None)
                if b is not None:
                    legs = [a, b]
                    break
            if legs is None:
                continue
            a, b = legs
            pl_odds = _pl_odds(legs)
            pl_hit = _pl_hit(legs)
            # 替代组合枚举：当日所有合法 (hafu × had_pool) 非同场
            alts = []
            for x in hafu:
                for y in had_pool:
                    if _same_match(x, y):
                        continue
                    alt_hit = _pl_hit([x, y])
                    alts.append({
                        "hafu": x, "had": y, "odds": _pl_odds([x, y]),
                        "hit": alt_hit,
                        "in_range": MIN_ODDS <= _pl_odds([x, y]) <= MAX_ODDS,
                    })
            out.append({
                "matchday": matchday,
                "hafu_all": hafu,
                "had_all": had,
                "had_pool": had_pool,
                "sel": legs,
                "pl_odds": pl_odds,
                "pl_hit": pl_hit,
                "in_range": MIN_ODDS <= pl_odds <= MAX_ODDS,
                "alts": alts,
            })

        # ===== 输出 =====
        lines = []
        total = fail = improvable = 0
        for row in out:
            md = row["matchday"]
            total += 1
            row_hit = row["pl_hit"]
            if row_hit is False:
                fail += 1
            sel = row["sel"]
            sel_key = (sel[0]["match_num"], sel[0]["hafu_key"], sel[1]["match_num"], sel[1]["pref"])
            unsel_hit_alt = [x for x in row["alts"]
                             if x["hit"] is True and (x["hafu"]["match_num"], x["hafu"]["hafu_key"],
                                                      x["had"]["match_num"], x["had"]["pref"]) != sel_key]
            flag = " [可优化]" if (row_hit is False and unsel_hit_alt) else ""
            if row_hit is False and unsel_hit_alt:
                improvable += 1
            a, b = sel
            lines.append(f"[{md}] 串关{'命中' if row_hit else '未中' if row_hit is False else '未结算'}"
                         f" 赔率={row['pl_odds']:.2f}{'' if row['in_range'] else ' (区间外)'}"
                         f" | 半全场: {a['home_team']}vs{a['away_team']} {a['pick']}@{a['odds']} p_hat={a['p_hat']}"
                         f" {'命中' if a['hit'] else '未中' if a['hit'] is False else '-'}"
                         f" | 胜平负: {b['home_team']}vs{b['away_team']} {b['pick']}@{b['odds']} p_hat={b['p_hat']}"
                         f" {'命中' if b['hit'] else '未中' if b['hit'] is False else '-'}{flag}")
            lines.append(f"   实际: 半场={a['actual'] or '-'}")
            # 当日所有候选（半全场）
            for x in row["hafu_all"]:
                mark = "【选】" if x == a else ""
                lines.append(f"  候选半全场{mark}: {x['home_team']}vs{x['away_team']} {x['pick']}@{x['odds']}"
                             f" p_hat={x['p_hat']} 半场方向={'命中' if x['half_hit'] else '未中' if x['half_hit'] is False else '-'}"
                             f" 全场方向={'命中' if x['full_hit'] else '未中' if x['full_hit'] is False else '-'}"
                             f" {'命中' if x['hit'] else '未中' if x['hit'] is False else '-'}")
            # 当日所有候选（胜平负）
            for x in row["had_all"]:
                mark = "【选】" if x == b else ""
                lines.append(f"  候选胜平负{mark}: {x['home_team']}vs{x['away_team']} {x['pick']}@{x['odds']}"
                             f" p_hat={x['p_hat']} src={x['source']}{'(兜底)' if x.get('fallback') else ''}"
                             f" {'命中' if x['hit'] else '未中' if x['hit'] is False else '-'}")
            if unsel_hit_alt:
                lines.append(f"  -- 当日存在未选且命中组合: {len(unsel_hit_alt)} 个 --")
                for x in unsel_hit_alt[:8]:
                    lines.append(f"     {x['hafu']['home_team']}vs{x['hafu']['away_team']} {x['hafu']['pick']}@{x['hafu']['odds']}"
                                 f" x {x['had']['home_team']}vs{x['had']['away_team']} {x['had']['pick']}@{x['had']['odds']}"
                                 f" 串关={x['odds']:.2f}{'' if x['in_range'] else '(区间外)'}")
            lines.append("")

        lines.append(f"===== 汇总: 串关={total} 未中={fail} 其中'当日存在未选且命中组合'={improvable} =====")

        # ===== 统计：已结算串关 =====
        rows_settled = [r for r in out if r["pl_hit"] is not None]
        n = len(rows_settled)
        hit = sum(1 for r in rows_settled if r["pl_hit"])
        av = sum(r["pl_odds"] for r in rows_settled) / n if n else 0
        lines.append(f"\n===== 串关统计（已结算） =====")
        lines.append(f"n={n} 命中={hit} p_hit={hit/n if n else 0:.4f} 均赔={av:.2f} ROI={av*hit/n - 1 if n else 0:+.4f}")
        inr = [r for r in rows_settled if r["in_range"]]
        if inr:
            ni, hi = len(inr), sum(1 for r in inr if r["pl_hit"])
            ai = sum(r["pl_odds"] for r in inr) / ni
            lines.append(f"赔率[4,10]内: n={ni} 命中={hi} p_hit={hi/ni:.4f} 均赔={ai:.2f} ROI={ai*hi/ni-1:+.4f}")

        # ===== 腿级统计 =====
        def _rate(lst):
            s = [x for x in lst if x["hit"] is not None]
            if not s:
                return 0, 0, 0.0
            h = sum(1 for x in s if x["hit"])
            return len(s), h, h / len(s)

        all_hafu = [x for r in out for x in r["hafu_all"]]
        all_had = [x for r in out for x in r["had_all"]]
        n_h, h_h, p_h = _rate(all_hafu)
        n_d, h_d, p_d = _rate(all_had)
        lines.append(f"\n===== 腿级命中率 =====")
        lines.append(f"半全场腿: n={n_h} 命中={h_h} p={p_h:.3f}")
        lines.append(f"胜平负腿: n={n_d} 命中={h_d} p={p_d:.3f}")

        # 半全场按 hafu_key
        lines.append("\n--- 半全场腿 按选项 ---")
        by_key = {}
        for x in all_hafu:
            by_key.setdefault(x["hafu_key"], []).append(x)
        for k, grp in sorted(by_key.items()):
            nn, hh, pp = _rate(grp)
            lines.append(f"  {HAFU_ZH[k]}({k}): n={nn} 命中={hh} p={pp:.3f}")

        # 半全场腿：半场方向 vs 全场方向拆解
        lines.append("\n--- 半全场腿 半场/全场方向命中拆解（已结算） ---")
        half_s = [x for x in all_hafu if x["half_hit"] is not None]
        if half_s:
            hh2 = sum(1 for x in half_s if x["half_hit"])
            lines.append(f"半场方向: n={len(half_s)} 命中={hh2} p={hh2/len(half_s):.3f}")
        full_s = [x for x in all_hafu if x["full_hit"] is not None]
        if full_s:
            hh3 = sum(1 for x in full_s if x["full_hit"])
            lines.append(f"全场方向: n={len(full_s)} 命中={hh3} p={hh3/len(full_s):.3f}")
        # 失败拆解：选项首字母=半场方向
        for r in out:
            pass
        hkey_s = [x for x in all_hafu if x["hit"] is not None]
        for hk in sorted({x["hafu_key"][0] for x in hkey_s}):
            grp = [x for x in hkey_s if x["hafu_key"][0] == hk]
            hs = sum(1 for x in grp if x["half_hit"])
            fs = sum(1 for x in grp if x["full_hit"])
            hitg = sum(1 for x in grp if x["hit"])
            lines.append(f"  首字母={HAFU_HALF[hk]}: n={len(grp)} 半场方向命中={hs} 全场方向命中={fs} 组合命中={hitg}")

        # 半全场腿按 p_hat / 赔率区间
        def _bucket(lst, key, edges, names):
            lines.append(f"\n--- 半全场腿 按{names[0]} ---")
            for i in range(len(edges) - 1):
                grp = [x for x in lst if edges[i] <= x[key] < edges[i + 1]]
                if not grp:
                    continue
                nn, hh, pp = _rate(grp)
                lines.append(f"  {names[1].format(edges[i], edges[i + 1])}: n={nn} 命中={hh} p={pp:.3f}")

        _bucket(all_hafu, "p_hat", [0, 0.30, 0.35, 0.40, 0.50, 1.01], ["p_hat区间", "{:.2f}~{:.2f}"])
        _bucket(all_hafu, "odds", [2.0, 3.0, 4.0, 5.0, 8.0, 100.0], ["赔率区间", "{:.0f}~{:.0f}"])

        # 半全场按联赛
        lines.append("\n--- 半全场腿 按联赛（n>=5） ---")
        by_league = {}
        for x in all_hafu:
            by_league.setdefault(x["league_name"], []).append(x)
        for lk, grp in sorted(by_league.items(), key=lambda kv: -len(kv[1])):
            nn, hh, pp = _rate(grp)
            if nn >= 5:
                lines.append(f"  {lk}: n={nn} 命中={hh} p={pp:.3f}")

        # 胜平负按来源
        lines.append("\n--- 胜平负腿 按来源 ---")
        by_src = {}
        for x in all_had:
            by_src.setdefault(x["source"], []).append(x)
        for sk, grp in sorted(by_src.items()):
            nn, hh, pp = _rate(grp)
            lines.append(f"  {sk}: n={nn} 命中={hh} p={pp:.3f}")
        fb = [x for x in all_had if x.get("fallback")]
        nf, hf, pf = _rate(fb)
        lines.append(f"  兜底(cold_dir): n={nf} 命中={hf} p={pf:.3f}")
        # 按赔率区间
        _bucket(all_had, "odds", [1.8, 2.5, 3.5, 5.0, 100.0], ["赔率区间", "{:.1f}~{:.1f}"])

        # ===== 每日多串：当日全部合法组合，任一中 =====
        lines.append("\n===== 每日多串模拟（当日全部合法组合，至少一中） =====")
        day_ok = 0
        day_n = 0
        multi_ok = 0
        multi_n = 0
        for r in out:
            alts = r["alts"]
            settled_alts = [x for x in alts if x["hit"] is not None]
            if not settled_alts:
                continue
            day_n += 1
            if any(x["hit"] is True for x in settled_alts):
                day_ok += 1
            if len(alts) >= 2:
                multi_n += 1
                if any(x["hit"] is True for x in alts):
                    multi_ok += 1
        if day_n:
            lines.append(f"每日全部合法组合任一中: {day_ok}/{day_n} = {day_ok/day_n:.3f}")
        if multi_n:
            lines.append(f"仅当日>=2组合的天: 任一中 {multi_ok}/{multi_n} = {multi_ok/multi_n:.3f}")
        base = sum(1 for r in out if r["pl_hit"] is True and r["pl_hit"] is not None)
        bset = [r for r in out if r["pl_hit"] is not None]
        lines.append(f"基准(每日1串): {base}/{len(bset)} = {base/len(bset) if bset else 0:.3f}")

        out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"_parlay_c_report_{_win}.txt")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        print("report ->", out_path)


if __name__ == "__main__":
    asyncio.run(main())
