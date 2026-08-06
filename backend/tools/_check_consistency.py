import urllib.request, json

for date in ['2026-08-01', '2026-08-02']:
    url = f'http://localhost:8008/api/predictions/review/daily?date={date}'
    resp = urllib.request.urlopen(url, timeout=30)
    d = json.loads(resp.read()).get('data', {})
    
    print(f'\n=== {date} ===')
    print(f'Summary: goals_hit={d.get("goals_hit")} goals_miss={d.get("goals_miss")} settled={d.get("settled")} unsettled={d.get("unsettled")}')
    
    matches = d.get('matches', [])
    mismatches = []
    for m in matches:
        eg = m.get('expected_goals', 0) or 0
        ah = m.get('actual_home_score') or 0
        aa = m.get('actual_away_score') or 0
        actual = ah + aa
        rg = m.get('result_goals', 0)
        
        # API rule: |error| <= 1.5
        api_hit = abs(actual - eg) <= 1.5 if eg > 0 else False
        db_hit = rg == 1
        
        if api_hit != db_hit:
            mismatches.append((m['match_id'], m['home_team'], m['away_team'], eg, actual, rg, api_hit))
    
    if mismatches:
        print(f'Mismatches ({len(mismatches)}):')
        for mid, home, away, eg, act, rg, api_hit in mismatches:
            err = abs(eg - act)
            status = 'DB:HIT/API:MISS' if rg==1 else 'DB:MISS/API:HIT'
            print(f'  ID={mid} {home} vs {away}: lam={eg:.2f} actual={act} err={err:.2f} {status}')
    else:
        print('All matches consistent!')
