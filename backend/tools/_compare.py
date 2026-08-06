"""新模型 vs 旧模型：进球数预测命中率对比"""
import urllib.request, json
from collections import defaultdict

def fetch_review(date):
    url = f'http://localhost:8008/api/predictions/review/daily?date={date}'
    resp = urllib.request.urlopen(url, timeout=30)
    return json.loads(resp.read())

# 旧模型数据（之前分析记录）
OLD_DATA = {
    'total': 27,
    'errors': {
        15473: ('全北现代 vs 首尔FC', 1.77, 0, +1.77),
        15474: ('浦项制铁 vs 金泉尚武', 1.30, 1, +0.30),
        15472: ('江原FC vs 富川FC', 4.64, 3, +1.64),
        15478: ('TPS图尔库 vs 玛丽港', 3.11, 3, +0.11),
        15479: ('赫根 vs 卡尔马', 3.75, 2, +1.75),
        15480: ('腓特烈斯塔 vs 桑纳菲', 1299.14, 1, +1298.14),
        15481: ('拉赫蒂 vs 查路', 2.93, 2, +0.93),
        15483: ('赫尔辛基火花 vs 库普斯', 3.19, 1, +2.19),
        15482: ('斯达 vs 维京', 7.50, 3, +4.50),
        15485: ('温哥华白帽 vs 洛杉矶FC', 5.99, 2, +3.99),
        15484: ('迈阿密国际 vs 哥伦布机员', 6.62, 4, +2.62),
        15486: ('桑托斯 vs 里莫', 3.35, 0, +3.35),
        15488: ('圣路易斯城 vs 皇家盐湖城', 4.66, 2, +2.66),
        15487: ('芝加哥火焰 vs 夏洛特FC', 4.26, 3, +1.26),
        15489: ('洛城银河 vs 达拉斯', 5.06, 0, +5.06),
        15490: ('波特兰伐木工 vs 西雅图海湾人', 5.25, 3, +2.25),
        15475: ('蔚山现代 vs 安养FC', 2.42, 4, -1.58),
        15477: ('大田市民 vs 光州FC', 1.75, 2, -0.25),
        15476: ('济州联队 vs 仁川联合', 1.82, 6, -4.18),
        15493: ('瓦萨 vs 国际图尔', 2.69, 1, +1.69),
        15492: ('IFK哥德堡 vs 代格福什', 3.30, 2, +1.30),
        15491: ('布鲁马波 vs 马尔默', 385.51, 3, +382.51),
        15494: ('AC奥卢 vs 埃尔维斯', 1.86, 1, +0.86),
        15495: ('AIK索尔纳 vs 奥尔格里特', 3.43, 3, +0.43),
        15496: ('奥斯KFUM vs 克里斯蒂', 3.06, 3, +0.06),
        15497: ('莫尔德 vs 萨普斯堡', 5.13, 6, -0.87),
        15498: ('奥勒松 vs 特罗姆瑟', 3.93, 8, -4.07),
    }
}

# 新模型数据
print("获取新模型预测数据...")
new_matches = {}
for date in ['2026-08-01', '2026-08-02']:
    data = fetch_review(date)
    for m in data.get('data', {}).get('matches', []):
        mid = m['match_id']
        lam = m['expected_goals']
        actual = (m.get('actual_home_score') or 0) + (m.get('actual_away_score') or 0)
        error = lam - actual
        gd = m.get('key_factors', {}).get('model_b', {}).get('goal_distribution', [])
        # 进球判定
        goal_hit = False
        if gd and actual < len(gd):
            top2 = sorted(range(len(gd)), key=lambda i: gd[i], reverse=True)[:2]
            goal_hit = actual in top2
        new_matches[mid] = {
            'home': m['home_team'], 'away': m['away_team'],
            'lambda': lam, 'actual': actual, 'error': error,
            'goal_hit': goal_hit,
            'score': m.get('actual_score', '?:?'),
            'result_goals': m.get('result_goals', 0),
        }

print(f"\n{'='*80}")
print("  进球数预测命中率对比：旧模型 vs 新模型")
print(f"{'='*80}")

# 指标计算
old_mae_list = []
new_mae_list = []
old_hit = 0
new_hit = 0
old_miss = 0
new_miss = 0

matched = 0
print(f"\n{'ID':<6} {'比赛':<30} {'旧λ':>8} {'新λ':>8} {'实际':>5} {'旧误差':>8} {'新误差':>8} {'旧判定':>6} {'新判定':>6}")
print("-" * 95)

for mid in sorted(OLD_DATA['errors'].keys()):
    old = OLD_DATA['errors'][mid]
    if mid not in new_matches:
        continue
    new = new_matches[mid]
    matched += 1
    
    old_lam = old[1]
    new_lam = new['lambda']
    actual = old[2]
    old_err = old[3]
    new_err = new['error']
    
    old_mae_list.append(abs(old_err))
    new_mae_list.append(abs(new_err))
    
    # 旧判定
    old_gd_hit = False
    # 使用API的result_goals
    old_rg = new['result_goals']  # 已被覆盖; 用误差方向近似
    if abs(old_err) <= 1.0:
        old_gd_hit = True  # 近似
    if old_gd_hit:
        old_hit += 1
    else:
        old_miss += 1
    
    if new['goal_hit']:
        new_hit += 1
    else:
        new_miss += 1
    
    old_status = 'HIT' if abs(old_err) <= 1.0 else 'MISS'
    new_status = 'HIT' if new['goal_hit'] else 'MISS'
    
    name = f"{old[0][:25]}"
    print(f"{mid:<6} {name:<30} {old_lam:>8.2f} {new_lam:>8.2f} {actual:>5} {old_err:>+8.2f} {new_err:>+8.2f} {old_status:>6} {new_status:>6}")

# 汇总
print(f"\n{'='*80}")
print("  汇总对比")
print(f"{'='*80}")

n = matched
old_mae = sum(old_mae_list) / n
new_mae = sum(new_mae_list) / n
old_rmse = (sum(e**2 for e in old_mae_list) / n) ** 0.5
new_rmse = (sum(e**2 for e in new_mae_list) / n) ** 0.5

# 排除数据污染的MAE
old_clean = [e for e in old_mae_list if e < 100]
new_clean = [e for e in new_mae_list if e < 100]
old_clean_mae = sum(old_clean) / len(old_clean) if old_clean else 0
new_clean_mae = sum(new_clean) / len(new_clean) if new_clean else 0

print(f"\n  {'指标':<25} {'旧模型':>10} {'新模型':>10} {'改善':>10}")
print(f"  {'-'*55}")
print(f"  {'MAE (含异常)':<25} {old_mae:>10.2f} {new_mae:>10.2f} {'↓' + str(round((1-new_mae/old_mae)*100)) + '%':>10}")
print(f"  {'MAE (排除异常)':<25} {old_clean_mae:>10.2f} {new_clean_mae:>10.2f} {'↓' + str(round((1-new_clean_mae/old_clean_mae)*100)) + '%':>10}")
print(f"  {'RMSE':<25} {old_rmse:>10.2f} {new_rmse:>10.2f} {'↓' + str(round((1-new_rmse/old_rmse)*100)) + '%':>10}")

# 进球判定命中率（简化：误差<=1球视为命中）
old_hit2 = sum(1 for e in old_mae_list if e <= 1.0)
new_hit2 = sum(1 for e in new_mae_list if e <= 1.0)
old_over = sum(1 for e in old_mae_list if 100 > e > 0)

# 方向统计
old_over_count = sum(1 for mid in OLD_DATA['errors'] if OLD_DATA['errors'][mid][3] > 0)
old_under_count = sum(1 for mid in OLD_DATA['errors'] if OLD_DATA['errors'][mid][3] < 0)
new_over_count = sum(1 for mid in new_matches if mid in OLD_DATA['errors'] and new_matches[mid]['error'] > 0)
new_under_count = sum(1 for mid in new_matches if mid in OLD_DATA['errors'] and new_matches[mid]['error'] < 0)

print(f"\n  {'偏差方向':<25} {'旧模型':>10} {'新模型':>10}")
print(f"  {'-'*55}")
print(f"  {'高估场次':<25} {old_over_count:>10} {new_over_count:>10}")
print(f"  {'低估场次':<25} {old_under_count:>10} {new_under_count:>10}")

# API 进球判定（基于 goal_distribution top-2 interval）
total_api_matches = 0
old_api_hit = 0
new_api_hit = 0
for mid in new_matches:
    if mid in OLD_DATA['errors']:
        total_api_matches += 1
        # 旧数据用原result_goals（已被覆盖，这里只能用误差判断）
        # 新数据用goal_hit
        if new_matches[mid]['goal_hit']:
            new_api_hit += 1

# 按联赛
print(f"\n  {'按联赛 MAE':<25} {'旧模型':>10} {'新模型':>10}")
print(f"  {'-'*55}")

# 从new_matches中聚合联赛
by_league = defaultdict(lambda: {'old': [], 'new': []})
league_names = {
    15485: '美职联', 15484: '美职联', 15487: '美职联', 15488: '美职联', 15489: '美职联', 15490: '美职联',
    15478: '芬超', 15481: '芬超', 15483: '芬超', 15493: '芬超', 15494: '芬超',
    15479: '瑞典超', 15492: '瑞典超', 15491: '瑞典超', 15495: '瑞典超',
    15480: '挪超', 15482: '挪超', 15496: '挪超', 15497: '挪超', 15498: '挪超',
}
# Clean league mapping
for mid in OLD_DATA['errors']:
    lg = '其他'
    for k, v in league_names.items():
        if mid == k:
            lg = v
            break
    if mid in new_matches:
        by_league[lg]['old'].append(abs(OLD_DATA['errors'][mid][3]))
        by_league[lg]['new'].append(abs(new_matches[mid]['error']))

for lg in ['美职联', '芬超', '瑞典超', '挪超', '其他']:
    if by_league[lg]['old']:
        o_m = sum(by_league[lg]['old']) / len(by_league[lg]['old'])
        n_m = sum(by_league[lg]['new']) / len(by_league[lg]['new'])
        pct = round((1 - n_m / max(o_m, 0.01)) * 100)
        arrow = '↓' if n_m < o_m else '↑'
        print(f"  {lg:<25} {o_m:>10.2f} {n_m:>10.2f} {arrow + str(abs(pct)) + '%':>10}")
