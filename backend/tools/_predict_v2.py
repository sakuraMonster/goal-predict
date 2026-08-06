"""对昨日6场重新预测并对比实际结果"""
import requests

IDS = [15463, 15464, 15465, 15466, 15467, 15468]
NAMES = {
    15463: "001 中日德兰 vs 贝西克塔斯",
    15464: "002 帕福斯 vs 斯普利特海杜克",
    15465: "003 安德莱赫特 vs 哈马比",
    15466: "004 费伦茨瓦罗斯 vs 特温特",
    15467: "005 本菲卡 vs 圣加仑",
    15468: "006 科林蒂安 vs 巴拉纳竞技",
}
SPF_MAP = {1: "主胜", 2: "平局", 3: "客胜"}

print(f"{'场次':<35} {'主胜':>6} {'平局':>6} {'客胜':>6} │ {'预测':>6} {'实际':>6} {'命中':>4} │ {'冷门':>4} {'修正':>4} │ {'进球':>5} {'实际比':>6}")
print("-" * 115)

hits = 0; total = 0
for mid in IDS:
    try:
        resp = requests.get(f"http://localhost:8008/api/predictions/{mid}", timeout=60)
        d = resp.json().get("data", {})
        
        hp = d.get("home_prob", 0)
        dp = d.get("draw_prob", 0)
        ap = d.get("away_prob", 0)
        
        max_p = max(hp, dp, ap)
        if hp == max_p: pred_spf = "主胜"; spf_code = 1
        elif dp == max_p: pred_spf = "平局"; spf_code = 2
        else: pred_spf = "客胜"; spf_code = 3
        
        actual_code = d.get("result_spf")
        actual_spf = SPF_MAP.get(actual_code, f"#{actual_code}" if actual_code else "?")
        
        hit = "Y" if spf_code == actual_code else "-"
        if actual_code is not None and actual_code >= 1:
            total += 1
            if hit == "Y": hits += 1
        
        cold = "Y" if d.get("is_cold_match") else "-"
        cc = d.get("cold_correction")
        corrected = "Y" if cc and cc.get("model_original") else "-"
        
        goals = d.get("expected_goals", 0)
        actual_score = d.get("actual_score", "?")
        
        name = NAMES.get(mid, f"ID={mid}")
        print(f"{name:<35} {hp:>6.3f} {dp:>6.3f} {ap:>6.3f} │ {pred_spf:>6} {actual_spf:>6} {hit:>4} │ {cold:>4} {corrected:>4} │ {goals:>5.1f} {actual_score:>6}")
    except Exception as e:
        print(f"{NAMES.get(mid, f'ID={mid}')}: ERROR - {e.__class__.__name__}: {e}")

print(f"\nSPF命中: {hits}/{total}")
