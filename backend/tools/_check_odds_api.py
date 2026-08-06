import requests, json
for mid in [15470, 15471]:
    r = requests.get(f'http://localhost:8008/api/matches/{mid}/odds-history', timeout=10)
    d = r.json().get("data", {})
    bms = d.get("bookmakers", [])
    print(f'\n=== match {mid} ===')
    print(f'bookmakers count: {len(bms)}')
    for bm in bms:
        name = bm.get("name", "?")
        points = len(bm.get("points", []))
        print(f'  {name}: {points} points')
    # 看第一条的keys
    if bms:
        print(f'  first bm keys: {list(bms[0].keys())}')
        pts = bms[0].get("points", [])
        if pts:
            print(f'  first point: {pts[0]}')
