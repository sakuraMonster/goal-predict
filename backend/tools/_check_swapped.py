import asyncio
from app.collector.sportmonks.client import SportMonksClient

async def main():
    sm = SportMonksClient()
    
    # Check problematic fixtures
    for label, sm_fx in [("周日001", 19648109), ("周日003", 19648108)]:
        print(f"=== {label} (SM fixture {sm_fx}) ===")
        fx = await sm.get_fixture_by_id(sm_fx, includes="participants")
        parts = fx.get("participants", []) if isinstance(fx, dict) else []
        for i, p in enumerate(parts):
            if isinstance(p, dict):
                loc = (p.get("meta") or {}).get("location", "NO_META")
                print(f"  [{i}] id={p.get('id')}, name={p.get('name')}, location={loc}")
        
        # If NO meta.location, check what our code would do
        no_meta = all(not (p.get("meta") or {}).get("location") for p in parts if isinstance(p, dict))
        if no_meta:
            print(f"  -> No meta.location! participants[0]={parts[0].get('id') if parts else 'N/A'}")
            print(f"  -> Code would assume [0]=home, [1]=away")
        print()
    
    await sm.close()

asyncio.run(main())
