"""情报标记对「选场」的增量 A/B 回测（近2个月）。

做法：容器内同时加载『线上旧引擎』(app.api.market_flow) 与『新引擎』(/tmp/mf_new.py)，
对同一窗口、同一 DB 各跑一遍方案A/D/C，比较 picks/stats 与方向腿命中。
不触碰生产容器文件、不改 DB、绕过 Redis 缓存（use_cache=False）。
运行：docker exec -w /app -e PYTHONIOENCODING=utf-8 ricking-backend-1 python /tmp/ab.py
"""
import sys
import asyncio
import functools
import importlib.util
from datetime import datetime, timedelta

sys.path.insert(0, "/app")

import app.api.market_flow as old  # noqa: E402

spec = importlib.util.spec_from_file_location("mf_new", "/tmp/mf_new.py")
new = importlib.util.module_from_spec(spec)
spec.loader.exec_module(new)

from app.db.database import async_session  # noqa: E402

old._market_flow_query = functools.partial(old._market_flow_query, use_cache=False)
new._market_flow_query = functools.partial(new._market_flow_query, use_cache=False)

START = "2026-08-11"
END = "2026-10-11"

SCHEMES = [
    ("方案A(全方向)", "market_flow_parlay_recommend", {}),
    ("方案D(进球半全场)", "market_flow_parlay_d_recommend", {"min_odds": 5.0, "max_odds": 15.0}),
    ("方案C(方向二串)", "market_flow_parlay_dir_recommend", {"min_odds": 4.0, "max_odds": 10.0}),
]


async def call(mod, fname, kw):
    async with async_session() as db:
        fn = getattr(mod, fname)
        return await fn(db=db, start_date=START, end_date=END, model_version=None, source=None, **kw)


def show(tag, res):
    s = res.get("stats") or {}
    print(f"  {tag}: n={s.get('n')} hit={s.get('hit')} p_hit={s.get('p_hit')} "
          f"roi={s.get('roi')} avg_odds={s.get('avg_odds')}")


def hr(legs):
    return f"{sum(1 for l in legs if l['hit'])}/{len(legs)}" if legs else "0/0"


async def main():
    for name, fname, kw in SCHEMES:
        print("=" * 72)
        print(name)
        try:
            r_old = await call(old, fname, kw)
        except Exception as e:
            print("  baseline ERROR:", repr(e))
            continue
        try:
            r_new = await call(new, fname, kw)
        except Exception as e:
            print("  new ERROR:", repr(e))
            continue
        show("baseline", r_old)
        show("new     ", r_new)

        do = {p.get("matchday"): p for p in r_old["picks"]}
        dn = {p.get("matchday"): p for p in r_new["picks"]}
        keys = sorted(set(do) | set(dn))
        chg = [k for k in keys if (do.get(k) or {}).get("hit") != (dn.get(k) or {}).get("hit")]
        up = [k for k in chg if (dn.get(k) or {}).get("hit") is True]
        dn_ = [k for k in chg if (dn.get(k) or {}).get("hit") is False]
        print(f"  出串日 baseline={len(do)} new={len(dn)}  命中变化日={len(chg)} (+{len(up)}/-{len(dn_)})")
        for k in chg:
            po, pn = do.get(k), dn.get(k)
            print(f"    {k}: baseline_hit={po and po.get('hit')}  new_hit={pn and pn.get('hit')}")

        legs = [lg for p in r_new["picks"] for lg in p["legs"]
                if lg.get("kind") == "dir" and lg.get("hit") is not None]
        w = [lg for lg in legs if lg.get("draw_warn")]
        nw = [lg for lg in legs if not lg.get("draw_warn")]
        print(f"  方向腿(new) 命中: 命中情报规则 {hr(w)}  未命中 {hr(nw)}")
        legs_o = [lg for p in r_old["picks"] for lg in p["legs"]
                  if lg.get("kind") == "dir" and lg.get("hit") is not None]
        print(f"  方向腿(baseline) 命中: {hr(legs_o)}")

    await leg_level()


def _parse_hafu(raw):
    import json
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            return {}
    return {k: float(v) for k, v in raw.items() if v and float(v) > 0} if isinstance(raw, dict) else {}


async def leg_level():
    print("=" * 72)
    print("方向腿候选级（全部候选，非仅选中）  —  标记是否携带信息")
    start = datetime.fromisoformat(START).replace(hour=12)
    end = (datetime.fromisoformat(END) + timedelta(days=1)).replace(hour=11, minute=59, second=59)
    async with async_session() as db:
        items, _, _ = await new._market_flow_query(db, start=start, end=end)

    amb_w, amb_nw, fav_w, fav_nw = [], [], [], []
    for it in items:
        if it.get("actual_outcome") is None:
            continue
        warn = bool(it.get("draw_intel"))
        fav = it.get("fav")
        had = it.get("had_odds") or {}
        pool = it.get("pool")
        # 胜平负（模糊池 fav 单选，HAD>=1.8）
        if pool == "ambiguous" and fav in had and had.get(fav) and had[fav] >= 1.8:
            h = (it.get("actual_outcome") == fav)
            (amb_w if warn else amb_nw).append(h)
        # 正路（半全场 胜胜/负负，>=1.8）
        if pool == "favorite" and fav in ("home", "away"):
            hafu = _parse_hafu(it.get("hafu_odds"))
            key = "hh" if fav == "home" else "aa"
            if hafu.get(key) and hafu[key] >= 1.8:
                hd = it.get("half_home_score")
                ha = it.get("half_away_score")
                if isinstance(hd, int) and isinstance(ha, int):
                    h = (hd > ha and it["actual_outcome"] == "home") if fav == "home" \
                        else (hd < ha and it["actual_outcome"] == "away")
                    (fav_w if warn else fav_nw).append(h)

    def line(tag, w, nw):
        f = lambda x: f"{sum(x)}/{len(x)} ({sum(x)/len(x)*100:.0f}%)" if x else "0/0"
        print(f"  {tag:<22} 命中情报规则 {f(w):<18} 未命中 {f(nw)}")

    line("模糊池 fav(胜平负)", amb_w, amb_nw)
    line("正路池 半全场胜胜负负", fav_w, fav_nw)


asyncio.run(main())
