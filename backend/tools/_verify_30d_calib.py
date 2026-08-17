"""近30天 联赛级 Model C λc 长期偏差验证（只读）
判断命中率低联赛是「长期系统性低估」还是「三天异常样本」。
"""
import asyncio
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, and_
from sqlalchemy.orm import joinedload

from app.db.database import async_session
from app.db.models import Prediction, Match

BEIJING_TZ = timezone(timedelta(hours=8))

# Model C 联赛 calib 参数（与 model_c.py LEAGUE_PARAMS 保持一致）
CALIB = {
    "default": 0.95,
    "英超": 0.942, "西甲": 0.923, "德甲": 0.991, "意甲": 0.891, "法甲": 0.963,
    "日职联": 0.880, "瑞典超": 1.011, "芬超": 0.900, "挪超": 1.022,
    "美职联": 1.050, "巴甲": 0.912, "韩K": 0.947, "日乙": 0.920, "韩K2": 0.930,
    "德乙": 1.225, "荷乙": 1.210, "英冠": 0.992, "葡超": 1.040, "法乙": 1.030,
}


def judge_snap_hit(actual_total, snap):
    if not snap:
        return None
    return actual_total in list(snap)


async def main():
    now = datetime.now(BEIJING_TZ).replace(tzinfo=None)
    since = now - timedelta(days=30)

    async with async_session() as db:
        result = await db.execute(
            select(Prediction)
            .options(
                joinedload(Prediction.match).joinedload(Match.league),
            )
            .where(and_(Prediction.kickoff_time >= since, Prediction.kickoff_time < now))
            .order_by(Prediction.kickoff_time)
        )
        preds = list(result.unique().scalars().all())

        settled = [p for p in preds if p.actual_home_score is not None]
        print(f"近30天窗口: {since:%m-%d %H:%M} ~ {now:%m-%d %H:%M}")
        print(f"预测记录 {len(preds)} 场，已结算 {len(settled)} 场\n")

        # 联赛聚合
        agg = defaultdict(lambda: {"n": 0, "act": 0, "lc": 0, "hit": 0, "has_c": 0})
        for p in settled:
            ln = p.league.name_zh if p.league else "未知"
            a = agg[ln]
            tg = (p.actual_home_score or 0) + (p.actual_away_score or 0)
            a["n"] += 1
            a["act"] += tg
            if p.expected_goals_c is not None:
                a["lc"] += p.expected_goals_c
                a["has_c"] += 1
                if judge_snap_hit(tg, p.snap_top2_c):
                    a["hit"] += 1

        print("=" * 110)
        print(f"{'联赛':<8}{'calib':>7}{'场数':>5}{'实际':>7}{'λc':>7}{'偏差':>7}  {'C命中率':>10}  判定")
        print("=" * 110)

        # 按样本量排序输出
        rows = []
        for ln, a in agg.items():
            if a["n"] < 3:  # 样本过小不纳入判定
                continue
            act = a["act"] / a["n"]
            lc = a["lc"] / a["has_c"] if a["has_c"] else 0
            bias = act - lc
            hit_rate = a["hit"] / a["has_c"] * 100 if a["has_c"] else 0
            calib = CALIB.get(ln, CALIB["default"])
            # 判定：偏差>0.5 且样本>=5 为显著低估；偏差<-0.3 为高估
            if a["n"] >= 5 and bias > 0.5:
                verdict = "长期低估"
            elif a["n"] >= 5 and bias < -0.3:
                verdict = "长期高估"
            elif bias > 0.3:
                verdict = "略低估"
            elif bias < -0.3:
                verdict = "略高估"
            else:
                verdict = "基本准确"
            rows.append((ln, calib, a["n"], act, lc, bias, hit_rate, verdict))

        rows.sort(key=lambda x: -x[2])  # 按场数降序
        for ln, calib, n, act, lc, bias, hit_rate, verdict in rows:
            print(f"{ln:<8}{calib:>7.3f}{n:>5}{act:>7.2f}{lc:>7.2f}{bias:>+7.2f}  {hit_rate:>9.1f}%  {verdict}")

        # 汇总：calib 分组偏差
        print("\n" + "=" * 110)
        print("按 calib 分组（近30天样本量加权）:")
        groups = defaultdict(lambda: {"n": 0, "act": 0, "lc": 0})
        for ln, a in agg.items():
            if a["n"] < 3:
                continue
            calib = CALIB.get(ln, CALIB["default"])
            key = "calib<1(压低估)" if calib < 1 else ("calib≈1" if calib < 1.05 else "calib>1(放大)")
            g = groups[key]
            g["n"] += a["n"]
            g["act"] += a["act"]
            g["lc"] += a["lc"]
        for key in ["calib<1(压低估)", "calib≈1", "calib>1(放大)"]:
            g = groups[key]
            if g["n"]:
                print(f"  {key:<16} n={g['n']:>3}  实际{g['act']/g['n']:.2f}  λc{g['lc']/g['n']:.2f}  偏差{g['act']/g['n']-g['lc']/g['n']:+.2f}")


if __name__ == "__main__":
    asyncio.run(main())
