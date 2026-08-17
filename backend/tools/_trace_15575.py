"""定位摩雷伦斯vs布拉加(15575) 特征提取崩溃点"""
import asyncio
import sys
import os
import traceback

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.db.database import async_session
from app.predictor.features_b import FeatureEngineerB

OUT = os.path.join(os.path.dirname(__file__), "trace_15575.txt")


async def main():
    lines = []
    async with async_session() as db:
        feat = FeatureEngineerB(db)
        try:
            fdf = await feat.extract_features(15575)
            lines.append(f"OK: empty={fdf.empty}")
        except Exception as e:
            tb = traceback.format_exc()
            lines.append(tb)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("done")


asyncio.run(main())
