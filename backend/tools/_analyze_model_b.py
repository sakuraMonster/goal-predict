"""逐场深度分析：模型B进球预测的全部27场比赛"""
import urllib.request, json
from collections import defaultdict
import math

def fetch_review(date):
    url = f'http://localhost:8008/api/predictions/review/daily?date={date}'
    resp = urllib.request.urlopen(url, timeout=30)
    return json.loads(resp.read())

all_matches = []
for date in ['2026-08-01', '2026-08-02']:
    data = fetch_review(date)
    matches = data.get('data', {}).get('matches', [])
    for m in matches:
        m['_date'] = date
    all_matches.extend(matches)

# 全局统计
total = len(all_matches)
errors_list = []
hit_count = 0
miss_count = 0

for m in all_matches:
    lam = m.get('expected_goals', 0)
    actual = (m.get('actual_home_score') or 0) + (m.get('actual_away_score') or 0)
    error = lam - actual
    abs_error = abs(error)
    mid = m['match_id']
    home = m['home_team']
    away = m['away_team']
    league = m.get('league_name', '?')
    date = m['_date']
    score = m.get('actual_score', '?:?')
    
    # Model B detail
    mb = m.get('key_factors', {}).get('model_b', {})
    lam_mb = mb.get('lambda', lam)
    over25 = mb.get('over_2_5_prob', 0)
    gd = mb.get('goal_distribution', [])
    reasoning = mb.get('reasoning', '')
    push = mb.get('push_factors', [])
    pull = mb.get('pull_factors', [])
    
    # 计算进球数判定（API逻辑：实际总进球是否在概率Top2区间）
    goal_hit = False
    if gd and actual < len(gd):
        top2_idx = sorted(range(len(gd)), key=lambda i: gd[i], reverse=True)[:2]
        goal_hit = actual in top2_idx
    
    rg = m.get('result_goals', 0)
    if rg == 1:
        hit_count += 1
    elif rg == -1:
        miss_count += 1
    
    errors_list.append({
        'id': mid, 'date': date, 'home': home, 'away': away, 'league': league,
        'lambda': lam, 'actual': actual, 'score': score,
        'error': error, 'abs_error': abs_error,
        'over25': over25, 'gd': gd, 'goal_hit': goal_hit, 'result_goals': rg,
        'reasoning': reasoning, 'push': push, 'pull': pull,
        'home_prob': m.get('home_prob', 0), 'draw_prob': m.get('draw_prob', 0),
        'away_prob': m.get('away_prob', 0),
        'pred_direction': m.get('pred_direction', ''),
        'actual_result': m.get('actual_result', ''),
        'is_cold': m.get('is_cold_match', False),
        'confidence': m.get('confidence_level', ''),
    })

# ============================================================
# PART 1: 逐场完整数据
# ============================================================
print("=" * 110)
print("  模型B 进球数预测 —— 全部27场逐场深度分析")
print("=" * 110)

for i, e in enumerate(errors_list):
    direction = '⚠高估' if e['error'] > 0.5 else ('⚠低估' if e['error'] < -0.5 else '  OK ')
    extreme = '🔥数据污染' if e['lambda'] > 100 else ''
    
    print(f"\n{'─'*110}")
    print(f"  [{i+1}/27] ID={e['id']} | {e['date']} | {e['home']} vs {e['away']} | {e['league']} {extreme}")
    print(f"  预测λ={e['lambda']:.2f} | 实际={e['score']} (总{e['actual']}球) | 误差={e['error']:+.2f}球 | {direction}")
    print(f"  over_2.5={e['over25']:.1%} | 进球判定={'HIT' if e['goal_hit'] else 'MISS'} | result_goals={e['result_goals']} | 冷门={e['is_cold']} | 置信度={e['confidence']}")
    print(f"  模型A方向={e['pred_direction']}({max(e['home_prob'],e['draw_prob'],e['away_prob'])*100:.0f}%) | 实际赛果={e['actual_result']} | probs=主{e['home_prob']:.1%}/平{e['draw_prob']:.1%}/客{e['away_prob']:.1%}")
    
    # 进球分布
    if e['gd']:
        gd_str = ' | '.join([f"{k}球={e['gd'][k]:.1%}" for k in range(min(5, len(e['gd'])))])
        print(f"  进球分布: {gd_str}")
    
    # Model B reasoning
    if e['reasoning']:
        print(f"  ModelB推理: {e['reasoning'][:200]}")
    
    # Push factors (推高λ)
    if e['push']:
        print(f"  ▶ 推高λ因素 ({len(e['push'])}个):")
        for pf in e['push']:
            print(f"      {pf['feature']}: {pf.get('value', '?')}, 贡献度={pf['contribution']:.4f}")
    else:
        print(f"  ▶ 推高λ因素: 无")
    
    # Pull factors (拉低λ)
    if e['pull']:
        print(f"  ▼ 拉低λ因素 ({len(e['pull'])}个):")
        for pf in e['pull']:
            print(f"      {pf['feature']}: {pf.get('value', '?')}, 贡献度={pf['contribution']:.4f}")
    else:
        print(f"  ▼ 拉低λ因素: 无")
    
    # 快速诊断
    if e['lambda'] > 100:
        print(f"  🔴 诊断: 数据污染(xG异常)，λ爆炸")
    elif e['error'] > 2.0:
        if e['actual'] == 0:
            print(f"  🔴 诊断: 零封比赛被严重高估。Poisson无法预测0球。Push因素驱动力过强(无Pull抵抗)")
        elif e['league'] == '美职联':
            print(f"  🟡 诊断: 美职联系统性高估。预测λ={e['lambda']:.1f}远高于联赛实际进球水平")
        elif e['actual'] <= 1:
            print(f"  🟡 诊断: 低进球比赛被高估。进攻特征被高权重市场信号放大")
        else:
            print(f"  🟡 诊断: 整体高估{e['error']:.1f}球。需要检查哪些push因素过度贡献")
    elif e['error'] < -2.0:
        print(f"  🔴 诊断: 爆冷大球被低估。实际{e['actual']}球远超Poisson单参数预测范围")
    elif e['error'] > 1.0:
        print(f"  🟡 诊断: 中等高估{e['error']:.1f}球。push因素贡献占比偏高")
    elif e['error'] < -1.0:
        print(f"  🟡 诊断: 中等低估{abs(e['error']):.1f}球。模型保守，未捕捉到大球信号")
    else:
        print(f"  🟢 诊断: 预测基本准确（误差<=1球）")

# ============================================================
# PART 2: 汇总统计
# ============================================================
print(f"\n\n{'='*110}")
print("  汇总统计")
print(f"{'='*110}")

# 按误差分级
levels = [
    ('数据污染 (λ>100)', [e for e in errors_list if e['lambda'] > 100]),
    ('严重高估 (误差>3)', [e for e in errors_list if e['error'] > 3 and e['lambda'] <= 100]),
    ('显著高估 (1~3)', [e for e in errors_list if 1 < e['error'] <= 3]),
    ('轻微高估 (0.3~1)', [e for e in errors_list if 0.3 < e['error'] <= 1]),
    ('基本准确 (<=0.3)', [e for e in errors_list if abs(e['error']) <= 0.3]),
    ('轻微低估 (-1~-0.3)', [e for e in errors_list if -1 <= e['error'] < -0.3]),
    ('显著低估 (-3~-1)', [e for e in errors_list if -3 <= e['error'] < -1]),
    ('严重低估 (<-3)', [e for e in errors_list if e['error'] < -3]),
]

for label, items in levels:
    if items:
        print(f"\n  [{label}] {len(items)}场:")
        for e in items:
            print(f"    ID={e['id']} {e['home']} vs {e['away']} ({e['league']}): λ={e['lambda']:.2f} 实际={e['score']}({e['actual']}球) 误差={e['error']:+.2f}")

# ============================================================
# PART 3: Push因素频率分析
# ============================================================
print(f"\n\n{'='*110}")
print("  Push/Pull 因素频率分析（哪些特征在驱动λ预测）")
print(f"{'='*110}")

push_counter = defaultdict(lambda: {'count': 0, 'total_contribution': 0.0, 'matches': []})
pull_counter = defaultdict(lambda: {'count': 0, 'total_contribution': 0.0, 'matches': []})

for e in errors_list:
    for pf in e['push']:
        name = pf['feature']
        push_counter[name]['count'] += 1
        push_counter[name]['total_contribution'] += pf.get('contribution', 0)
        push_counter[name]['matches'].append(e['id'])
    for pf in e['pull']:
        name = pf['feature']
        pull_counter[name]['count'] += 1
        pull_counter[name]['total_contribution'] += pf.get('contribution', 0)
        pull_counter[name]['matches'].append(e['id'])

print(f"\n  ▶ 推高λ因素 (Top 15):")
for name, info in sorted(push_counter.items(), key=lambda x: -x[1]['count'])[:15]:
    avg_c = info['total_contribution'] / info['count']
    print(f"    {name}: 出现{info['count']}次, 平均贡献={avg_c:.4f}, 涉及ID={info['matches'][:5]}...")

print(f"\n  ▼ 拉低λ因素 (Top 15):")
pull_sorted = sorted(pull_counter.items(), key=lambda x: -x[1]['count'])[:15]
if pull_sorted:
    for name, info in pull_sorted:
        avg_c = info['total_contribution'] / info['count']
        print(f"    {name}: 出现{info['count']}次, 平均贡献={avg_c:.4f}, 涉及ID={info['matches'][:5]}...")
else:
    print(f"    （无拉低因素 — 所有比赛的pull_factors均为空！）")

# ============================================================
# PART 4: 按实际比分场景分析
# ============================================================
print(f"\n\n{'='*110}")
print("  场景分析：特定比分模式下的预测表现")
print(f"{'='*110}")

scenarios = {
    '零封(0球)': [e for e in errors_list if e['actual'] == 0],
    '小球(1球)': [e for e in errors_list if e['actual'] == 1],
    '2球': [e for e in errors_list if e['actual'] == 2],
    '3球': [e for e in errors_list if e['actual'] == 3],
    '4球': [e for e in errors_list if e['actual'] == 4],
    '大球(6+)': [e for e in errors_list if e['actual'] >= 6],
}

for scenario, items in scenarios.items():
    if items:
        avg_lam = sum(e['lambda'] for e in items) / len(items)
        avg_error = sum(e['error'] for e in items) / len(items)
        print(f"\n  [{scenario}] {len(items)}场:")
        print(f"    平均预测λ={avg_lam:.2f}, 平均误差={avg_error:+.2f}")
        for e in items:
            # 找出最大的push因素
            top_push = sorted(e['push'], key=lambda x: x.get('contribution', 0), reverse=True)[:3] if e['push'] else []
            top_push_str = ', '.join([f"{p['feature']}({p['contribution']:.3f})" for p in top_push])
            print(f"    ID={e['id']} {e['home']} vs {e['away']} λ={e['lambda']:.2f} 误差={e['error']:+.2f} | top_push: {top_push_str}")

# ============================================================
# PART 5: 核心问题总结
# ============================================================
print(f"\n\n{'='*110}")
print("  核心问题诊断")
print(f"{'='*110}")

# 问题1: pull_factors 缺失
no_pull = sum(1 for e in errors_list if not e['pull'])
print(f"\n  问题1: {no_pull}/{total} ({no_pull/total*100:.0f}%) 的比赛没有任何拉低λ因素")
print(f"    含义: 模型只看到推高λ的信号，没有任何对抗性信号来抑制预测偏高")
print(f"    后果: λ几乎必然偏高")

# 问题2: 市场信号主导
market_features = ['市场隐含主胜概率', '市场隐含客胜概率', '市场隐含']
market_push_count = 0
for e in errors_list:
    for pf in e['push']:
        if '市场隐含' in pf['feature']:
            market_push_count += 1
print(f"\n  问题2: '市场隐含概率'类特征作为push因素出现{market_push_count}次")
print(f"    含义: 赔率隐含概率是驱动λ预测的最主要特征")
print(f"    风险: 赔率反映的是市场预期（含情绪），而非实际进球能力")

# 问题3: 联赛差异
by_league_errors = defaultdict(list)
for e in errors_list:
    by_league_errors[e['league']].append(e['error'])
print(f"\n  问题3: 联赛系统性偏差")
for lg, errs in sorted(by_league_errors.items(), key=lambda x: -len(x[1])):
    if len(errs) >= 2:
        m = sum(errs)/len(errs)
        print(f"    {lg}: {len(errs)}场, mean_error={m:+.2f}, all_over={all(e>0 for e in errs)}")
