import json
from collections import defaultdict

with open('e:/zhangxuejun/new-thinking/ricking-03/backend/tools/model_c_analysis_0725_0727.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

leagues = defaultdict(lambda: {'hit': [], 'miss': []})
for m in data['missed']:
    leagues[m['league']]['miss'].append(m)
for m in data['hit']:
    leagues[m['league']]['hit'].append(m)

league_order = ['挪超', '瑞典超', '芬超', '巴甲', '未知', '欧冠']

for lg in league_order:
    if lg not in leagues:
        continue
    entries = leagues[lg]
    hit_count = len(entries['hit'])
    total = len(entries['hit']) + len(entries['miss'])
    acc = hit_count / total * 100 if total else 0
    print(f'==== {lg}  {hit_count}/{total} = {acc:.1f}% ====')

    for m in entries['miss']:
        d = m['detail']
        extra = '  ***低分规则触发***' if d.get('low_score_applied') else ''
        print("  MISS | {home:<8s} vs {away:<8s} | {score}({tg}球) | lam={eg:.2f} SNAP={snap} | GL={gl:.1f} calib={cal:.3f} s={s:+.3f} f={f:+.3f} dp={dp:+.3f}{extra}".format(
            home=m['home'], away=m['away'], score=m['actual_score'], tg=m['actual_total'],
            eg=m['expected_goals'], snap=m['snap_top2'],
            gl=d['goal_line'], cal=d['calib'], s=d['strength_adj'], f=d['form_adj'], dp=d['drop_adj'],
            extra=extra))

    for m in entries['hit']:
        print("  HIT  | {home:<8s} vs {away:<8s} | {tg}球 | lam={eg:.2f} SNAP={snap}".format(
            home=m['home'], away=m['away'], tg=m['actual_total'],
            eg=m['expected_goals'], snap=m['snap_top2']))
    print()
