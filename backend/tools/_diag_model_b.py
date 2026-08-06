"""检查 SM prediction 特征对 Model B 的影响"""
import asyncio, os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv
load_dotenv()

import numpy as np
import pandas as pd
import joblib
from app.db.database import async_session
from app.db.models import Match
from app.predictor.features import FeatureEngineer
from app.predictor.models.model_b import ModelB
from sqlalchemy import select

MODEL_DIR = os.path.join(os.path.dirname(__file__), "..", "models")

async def main():
    with open(os.path.join(MODEL_DIR, "model_b_features.json")) as f:
        saved_features = json.load(f)
    model = joblib.load(os.path.join(MODEL_DIR, "model_b.pkl"))
    scaler = joblib.load(os.path.join(MODEL_DIR, "model_b_scaler.pkl"))
    
    async with async_session() as db:
        fe = FeatureEngineer(db)
        
        # Without SM prediction
        feat_no_sm = await fe.extract_features(15470)
        mb = ModelB()
        r1 = mb.predict(feat_no_sm)
        print(f"Without SM prediction: expected_goals={r1['expected_goals']}")
        
        # With SM prediction
        from app.collector.sportmonks.client import SportMonksClient
        sm = SportMonksClient()
        sm_pred = await sm.get_predictions_by_fixture(19629619)
        await sm.close()
        print(f"\nSM prediction raw: {json.dumps(sm_pred, indent=2)[:500] if sm_pred else 'None'}")
        
        feat_with_sm = await fe.extract_features(15470, sm_pred)
        r2 = mb.predict(feat_with_sm)
        print(f"\nWith SM prediction: expected_goals={r2['expected_goals']}")
        
        # Compare SM-related features
        sm_names = ['sm_home_prob', 'sm_draw_prob', 'sm_away_prob', 'sm_over_2_5_prob', 'sm_btts_prob']
        print(f"\n{'feature':>25} {'no_sm':>10} {'with_sm':>10} {'coef':>10}")
        for name in sm_names:
            idx = saved_features.index(name)
            no_val = feat_no_sm[name].values[0] if name in feat_no_sm.columns else 'N/A'
            with_val = feat_with_sm[name].values[0] if name in feat_with_sm.columns else 'N/A'
            coef = model.coef_[idx]
            print(f"{name:>25} {str(no_val):>10} {str(with_val):>10} {coef:>10.4f}")
        
        # What if we zero out SM features?
        feat_modified = feat_with_sm.copy()
        for name in sm_names:
            if name in feat_modified.columns:
                feat_modified[name] = feat_no_sm[name].values[0] if name in feat_no_sm.columns else 0
        r3 = mb.predict(feat_modified)
        print(f"\nWith SM features replaced by defaults: expected_goals={r3['expected_goals']}")

asyncio.run(main())
