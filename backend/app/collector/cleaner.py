"""
球队名称映射引擎：L1(翻译表) → L2(编辑距离) → L3(人工确认)
"""
from difflib import SequenceMatcher
from typing import Optional, List, Dict

CITY_TRANSLATIONS = {
    "曼彻斯特": "Manchester",
    "伦敦": "London",
    "利物浦": "Liverpool",
    "巴塞罗那": "Barcelona",
    "马德里": "Madrid",
    "巴黎": "Paris",
    "慕尼黑": "Munich",
    "多特蒙德": "Dortmund",
    "米兰": "Milan",
    "都灵": "Turin",
    "那不勒斯": "Napoli",
    "罗马": "Roma",
    "塞维利亚": "Sevilla",
    "瓦伦西亚": "Valencia",
    "毕尔巴鄂": "Bilbao",
    "里斯本": "Lisbon",
    "波尔图": "Porto",
    "阿姆斯特丹": "Amsterdam",
    "格拉斯哥": "Glasgow",
    "马赛": "Marseille",
    "里昂": "Lyon",
    "摩纳哥": "Monaco",
    "柏林": "Berlin",
    "汉堡": "Hamburg",
    "勒沃库森": "Leverkusen",
    "沃尔夫斯堡": "Wolfsburg",
    "门兴": "Monchengladbach",
    "法兰克福": "Frankfurt",
    "斯图加特": "Stuttgart",
    "不来梅": "Bremen",
    "佛罗伦萨": "Fiorentina",
    "拉齐奥": "Lazio",
    "亚特兰大": "Atalanta",
    "桑普多利亚": "Sampdoria",
    "热那亚": "Genoa",
    "博洛尼亚": "Bologna",
    "乌迪内斯": "Udinese",
    "卡利亚里": "Cagliari",
    "东京": "Tokyo",
    "大阪": "Osaka",
    "横滨": "Yokohama",
    "名古屋": "Nagoya",
    "神户": "Kobe",
    "广岛": "Hiroshima",
    "鹿岛": "Kashima",
    "浦和": "Urawa",
    "川崎": "Kawasaki",
    "首尔": "Seoul",
    "全北": "Jeonbuk",
    "蔚山": "Ulsan",
    "水原": "Suwon",
    "北京": "Beijing",
    "上海": "Shanghai",
    "广州": "Guangzhou",
    "山东": "Shandong",
}


class NameMatcher:
    """球队/联赛名称三层匹配引擎"""

    @staticmethod
    def match_l1(chinese_name: str, candidates: List[Dict]) -> Optional[Dict]:
        """
        L1: 翻译表精确匹配
        candidates: [{"id": 1, "name_en": "Manchester United", ...}, ...]
        Returns: {"level": "L1", "match": candidate_dict, "confidence": 1.0} or None
        """
        zh_lower = chinese_name.lower()
        for city_zh, city_en in CITY_TRANSLATIONS.items():
            if city_zh in zh_lower:
                for c in candidates:
                    if city_en.lower() in c.get("name_en", "").lower():
                        return {"level": "L1", "match": c, "confidence": 1.0}
        return None

    @staticmethod
    def match_l2(chinese_name: str, candidates: List[Dict]) -> List[Dict]:
        """
        L2: 编辑距离模糊匹配
        Returns: 按相似度降序排列的候选列表，每个候选含 similarity 字段
        """
        results = []
        for c in candidates:
            ratio = SequenceMatcher(
                None,
                chinese_name.lower(),
                c.get("name_en", "").lower()
            ).ratio()
            if ratio > 0.3:
                results.append({**c, "similarity": round(ratio, 4)})
        return sorted(results, key=lambda x: x["similarity"], reverse=True)

    @staticmethod
    def auto_match(chinese_name: str, candidates: List[Dict]) -> Optional[Dict]:
        """
        自动化匹配：先 L1 后 L2
        如果 L2 最佳匹配相似度 >= 0.85，自动确认
        否则返回 L2 候选列表供 L3 人工确认
        """
        l1_result = NameMatcher.match_l1(chinese_name, candidates)
        if l1_result:
            return l1_result

        l2_results = NameMatcher.match_l2(chinese_name, candidates)
        if not l2_results:
            return None

        best = l2_results[0]
        similarity = best.get("similarity", 0)
        if similarity >= 0.85:
            return {"level": "L2", "match": best, "confidence": similarity}

        return {"level": "L3", "candidates": l2_results, "confidence": similarity}
