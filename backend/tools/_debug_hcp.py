"""调试让球概率推导"""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv; load_dotenv()
import numpy as np
from scipy.stats import poisson

async def main():
    home_p, draw_p, away_p = 0.369, 0.465, 0.166
    handicap_line = -1.0
    expected_goals = 3.1
    
    hcp_home = hcp_draw = hcp_away = 0.0
    totals = {}
    
    max_goals = min(int(expected_goals) + 4, 8)
    for hg in range(max_goals + 1):
        for ag in range(max_goals + 1):
            if hg + ag == 0:
                continue
            total = hg + ag
            p = (poisson.pmf(total, expected_goals) *
                 (home_p if hg > ag else draw_p if hg == ag else away_p))
            
            adj = hg + handicap_line
            if adj > ag:
                hcp_home += p
                cat = "让胜"
            elif adj == ag:
                hcp_draw += p
                cat = "让平"
            else:
                hcp_away += p
                cat = "让负"
            
            totals.setdefault(total, {"让胜": 0, "让平": 0, "让负": 0})
            totals[total][cat] += p
    
    total = hcp_home + hcp_draw + hcp_away
    
    print(f"Unnormalized: 让胜={hcp_home:.4f} 让平={hcp_draw:.4f} 让负={hcp_away:.4f} total={total:.4f}")
    print(f"Normalized:   让胜={hcp_home/total:.1%} 让平={hcp_draw/total:.1%} 让负={hcp_away/total:.1%}")
    print(f"\nExpected from SPF: 让负 ≥ {draw_p+away_p:.1%} (平+客)")
    print()
    
    for t in sorted(totals.keys()):
        pmf = poisson.pmf(t, expected_goals)
        cat_sum = totals[t]
        print(f"T={t} (pmf={pmf:.3f}): 让胜={cat_sum['让胜']:.4f} 让平={cat_sum['让平']:.4f} 让负={cat_sum['让负']:.4f}")
    
    # Now check: sum of all score probs (ignoring handicap) should theoretically be 1
    check_sum = 0
    for hg in range(max_goals + 1):
        for ag in range(max_goals + 1):
            if hg + ag == 0: continue
            check_sum += (poisson.pmf(hg + ag, expected_goals) *
                         (home_p if hg > ag else draw_p if hg == ag else away_p))
    print(f"\nTotal unnormalized sum (check): {check_sum:.4f}")
    
    # What's the theoretical sum?
    # Σ poisson.pmf(T) * Σ_{hg+ag=T} factor(hg,ag)
    # = Σ poisson.pmf(T) * (T * away_p + 1 * draw_p if T odd, else (T/2)*away_p + 1*draw_p + (T/2)*home_p)
    # Actually this is complex. But the key insight: Σ factor = #scores with hg>ag * home_p + #scores with hg=ag * draw_p + #scores with hg<ag * away_p
    # For T scores, count_away = count_home = T/2 and count_draw = 1 (when T even) or 0 (when T odd)
    
    print(f"  Should sum to > 1 since there are T+1 scores per T")
    
    # Direct enumeration without truncation
    total_00_05 = sum(poisson.pmf(t, expected_goals) * (t+1) for t in range(6))
    print(f"  Expected total with factors (T=0..5): > some value")

asyncio.run(main())
