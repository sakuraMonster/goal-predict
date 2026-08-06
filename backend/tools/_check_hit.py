import urllib.request, json, math
total_hit = 0
total_miss = 0
for date in ['2026-08-01', '2026-08-02', '2026-08-03']:
    try:
        url = f'http://localhost:8008/api/predictions/review/daily?date={date}'
        resp = urllib.request.urlopen(url, timeout=30)
        d = json.loads(resp.read()).get('data', {})
        gh = d.get('goals_hit', 0)
        gm = d.get('goals_miss', 0)
        total = gh + gm
        print(f'{date}: HIT={gh}/{total} ({gh/total*100:.0f}%)  MISS={gm}')
        total_hit += gh
        total_miss += gm
        
        # Show per-match details
        for m in d.get('matches', []):
            actual = (m.get('actual_home_score') or 0) + (m.get('actual_away_score') or 0)
            eg = m.get('expected_goals', 0) or 0
            if eg <= 0:
                continue
            # V4.12 rule
            eg_clean = round(eg, 10)  # 去浮点噪声
            frac = eg_clean - math.floor(eg_clean)
            SNAP_DOWN, SNAP_UP = 0.10, 0.90
            if frac < SNAP_DOWN:
                eff = math.floor(eg_clean)
            elif frac > SNAP_UP:
                eff = math.ceil(eg_clean)
            else:
                eff = eg
            dists = sorted([(abs(eff - i), i) for i in range(7)])
            top2 = sorted([dists[0][1], dists[1][1]])
            hit = min(actual, 6) in top2
            status = 'HIT' if hit else 'MISS'
            goals = f'{top2[0]}球/{top2[1]}球' if top2[0] != 6 else f'{top2[0]}球/{top2[1]}球'
            print(f'  {m["match_id"]} {m["home_team"]} vs {m["away_team"]}: lam={eg:.2f} actual={m.get("actual_score","?:?")} top2={goals} {status}')
    except Exception as e:
        print(f'{date}: error {e}')

print(f'\nTotal: HIT={total_hit}/{total_hit+total_miss} ({total_hit/(total_hit+total_miss)*100:.0f}%)')
