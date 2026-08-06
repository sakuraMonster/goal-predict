import urllib.request, json, math

all_data = []
for date in ['2026-08-01', '2026-08-02']:
    url = f'http://localhost:8008/api/predictions/review/daily?date={date}'
    resp = urllib.request.urlopen(url, timeout=30)
    for m in json.loads(resp.read()).get('data',{}).get('matches',[]):
        all_data.append({
            'lam': m['expected_goals'],
            'actual': (m.get('actual_home_score') or 0)+(m.get('actual_away_score') or 0),
            'home': m['home_team'], 'away': m['away_team'], 'id': m['match_id']
        })

total = len(all_data)

# Rule A: [floor(lam), ceil(lam)], integer->[n, n+1]
hit_a = sum(1 for d in all_data if d['actual'] in (
    (math.floor(d['lam']), math.ceil(d['lam'])) if math.floor(d['lam']) != math.ceil(d['lam'])
    else (int(d['lam']), int(d['lam'])+1)
))
print(f'[floor, ceil]: {hit_a}/{total} = {hit_a/total*100:.1f}%')

# Rule B: [floor(lam), ceil(lam)], integer->[n-1, n]
hit_b = sum(1 for d in all_data if d['actual'] in (
    (math.floor(d['lam']), math.ceil(d['lam'])) if math.floor(d['lam']) != math.ceil(d['lam'])
    else (int(d['lam'])-1, int(d['lam']))
))
print(f'[floor, ceil] int->[n-1,n]: {hit_b}/{total} = {hit_b/total*100:.1f}%')

# Rule C: round(lam) and its neighbor toward lam
hit_c = 0
for d in all_data:
    r = round(d['lam'])
    if d['lam'] >= r:
        bins = (r, r + 1)
    else:
        bins = (r - 1, r)
    if d['actual'] in bins:
        hit_c += 1
print(f'[round, round+-1 toward lam]: {hit_c}/{total} = {hit_c/total*100:.1f}%')

# Rule D: 2 closest integers, with 4 treated as 4+
hit_d = 0
for d in all_data:
    lam = d['lam']
    dists = [(abs(lam - i), i) for i in range(5)]  # 0,1,2,3,4(4+)
    dists.sort()
    bins = tuple(sorted([dists[0][1], dists[1][1]]))
    act = min(d['actual'], 4)
    if act in bins:
        hit_d += 1
print(f'[2 closest, 4=4+]: {hit_d}/{total} = {hit_d/total*100:.1f}%')

# Rule E: λ-based dynamic 2-bin
# λ<1.5: [0,1], 1.5≤λ<2.5: [1,2], 2.5≤λ<3.5: [2,3], λ≥3.5: [3,4+]
hit_e = 0
for d in all_data:
    lam = d['lam']
    if lam < 1.5:
        bins = (0, 1)
    elif lam < 2.5:
        bins = (1, 2)
    elif lam < 3.5:
        bins = (2, 3)
    else:
        bins = (3, 4)  # 4 means 4+
    act = min(d['actual'], 4)
    if act in bins:
        hit_e += 1
print(f'[dynamic range]: {hit_e}/{total} = {hit_e/total*100:.1f}%')

# Show misses for the best 2-value rule
print('\n--- Misses for Rule D (2 closest, 4=4+) ---')
for d in all_data:
    lam = d['lam']
    dists = [(abs(lam - i), i) for i in range(5)]
    dists.sort()
    bins = tuple(sorted([dists[0][1], dists[1][1]]))
    act = min(d['actual'], 4)
    if act not in bins:
        print(f'  ID={d["id"]} {d["home"][:12]} vs {d["away"][:12]}: lam={lam:.2f} act={d["actual"]} bins={bins}')
