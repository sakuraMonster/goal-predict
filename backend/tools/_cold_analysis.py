"""分析MISS比赛是否有冷门信号"""
import urllib.request, json

# 11场MISS比赛ID
miss_ids = [15472, 15478, 15486, 15489, 15490, 15475, 15476, 15493, 15497, 15498, 15496]

for mid in miss_ids:
    url = f'http://localhost:8008/api/predictions/{mid}'
    resp = urllib.request.urlopen(url, timeout=10)
    d = json.loads(resp.read()).get('data', {})
    
    eg = d.get('expected_goals', 0)
    ah = d.get('actual_home_score', 0) or 0
    aa = d.get('actual_away_score', 0) or 0
    actual = ah + aa
    is_cold = d.get('is_cold_match', False)
    conf = d.get('confidence_level', '?')
    
    kf = d.get('key_factors', {})
    if isinstance(kf, str):
        kf = json.loads(kf)
    
    ma = kf.get('model_a', {})
    mb = kf.get('model_b', {})
    pred_dir = ma.get('pred_direction', '?')
    
    # Check cold correction
    cc = d.get('cold_correction')
    
    # Check deviation features from key_factors
    error_analysis = None
    # We don't have error_analysis in the prediction detail, but we have model reasoning
    
    # Get market odds info
    hp = d.get('home_prob', 0)
    dp = d.get('draw_prob', 0)
    ap = d.get('away_prob', 0)
    
    print(f'ID={mid} {d.get("home_team","?")} vs {d.get("away_team","?")}')
    print(f'  lam={eg:.2f} actual={ah}:{aa}({actual}) is_cold={is_cold} conf={conf}')
    print(f'  pred_direction={pred_dir} probs=主{hp:.1%}/平{dp:.1%}/客{ap:.1%}')
    
    # Check market vs fundamental divergence
    push = mb.get('push_factors', [])
    pull = mb.get('pull_factors', [])
    
    # Find any deviation/divergence signals
    for pf in push + pull:
        fname = pf.get('feature', '')
        if any(kw in fname for kw in ['偏离', '分歧', '离散', '背离', 'cold', '冷', '波动']):
            print(f'  signal: {fname} = {pf.get("value","?")} (contrib={pf.get("contribution",0):.4f})')
    
    # ZIP zero inflation
    zp = d.get('zero_inflation_prob', 0)
    if zp > 0.1:
        print(f'  ZIP zero_prob={zp:.2f}')
    
    # cold correction details
    if cc:
        print(f'  cold_correction: {json.dumps(cc, ensure_ascii=False)[:200]}')
    
    print()
