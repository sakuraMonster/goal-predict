"""修正未知联赛的 match 数据，并用 Model C 重新预测"""
import asyncio
import json
import sys
import os
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from collections import defaultdict
from sqlalchemy import select, update
from app.db.database import async_session
from app.db.models import Match, Prediction, League
from app.predictor.models.model_c import ModelC
from app.predictor.features_b import FeatureEngineerB
from app.predictor.snap import snap_top2

# 球队 -> 联赛 映射（基于已知球队名）
TEAM_LEAGUE_MAP = {
    # 韩K 球队
    "金泉尚武": 6, "大田市民": 6, "首尔FC": 6, "蔚山现代": 6,
    "浦项制铁": 6, "全北现代": 6, "光州FC": 6, "济州SK": 6,
    "仁川联": 6, "富川FC": 6, "安养FC": 6, "江原FC": 6,
    # 美职联 球队
    "圣迭戈FC": 12, "达拉斯": 12, "圣何塞": 12, "洛城银河": 12,
    # 芬超（被误标为欧冠的）
    "库奥皮奥": 10, "瓦萨": 10,
}


async def main():
    async with async_session() as db:
        # ── 步骤1：查询并修正 match 的 league_id ──
        print("=== 步骤1：查询当前联赛信息 ===")
        result = await db.execute(select(League.id, League.name_zh))
        league_names = {row[0]: row[1] for row in result}

        # 获取 07-25 ~ 07-27 所有 match
        dates = ["2026-07-25", "2026-07-26", "2026-07-27"]
        all_matches = []
        for day_str in dates:
            d_start = datetime.strptime(day_str, "%Y-%m-%d")
            query_start = d_start.replace(hour=12)
            query_end = (d_start + timedelta(days=1)).replace(hour=12)
            result = await db.execute(
                select(Match).where(
                    Match.kickoff_time >= query_start,
                    Match.kickoff_time < query_end,
                ).order_by(Match.kickoff_time)
            )
            for m in result.scalars().all():
                all_matches.append(m)

        # 找出需要修正的
        fixes = []
        for m in all_matches:
            if m.home_team_name in TEAM_LEAGUE_MAP or m.away_team_name in TEAM_LEAGUE_MAP:
                home_lg = TEAM_LEAGUE_MAP.get(m.home_team_name)
                away_lg = TEAM_LEAGUE_MAP.get(m.away_team_name)
                new_lg = home_lg or away_lg
                if new_lg and m.league_id != new_lg:
                    old_name = league_names.get(m.league_id, f"ID={m.league_id}")
                    new_name = league_names.get(new_lg, f"ID={new_lg}")
                    fixes.append((m, old_name, new_name, new_lg))

        print(f"需要修正的比赛: {len(fixes)} 场")
        for m, old, new, new_lg in fixes:
            print(f"  match_id={m.id} | {m.home_team_name} vs {m.away_team_name} | {old} -> {new}")

        # ── 步骤2：更新 match 和 prediction 的 league_id ──
        print("\n=== 步骤2：更新 league_id ===")
        updated_match_ids = []
        updated_league_ids = {}
        for m, old, new, new_lg in fixes:
            # 更新 match
            await db.execute(
                update(Match).where(Match.id == m.id).values(league_id=new_lg)
            )
            # 更新 prediction
            await db.execute(
                update(Prediction).where(Prediction.match_id == m.id).values(league_id=new_lg)
            )
            updated_match_ids.append(m.id)
            updated_league_ids[m.id] = new_lg
            print(f"  已更新 match_id={m.id} -> league_id={new_lg} ({new})")

        await db.commit()
        print(f"共更新 {len(fixes)} 场比赛和对应预测记录")

        # ── 步骤3：重新提取特征并运行 Model C 预测 ──
        print("\n=== 步骤3：Model C 重预测 ===")
        feat_engine = FeatureEngineerB(db)
        model_c = ModelC()

        # 对所有 31 场重新预测（因为有些联赛变了，其预测参数也会变）
        results = []
        total, failed = 0, 0
        for m in all_matches:
            total += 1
            # 获取最新 league_id（可能已被修正）
            result = await db.execute(select(Match.league_id).where(Match.id == m.id))
            current_lg_id = result.scalar_one()
            league_name = league_names.get(current_lg_id, "未知")

            # 获取实际结果
            pred_result = await db.execute(
                select(Prediction).where(Prediction.match_id == m.id)
            )
            pred = pred_result.scalar_one_or_none()
            actual_total = pred.actual_total_goals if pred else None
            actual_score = pred.actual_score if pred else None

            try:
                features_df = await feat_engine.extract_features(m.id)
                if features_df.empty:
                    failed += 1
                    continue
                features = features_df.iloc[0].to_dict()
            except Exception as e:
                print(f"  [FAIL] match_id={m.id} 特征提取: {e}")
                failed += 1
                continue

            result_c = model_c.predict(features, league_name)
            expected_goals = result_c["expected_goals"]
            snap = snap_top2(expected_goals)
            detail = result_c["detail"]

            # 写回 prediction 表
            if pred:
                pred.expected_goals_c = expected_goals
                pred.snap_top2_c = snap

            hit = actual_total in snap if actual_total is not None else None
            status = "HIT" if hit else "MISS" if hit is False else "N/A"
            was_fixed = "(已修正)" if m.id in updated_match_ids else ""

            print(f"  {m.home_team_name or '?'} vs {m.away_team_name or '?'} | "
                  f"联赛={league_name} {was_fixed} | "
                  f"实际={actual_score}({actual_total}球) | lam={expected_goals} SNAP={snap} | {status}")

            results.append({
                "match_id": m.id,
                "home": m.home_team_name,
                "away": m.away_team_name,
                "league": league_name,
                "actual_score": actual_score,
                "actual_total": actual_total,
                "expected_goals": expected_goals,
                "snap_top2": snap,
                "hit": hit,
                "detail": detail,
                "was_fixed": m.id in updated_match_ids,
            })

        await db.commit()

        # ── 步骤4：汇总输出 ──
        settled = [r for r in results if r["hit"] is not None]
        hit_count = sum(1 for r in settled if r["hit"])
        miss_count = sum(1 for r in settled if not r["hit"])

        print("\n" + "=" * 70)
        print(f"修正后 Model C 预测结果: {hit_count}/{len(settled)} = {hit_count/len(settled)*100:.1f}%")
        print("=" * 70)

        # 按联赛分组
        by_league = defaultdict(lambda: {"hit": [], "miss": []})
        for r in settled:
            key = "hit" if r["hit"] else "miss"
            by_league[r["league"]][key].append(r)

        league_order = ["韩K", "美职联", "挪超", "瑞典超", "芬超", "巴甲", "欧冠"]
        for lg in league_order:
            if lg not in by_league:
                continue
            entries = by_league[lg]
            hc = len(entries["hit"])
            mc = len(entries["miss"])
            total_lg = hc + mc
            acc = hc / total_lg * 100 if total_lg else 0
            fixed_mark = " [刚修正]" if any(r.get("was_fixed") for r in entries["hit"] + entries["miss"]) else ""
            print(f"\n--- {lg}  {hc}/{total_lg} = {acc:.1f}%{fixed_mark} ---")

            for r in entries["miss"]:
                d = r["detail"]
                extra = "  ***低分规则***" if d.get("low_score_applied") else ""
                print(f"  MISS | {r['home']:<8s} vs {r['away']:<8s} | {r['actual_score']}({r['actual_total']}球) | lam={r['expected_goals']:.2f} SNAP={r['snap_top2']} | GL={d['goal_line']:.1f} calib={d['calib']:.3f} s={d['strength_adj']:+.3f} f={d['form_adj']:+.3f} dp={d['drop_adj']:+.3f}{extra}")

            for r in entries["hit"]:
                print(f"  HIT  | {r['home']:<8s} vs {r['away']:<8s} | {r['actual_total']}球 | lam={r['expected_goals']:.2f} SNAP={r['snap_top2']}")

        print(f"\n修正后的 prediction 记录已更新到数据库。")
        print(f"总成功: {total - failed}/{total}, 失败: {failed}")


if __name__ == "__main__":
    asyncio.run(main())
