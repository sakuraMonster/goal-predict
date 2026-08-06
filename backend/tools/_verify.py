import urllib.request, json
for mid in [15489, 15485, 15473, 15476, 15480]:
    try:
        url = f'http://localhost:8008/api/predictions/{mid}'
        resp = urllib.request.urlopen(url, timeout=15)
        data = json.loads(resp.read())
        d = data.get('data', {})
        eg = d.get('expected_goals', '?')
        zp = d.get('zero_inflation_prob', '?')
        actual = d.get('actual_score', '?:?')
        kf = d.get('key_factors', {})
        if isinstance(kf, str):
            kf = json.loads(kf)
        mb = kf.get('model_b', {})
        lam = mb.get('lambda', '?')
        pull = mb.get('pull_factors', [])
        print(f'ID={mid}: lambda={lam}, expected_goals={eg:.2f}, zero_prob={zp}, actual={actual}')
        print(f'  pull_factors: {len(pull)}')
        for p in pull:
            print(f'    {p["feature"]}: contrib={p["contribution"]}')
    except Exception as e:
        print(f'ID={mid}: {e}')
