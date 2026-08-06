"""
数据采集 Pipeline：整合 SportMonks + 竞彩网 数据，写入数据库
"""
import json
import unicodedata
from datetime import datetime, timedelta
from sqlalchemy import select, update, text, or_
from app.collector.sportmonks.client import SportMonksClient
from app.collector.scrapers import jczq_scraper
from app.db.models import (
    Match, OddsSnapshot, TeamSeasonStats, Team, TeamAlias,
    League, LeagueAlias, TaskLog, Injury, HeadToHead,
)
from app.db.database import async_session, engine
from app.db.logger import AppLogger


def _normalize_name(s: str) -> str:
    """Unicode NFKD 规范化并去除重音符号，用于跨系统名称匹配"""
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s.lower().strip())
    return "".join(c for c in s if not unicodedata.combining(c))


class SyncPipeline:
    """数据同步主管道"""

    # SportMonks market_id 常量
    MARKET_1X2 = 1        # 胜平负 - Fulltime Result
    MARKET_HANDICAP = 6    # 亚盘让球 - Asian Handicap
    MARKET_OVER_UNDER = 80 # 大小球 - Goals Over/Under

    # 常见博彩公司 ID → 中文名（SportMonks bookmaker_id）
    BOOKMAKER_NAMES = {
        1: "10Bet",
        2: "bet365",
        5: "威廉希尔",
        9: "Betfair",
        11: "立博",
        12: "BetVictor",
        14: "Bwin",
        16: "Marathonbet",
        20: "Pinnacle",
        23: "Pinnacle",
        29: "澳门",
        34: "Sbo",
        35: "1xbet",
        38: "皇冠",
        44: "易胜博",
        47: "12BET",
    }

    def __init__(self):
        self.sm = SportMonksClient()

    async def _log_task(self, task_type: str, status: str, message: str = "", duration_ms: int = 0):
        await AppLogger.log(task_type, status, message, duration_ms)

    async def _find_team_by_alias(self, name: str) -> int | None:
        """通过中文或英文别名查找球队ID（重复别名取第一条）"""
        if not name:
            return None
        async with async_session() as db:
            result = await db.execute(
                select(TeamAlias).where(TeamAlias.alias_name == name).limit(1)
            )
            alias = result.scalar_one_or_none()
            if alias:
                return alias.team_id
            result = await db.execute(
                select(Team).where((Team.name_zh == name) | (Team.name_en == name))
            )
            team = result.scalar_one_or_none()
            if team:
                return team.id
        return None

    async def _find_league_by_name(self, name: str) -> int | None:
        """通过名称查找联赛ID"""
        if not name:
            return None
        async with async_session() as db:
            result = await db.execute(
                select(League).where(
                    (League.name_zh == name) |
                    (League.name_zh.contains(name)) |
                    (League.name_en == name)
                ).limit(1)
            )
            league = result.scalar_one_or_none()
            if league:
                return league.id
            result = await db.execute(
                select(LeagueAlias).where(LeagueAlias.alias_name == name).limit(1)
            )
            alias = result.scalar_one_or_none()
            if alias:
                return alias.league_id
        return None

    # ═══════════════════════════════════════════════════════════════
    # 500.com 赛程同步 → 竞彩网赛程同步
    # ═══════════════════════════════════════════════════════════════

    async def sync_daily_matches(self):
        """同步当日+未来3日竞彩赛程（来源：竞彩网）"""
        start = datetime.utcnow()
        total_new = 0
        total_updated = 0
        new_teams = 0
        try:
            matches = await jczq_scraper.scrape_daily_matches()
            AppLogger.info("sync", f"从竞彩网拉取到 {len(matches)} 场赛事")

            async with async_session() as db:
                for m in matches:
                    jc_id = m.get("jc_match_id", "")
                    if not jc_id:
                        continue

                    home_team_id = await self._find_team_by_alias(m.get("home_team", ""))
                    away_team_id = await self._find_team_by_alias(m.get("away_team", ""))
                    league_id = await self._find_league_by_name(m.get("league_name", ""))

                    # ── 自动创建未匹配球队的占位记录 ──
                    for raw_name, tid_attr, abbr_key in [
                        (m.get("home_team", ""), "home_team_id", "home_team_abbr"),
                        (m.get("away_team", ""), "away_team_id", "away_team_abbr"),
                    ]:
                        tid = home_team_id if tid_attr == "home_team_id" else away_team_id
                        if not raw_name or tid:
                            continue
                        # 创建占位 Team 记录
                        placeholder = Team(
                            name_zh=raw_name,
                            name_en=f"UNKNOWN:{raw_name}",
                            needs_review=True,
                            review_reason="从竞彩网自动新增，需确认SportMonks映射",
                        )
                        db.add(placeholder)
                        await db.flush()  # 获取自增 ID
                        new_teams += 1
                        # 捕获竞彩网英文缩写作为别名，后续 batch-match 可用
                        abbr = m.get(abbr_key, "")
                        if abbr and len(abbr) >= 2:
                            db.add(TeamAlias(team_id=placeholder.id, alias_name=abbr, source="sporttery.cn", is_primary=False))
                        if tid_attr == "home_team_id":
                            home_team_id = placeholder.id
                        else:
                            away_team_id = placeholder.id
                        AppLogger.info("sync", f"  新增球队占位: {raw_name} (id={placeholder.id})")

                    kickoff_str = m.get("kickoff_time", "")
                    kickoff = datetime.now().replace(microsecond=0)
                    if kickoff_str:
                        try:
                            kickoff = datetime.strptime(kickoff_str, "%Y-%m-%d %H:%M:%S")
                        except ValueError:
                            try:
                                kickoff = datetime.strptime(kickoff_str, "%Y-%m-%d %H:%M")
                            except ValueError:
                                pass

                    result = await db.execute(
                        select(Match).where(Match.jc_match_id == jc_id)
                    )
                    existing = result.scalar_one_or_none()

                    if existing:
                        existing.kickoff_time = kickoff
                        existing.handicap_line = m.get("handicap_line", 0.0) or 0.0
                        existing.match_num = m.get("match_num", "")
                        # 始终更新：修正之前可能错误的映射
                        if home_team_id:
                            existing.home_team_id = home_team_id
                        if away_team_id:
                            existing.away_team_id = away_team_id
                        if league_id:
                            existing.league_id = league_id
                        total_updated += 1
                    else:
                        db.add(Match(
                            jc_match_id=jc_id,
                            match_num=m.get("match_num", ""),
                            kickoff_time=kickoff,
                            handicap_line=m.get("handicap_line", 0.0) or 0.0,
                            league_id=league_id,
                            home_team_id=home_team_id,
                            away_team_id=away_team_id,
                            home_team_name=m.get("home_team", ""),
                            away_team_name=m.get("away_team", ""),
                            venue=m.get("league_name", ""),
                        ))
                        total_new += 1

                await db.commit()

            duration = int((datetime.utcnow() - start).total_seconds() * 1000)
            msg = f"新增 {total_new} 场，更新 {total_updated} 场，共 {len(matches)} 场赛事"
            if new_teams > 0:
                msg += f"，自动创建 {new_teams} 支新球队占位"
            AppLogger.info("sync", msg)
            await self._log_task("sync_matches", "success", msg, duration)

        except Exception as e:
            duration = int((datetime.utcnow() - start).total_seconds() * 1000)
            await self._log_task("sync_matches", "failed", f"同步失败: {e}", duration)
            raise

    # ═══════════════════════════════════════════════════════════════
    # SportMonks 赛事匹配
    # ═══════════════════════════════════════════════════════════════

    async def match_to_sportmonks(self):
        """赛事匹配：英文名称优先匹配 SportMonks，无法匹配的标记为待人工处理"""
        start = datetime.utcnow()
        try:
            async with async_session() as db:
                from sqlalchemy.orm import joinedload

                # 获取未匹配的赛事（含关联的 Team）
                result = await db.execute(
                    select(Match)
                    .options(joinedload(Match.home_team), joinedload(Match.away_team))
                    .where(
                        Match.sportmonks_fixture_id.is_(None),
                        Match.kickoff_time >= datetime.utcnow() - timedelta(days=1),
                    )
                )
                unmatched = result.unique().scalars().all()

                if not unmatched:
                    # 查询已匹配赛事数，让日志更有信息量
                    matched_result = await db.execute(
                        select(Match).where(
                            Match.sportmonks_fixture_id.isnot(None),
                            Match.kickoff_time >= datetime.utcnow() - timedelta(days=1),
                        )
                    )
                    matched_count = len(matched_result.scalars().all())
                    await self._log_task("match_fixtures", "success",
                        f"无待匹配赛事（近期已匹配 {matched_count} 场）", 0)
                    return

                # 预加载球队别名 + 联赛中文名
                alias_result = await db.execute(select(TeamAlias))
                all_aliases = alias_result.scalars().all()
                alias_map: dict[int, list] = {}
                league_map: dict[int, str] = {}  # team_id → league_name_zh
                for a in all_aliases:
                    alias_map.setdefault(a.team_id, []).append(a.alias_name.lower().strip())
                    if a.league_name_zh and a.team_id not in league_map:
                        league_map[a.team_id] = a.league_name_zh

                # ── 预解析：为无 sportmonks_id 的球队查找 SportMonks 映射 ──
                def _has_cjk(s: str) -> bool:
                    """判断字符串是否含中日韩字符"""
                    for ch in s:
                        cp = ord(ch)
                        if (0x4E00 <= cp <= 0x9FFF or 0x3400 <= cp <= 0x4DBF
                            or 0x3040 <= cp <= 0x30FF or 0xAC00 <= cp <= 0xD7AF):
                            return True
                    return False

                # 1. 为没有 team_id 的比赛创建占位球队
                placeholder_created = 0
                for m in unmatched:
                    for side, name_attr, tid_attr in [
                        ("home", "home_team_name", "home_team_id"),
                        ("away", "away_team_name", "away_team_id"),
                    ]:
                        tid = getattr(m, tid_attr)
                        raw_name = getattr(m, name_attr, "")
                        if tid or not raw_name:
                            continue
                        placeholder = Team(
                            name_zh=raw_name,
                            name_en=f"UNKNOWN:{raw_name}",
                            needs_review=True,
                            review_reason="从竞彩网自动新增，需确认SportMonks映射",
                        )
                        db.add(placeholder)
                        await db.flush()
                        setattr(m, tid_attr, placeholder.id)
                        placeholder_created += 1
                        AppLogger.info("match_fixtures", f"  创建占位球队: {raw_name} (id={placeholder.id})")

                if placeholder_created > 0:
                    await db.commit()
                    AppLogger.info("match_fixtures", f"为 {placeholder_created} 支缺失球队创建占位记录")
                    # 刷新 session 使 Match 的 joinedload 重新加载 Team 关联
                    db.expire_all()
                    # 重新查询 unmatched 以获取正确的 Team 关联
                    result = await db.execute(
                        select(Match)
                        .options(joinedload(Match.home_team), joinedload(Match.away_team))
                        .where(
                            Match.sportmonks_fixture_id.is_(None),
                            Match.kickoff_time >= datetime.utcnow() - timedelta(days=1),
                        )
                    )
                    unmatched = result.unique().scalars().all()

                # 2. 收集所有无 sportmonks_id 的球队
                unknown_teams: dict[int, Team] = {}
                for m in unmatched:
                    for t, tid in [(m.home_team, m.home_team_id), (m.away_team, m.away_team_id)]:
                        if t and tid and not t.sportmonks_id and tid not in unknown_teams:
                            unknown_teams[tid] = t
                    # 如果 joinedload 没加载到（刚创建的占位），直接查 DB
                    for tid_attr in ["home_team_id", "away_team_id"]:
                        tid = getattr(m, tid_attr)
                        if tid and tid not in unknown_teams:
                            t_result = await db.execute(select(Team).where(Team.id == tid))
                            t = t_result.scalar_one_or_none()
                            if t and not t.sportmonks_id:
                                unknown_teams[tid] = t

                if unknown_teams:
                    AppLogger.info("match_fixtures",
                        f"发现 {len(unknown_teams)} 支球队缺少 SportMonks 映射，将在赛事匹配阶段通过 fixture 反推")

                    # 提交占位球队（如有创建）
                    if placeholder_created > 0:
                        try:
                            await db.commit()
                        except Exception as e:
                            AppLogger.warning("match_fixtures", f"占位球队提交失败: {e}")
                            await db.rollback()

                # 按日期分组
                date_groups: dict[str, list] = {}
                for m in unmatched:
                    d = m.kickoff_time.strftime("%Y-%m-%d")
                    date_groups.setdefault(d, []).append(m)

                total_matched = 0
                needs_manual = []  # 需要人工匹配的赛事
                api_errors = 0  # API 调用失败次数

                def _get_en_variants(team, raw_name: str, team_id: int | None) -> list:
                    """收集纯英文变体（用于 SportMonks 匹配），不含中文，已做 unicode 规范化"""
                    variants = []
                    if team:
                        en = _normalize_name(team.name_en or "")
                        if en and not _has_cjk(en):
                            variants.append(en)
                        zh = _normalize_name(team.name_zh or "")
                        if zh and not _has_cjk(zh):
                            variants.append(zh)
                        # 英文别名
                        for alias in alias_map.get(team.id, []):
                            an = _normalize_name(alias)
                            if not _has_cjk(an) and an not in variants:
                                variants.append(an)
                    # 竞彩网原始名如果是英文也加入
                    raw = _normalize_name(raw_name) if raw_name else ""
                    if raw and not _has_cjk(raw) and raw not in variants:
                        variants.append(raw)
                    return variants

                for date_str, day_matches in date_groups.items():
                    d = datetime.strptime(date_str, "%Y-%m-%d")

                    # V4.11: 改用 fixtures/date 逐日查询（fixtures/between 返回数据不完整，
                    # 仅包含欧冠/欧协联等赛事，缺少挪超/瑞典超/芬超/美职联）
                    sm_fixtures = []
                    query_dates = []
                    for offset in range(-2, 3):  # -2, -1, 0, +1, +2 天
                        query_date = (d + timedelta(days=offset)).strftime("%Y-%m-%d")
                        query_dates.append(query_date)
                        try:
                            day_fixtures = await self.sm.get_fixtures_by_date(
                                query_date, includes="participants"
                            )
                            sm_fixtures.extend(day_fixtures)
                        except Exception as e:
                            api_errors += 1
                            AppLogger.warning("match_fixtures", f"SportMonks 查询失败 date={query_date}: {e}")

                    if not sm_fixtures:
                        AppLogger.warning("match_fixtures", f"SportMonks 日期范围 {query_dates[0]}~{query_dates[-1]} 无赛程返回")
                        continue

                    # 去重（同一 fixture 可能出现在相邻日期的查询结果中）
                    seen_fx = set()
                    deduped = []
                    for fx in sm_fixtures:
                        fxid = fx.get("id")
                        if fxid and fxid not in seen_fx:
                            seen_fx.add(fxid)
                            deduped.append(fx)
                    sm_fixtures = deduped

                    for match in day_matches:
                        home_variants = _get_en_variants(match.home_team, match.home_team_name, match.home_team_id)
                        away_variants = _get_en_variants(match.away_team, match.away_team_name, match.away_team_id)

                        fx_id = None
                        is_swapped = False

                        # ── 策略 A：双方有 SM ID 时，用参与者 ID 精确匹配 ──
                        home_sm_id = match.home_team.sportmonks_id if match.home_team else None
                        away_sm_id = match.away_team.sportmonks_id if match.away_team else None
                        if home_sm_id and away_sm_id:
                            for fx in sm_fixtures:
                                participants = fx.get("participants", [])
                                if len(participants) < 2:
                                    continue
                                pids = {p.get("id") for p in participants if isinstance(p, dict)}
                                if home_sm_id in pids and away_sm_id in pids:
                                    fx_id = fx["id"]
                                    # 用 meta.location 判断方向（SM 不保证 participants[0] 一定是主队）
                                    has_location = False
                                    for p in participants:
                                        if not isinstance(p, dict):
                                            continue
                                        pid = p.get("id")
                                        loc = (p.get("meta") or {}).get("location", "")
                                        if loc:  # 有 location 字段即可信任
                                            has_location = True
                                        if pid == home_sm_id and loc == "away":
                                            is_swapped = True
                                            break
                                        if pid == away_sm_id and loc == "home":
                                            is_swapped = True
                                            break
                                    # 回退：仅在无 location 字段时用数组位置推断
                                    if not has_location and not is_swapped:
                                        if (participants[0].get("id") == away_sm_id and
                                            participants[1].get("id") == home_sm_id):
                                            is_swapped = True
                                    break

                        # ── 策略 B：名称匹配（ID 匹配失败时回退）──
                        if not fx_id:
                            # ── 策略 C：单方有 SM ID 时，通过 fixture 参与者反推缺失球队的 SM ID ──
                            # 核心思路：fixture 中的参与者 ID 来自 SM 官方数据，100% 可靠。
                            # 当一方有 SM ID 且能在 fixture 中找到时，另一方必然是正确映射。
                            if (home_sm_id and not away_sm_id) or (away_sm_id and not home_sm_id):
                                known_sm_id = home_sm_id or away_sm_id
                                unknown_side = "away" if home_sm_id else "home"
                                unknown_team = match.away_team if home_sm_id else match.home_team
                                unknown_name = match.away_team_name if home_sm_id else match.home_team_name
                                unknown_variants = away_variants if home_sm_id else home_variants

                                for fx in sm_fixtures:
                                    participants = fx.get("participants", [])
                                    if len(participants) < 2:
                                        continue
                                    pids = [p.get("id") for p in participants if isinstance(p, dict)]
                                    if known_sm_id not in pids:
                                        continue
                                    # 找到另一方参与者
                                    other_pid = next((pid for pid in pids if pid != known_sm_id), None)
                                    if not other_pid:
                                        continue
                                    other_p = next((p for p in participants if isinstance(p, dict) and p.get("id") == other_pid), {})
                                    other_name = other_p.get("name", "")

                                    # 用 unknown 方的名称变体与 fixture 中的另一方名称匹配
                                    if unknown_variants:
                                        other_normalized = _normalize_name(other_name or "")
                                        for v in unknown_variants:
                                            vn = _normalize_name(v)
                                            if vn and other_normalized and (vn in other_normalized or other_normalized in vn):
                                                # 匹配成功：设置 fixture ID + 反推球队 SM ID
                                                fx_id = fx["id"]
                                                if unknown_team and not unknown_team.sportmonks_id:
                                                    unknown_team.sportmonks_id = other_pid
                                                    unknown_team.name_en = other_name
                                                    unknown_team.needs_review = False
                                                    AppLogger.info("match_fixtures",
                                                        f"  反推球队映射: {unknown_team.name_zh} → SM id={other_pid} name={other_name} (来自 fixture {fx_id})")
                                                break

                        # ── 策略 B（原）：名称双向匹配 ──
                        if not fx_id:
                            # 任一方无英文变体 → 标记人工处理
                            if not home_variants or not away_variants:
                                home_league = league_map.get(match.home_team_id, "")
                                away_league = league_map.get(match.away_team_id, "")
                                needs_manual.append({
                                    "jc_match_id": match.jc_match_id,
                                    "home_name": match.home_team_name,
                                    "away_name": match.away_team_name,
                                    "kickoff": str(match.kickoff_time)[:16],
                                    "home_league": home_league,
                                    "away_league": away_league,
                                    "reason": "球队缺少英文名称，需人工确认 SportMonks 映射",
                                })
                                continue

                            for fx in sm_fixtures:
                                participants = fx.get("participants", [])
                                if len(participants) < 2:
                                    continue
                                # 用 meta.location 确定 SM 的主客队名（不依赖数组位置）
                                sm_home_name = None
                                sm_away_name = None
                                for p in participants:
                                    if not isinstance(p, dict):
                                        continue
                                    name = p.get("name", "")
                                    loc = (p.get("meta") or {}).get("location", "")
                                    if loc == "home":
                                        sm_home_name = name
                                    elif loc == "away":
                                        sm_away_name = name
                                # 回退：无 location 时用数组位置
                                if not sm_home_name or not sm_away_name:
                                    sm_home_name = participants[0].get("name", "")
                                    sm_away_name = participants[1].get("name", "")
                                sm_home = _normalize_name(sm_home_name or "")
                                sm_away = _normalize_name(sm_away_name or "")

                                def _match_any(variants, target):
                                    return any(v and target and (v in target or target in v) for v in variants)

                                # 正向匹配
                                if _match_any(home_variants, sm_home) and _match_any(away_variants, sm_away):
                                    fx_id = fx["id"]
                                    break
                                # 互换匹配
                                if _match_any(home_variants, sm_away) and _match_any(away_variants, sm_home):
                                    fx_id = fx["id"]
                                    is_swapped = True
                                    break

                        if fx_id:
                            match.sportmonks_fixture_id = fx_id
                            total_matched += 1
                            # 标记主客互换（不修改 Match，赔率同步时处理方向修正）
                            if is_swapped:
                                match.is_swapped = True
                                AppLogger.warning("match_fixtures",
                                    f"赛事 {match.jc_match_id} 主客互换（SportMonks vs 竞彩网 顺序相反，赔率将自动修正）")
                            # 提取球队 logo（image_path）并回填 Team.logo_url
                            matched_fx = next((f for f in sm_fixtures if f.get("id") == fx_id), None)
                            if matched_fx:
                                for p in matched_fx.get("participants", []):
                                    if not isinstance(p, dict):
                                        continue
                                    img = p.get("image_path", "")
                                    pid = p.get("id")
                                    if img:
                                        if match.home_team and pid == match.home_team.sportmonks_id and not match.home_team.logo_url:
                                            match.home_team.logo_url = img
                                        if match.away_team and pid == match.away_team.sportmonks_id and not match.away_team.logo_url:
                                            match.away_team.logo_url = img
                        else:
                            # 有英文名但没匹配到 → 也标记人工处理
                            home_league = league_map.get(match.home_team_id, "")
                            away_league = league_map.get(match.away_team_id, "")
                            needs_manual.append({
                                "jc_match_id": match.jc_match_id,
                                "home_name": match.home_team_name,
                                "away_name": match.away_team_name,
                                "kickoff": str(match.kickoff_time)[:16],
                                "home_league": home_league,
                                "away_league": away_league,
                                "reason": f"英文变体(home={home_variants}, away={away_variants})未匹配到 SportMonks",
                            })

                await db.commit()

            # 记录需要人工匹配的赛事
            if needs_manual:
                detail = "; ".join(
                    f"{m['home_name']}vs{m['away_name']}[{m.get('home_league','')}/{m.get('away_league','')}]"
                    for m in needs_manual
                )
                AppLogger.warning("match_fixtures", f"需人工匹配: {detail}")

            duration = int((datetime.utcnow() - start).total_seconds() * 1000)
            msg = f"成功匹配 {total_matched}/{len(unmatched)} 场赛事到 SportMonks"
            if api_errors > 0:
                msg += f"（{api_errors} 个日期 API 查询失败，请检查 SPORTMONKS_API_KEY）"
            if needs_manual:
                msg += f"，{len(needs_manual)} 场需人工处理"
            await self._log_task("match_fixtures", "success", msg, duration)

        except Exception as e:
            duration = int((datetime.utcnow() - start).total_seconds() * 1000)
            await self._log_task("match_fixtures", "failed", str(e), duration)

    # ═══════════════════════════════════════════════════════════════
    # 赔率同步（来源：SportMonks）
    # ═══════════════════════════════════════════════════════════════

    async def sync_odds(self):
        """从 SportMonks 同步赔率（1X2 + 亚盘 + 大小球）"""
        start = datetime.utcnow()
        try:
            # 先确保赛事已匹配
            await self.match_to_sportmonks()

            async with async_session() as db:
                # 仅同步近期赛事（过去24h + 未来7天），避免全量 15k 场历史赛事
                cutoff = datetime.utcnow() - timedelta(hours=24)
                future_cutoff = datetime.utcnow() + timedelta(days=7)
                result = await db.execute(
                    select(Match).where(
                        Match.sportmonks_fixture_id.isnot(None),
                        Match.kickoff_time >= cutoff,
                        Match.kickoff_time <= future_cutoff,
                    )
                )
                matches = result.scalars().all()

                if not matches:
                    await self._log_task("sync_odds", "success", "无已匹配赛事，跳过赔率同步", 0)
                    return

                total_snapshots = 0
                error_count = 0
                for match in matches:
                    try:
                        odds_list = await self.sm.get_odds_pre_match(match.sportmonks_fixture_id)
                    except Exception as e:
                        error_count += 1
                        AppLogger.warning("sync_odds", f"fixture={match.sportmonks_fixture_id} 赔率拉取失败: {e}")
                        continue

                    if not odds_list:
                        continue

                    # 按 bookmaker_id 分组
                    bookmaker_groups: dict[int, list] = {}
                    bookmaker_names: dict[int, str] = {}  # bookmaker_id → 中文名
                    for o in odds_list:
                        bm_id = o.get("bookmaker_id", 0)
                        bookmaker_groups.setdefault(bm_id, []).append(o)
                        if bm_id not in bookmaker_names:
                            # 优先从 API 返回的嵌套 bookmaker 对象中取 name
                            bm_obj = o.get("bookmaker", {})
                            bm_api_name = bm_obj.get("name", "") if isinstance(bm_obj, dict) else ""
                            # 其次用本地中文映射，最后退回 ID
                            bookmaker_names[bm_id] = (
                                self.BOOKMAKER_NAMES.get(bm_id)
                                or bm_api_name
                                or str(bm_id)
                            )

                    snapshot_time = datetime.utcnow()

                    # ── 辅助函数 ──
                    def _safe_float(v):
                        """安全转 float，兼容逗号分隔的复合盘口如 "0, -0.5" """
                        if v is None:
                            return None
                        if isinstance(v, (int, float)):
                            return float(v)
                        try:
                            # 复合盘口 "0,0.5" → 取第一部分（通常更接近平手）
                            s = str(v).replace(" ", "").split(",")[0]
                            return float(s)
                        except (ValueError, TypeError):
                            return None

                    def _norm_side_label(raw: str) -> str | None:
                        """统一主客标签：1/Home/home → home, 2/Away/away → away"""
                        s = raw.strip().lower()
                        if s in ("1", "home"):
                            return "home"
                        if s in ("2", "away"):
                            return "away"
                        return None

                    def _norm_1x2_label(raw: str) -> str | None:
                        """统一胜平负标签：home/1→home, draw/x→draw, away/2→away"""
                        s = raw.strip().lower()
                        if s in ("1", "home"):
                            return "home"
                        if s in ("x", "draw"):
                            return "draw"
                        if s in ("2", "away"):
                            return "away"
                        return None

                    # 取前 3 家博彩公司
                    for bm_id, odds_items in list(bookmaker_groups.items())[:3]:
                        spf = {}                     # 胜平负（所有盘口线共用）
                        hcp_by_line: dict[float, dict] = {}  # 亚盘按 line 分组
                        ou_data = {}                 # 大小球（取首要线，通常 2.5）

                        for o in odds_items:
                            mid = o.get("market_id")
                            label_raw = o.get("label", "")
                            value = o.get("value")

                            if mid == self.MARKET_1X2:
                                key = _norm_1x2_label(label_raw)
                                if key:
                                    spf[key] = value

                            elif mid == self.MARKET_HANDICAP:
                                line = _safe_float(o.get("handicap"))
                                if line is None:
                                    continue
                                key = _norm_side_label(label_raw)
                                if key is None:
                                    continue
                                hcp_by_line.setdefault(line, {})[key] = value

                            elif mid == self.MARKET_OVER_UNDER:
                                # 大小球暂取第一条（通常是 2.5 球，最常见）
                                if not ou_data:
                                    raw = label_raw.strip().lower()
                                    ou_data[raw] = value
                                    line = _safe_float(o.get("total"))
                                    if line is not None:
                                        ou_data["line"] = line

                        # ── 生成快照 ──
                        home_win = _safe_float(spf.get("home"))
                        draw = _safe_float(spf.get("draw"))
                        away_win = _safe_float(spf.get("away"))
                        is_swapped = getattr(match, "is_swapped", False)

                        if is_swapped:
                            home_win, away_win = away_win, home_win

                        if hcp_by_line:
                            # 每个亚盘线生成一条快照
                            for line, hcp in hcp_by_line.items():
                                hh = _safe_float(hcp.get("home"))
                                ha = _safe_float(hcp.get("away"))
                                if hh is None or ha is None:
                                    continue
                                actual_line = line
                                if is_swapped:
                                    hh, ha = ha, hh
                                    actual_line = -line

                                db.add(OddsSnapshot(
                                    match_id=match.id,
                                    snapshot_time=snapshot_time,
                                    bookmaker=bookmaker_names.get(bm_id, str(bm_id)),
                                    home_win=home_win,
                                    draw=draw,
                                    away_win=away_win,
                                    handicap_home=hh,
                                    handicap_line=actual_line,
                                    handicap_away=ha,
                                    over_odds=_safe_float(ou_data.get("over")),
                                    goal_line=ou_data.get("line"),
                                    under_odds=_safe_float(ou_data.get("under")),
                                ))
                                total_snapshots += 1
                        elif spf:
                            # 无亚盘数据但有 1X2，生成一条仅含 1X2 的快照
                            db.add(OddsSnapshot(
                                match_id=match.id,
                                snapshot_time=snapshot_time,
                                bookmaker=bookmaker_names.get(bm_id, str(bm_id)),
                                home_win=home_win,
                                draw=draw,
                                away_win=away_win,
                            ))
                            total_snapshots += 1

                # 标记初盘：尚无初盘的 (match, bookmaker) 组合自动标上最早快照
                match_ids = [m.id for m in matches]
                if match_ids:
                    await db.execute(
                        text("""
                            UPDATE odds_snapshots SET is_opening = TRUE
                            WHERE id IN (
                                SELECT DISTINCT ON (match_id, bookmaker) id
                                FROM odds_snapshots
                                WHERE match_id = ANY(:mids)
                                ORDER BY match_id, bookmaker, snapshot_time ASC
                            )
                            AND NOT EXISTS (
                                SELECT 1 FROM odds_snapshots o2
                                WHERE o2.match_id = odds_snapshots.match_id
                                  AND o2.bookmaker = odds_snapshots.bookmaker
                                  AND o2.is_opening = TRUE
                                  AND o2.id != odds_snapshots.id
                            )
                        """),
                        {"mids": match_ids}
                    )

                await db.commit()

            duration = int((datetime.utcnow() - start).total_seconds() * 1000)
            msg = f"更新 {total_snapshots} 条赔率快照（{len(matches)} 场赛事，来源 SportMonks）"
            if error_count > 0:
                msg += f"，{error_count} 场 API 拉取失败（请检查 SPORTMONKS_API_KEY 是否有效或网络连接）"
            await self._log_task("sync_odds", "success", msg, duration)

        except Exception as e:
            duration = int((datetime.utcnow() - start).total_seconds() * 1000)
            await self._log_task("sync_odds", "failed", str(e), duration)

    # ═══════════════════════════════════════════════════════════════
    # 球队信息同步（来源：SportMonks）
    # ═══════════════════════════════════════════════════════════════

    # SportMonks 赛季详细统计 type_id → 字段映射（来自 /statistics/seasons/teams/{id}）
    # 该 endpoint 返回 details 数组，type_id 映射如下：
    STAT_TYPE_MAP = {
        83: "played",           # Matches Played
        84: "wins",             # Wins
        85: "draws",            # Draws
        86: "losses",           # Losses
        87: "goals_for",        # Goals For
        88: "goals_against",    # Goals Against
        89: "clean_sheets",     # Clean Sheets
        90: "failed_to_score",  # Failed to Score
        209: "avg_possession",  # Average Possession %
        201: "xG",             # Expected Goals（备选 type，部分 endpoint 用）
        202: "xGA",            # Expected Goals Against（备选 type）
        5304: "xG",            # Expected Goals（/statistics/seasons/teams 端点）
    }

    # type_id → 从 value 对象中提取的 key
    STAT_VALUE_KEY = {
        5304: "expected",   # xG: value.expected
        1677: "total",      # Shots: value.total
    }

    async def sync_team_info(self):
        """从 SportMonks 更新当前比赛涉及球队的统计数据、伤病、历史交锋"""
        start = datetime.utcnow()
        try:
            async with async_session() as db:
                # ── 只取当前/未来赛事涉及球队（而非全量245支）──
                cutoff = datetime.utcnow() - timedelta(hours=24)
                match_result = await db.execute(
                    select(Match).where(
                        Match.kickoff_time >= cutoff,
                        Match.home_team_id.isnot(None),
                        Match.away_team_id.isnot(None),
                    )
                )
                matches = match_result.scalars().all()

                # 收集去重后的有 sportmonks_id 球队
                team_ids_seen = set()
                team_map = {}  # local_id → (Team, sm_id)
                for m in matches:
                    for tid in [m.home_team_id, m.away_team_id]:
                        if tid and tid not in team_ids_seen:
                            team_ids_seen.add(tid)

                if not team_ids_seen:
                    await self._log_task("update_teams", "success", "当前无赛事球队，跳过", 0)
                    return

                # 查询这些球队（仅限有 sportmonks_id 的）
                result = await db.execute(
                    select(Team).where(
                        Team.id.in_(list(team_ids_seen)),
                        Team.sportmonks_id.isnot(None),
                    )
                )
                teams = result.scalars().all()

                if not teams:
                    await self._log_task("update_teams", "success",
                        f"当前赛事 {len(team_ids_seen)} 支球队均无 SportMonks 关联，跳过", 0)
                    return

                stats_count = 0
                injury_count = 0
                h2h_count = 0

                for team in teams:
                    sm_id = team.sportmonks_id

                    # ── 阶段 1：拉取球队基础数据（latest 近期比赛 + statistics 赛季列表）──
                    try:
                        team_data = await self.sm.get_team_by_id(
                            sm_id, includes="sidelined;statistics;latest;latest.participants;latest.scores"
                        )
                    except Exception as e:
                        AppLogger.warning("update_teams", f"team={sm_id} 数据拉取失败: {e}")
                        continue

                    # ── 阶段 2：提取近期比赛数据（form + recent_matches）→ 界面展示专用，优先处理 ──
                    # 此阶段独立于历史赛季统计，即使统计 API 全挂也不影响 UI 数据
                    latest_matches = team_data.get("latest", [])
                    form_chars = self._derive_form_from_matches(latest_matches, sm_id)
                    recent = self._extract_recent_matches(latest_matches, sm_id)

                    if form_chars or recent:
                        # 找到或创建当前赛季的 stats 记录来存储 form + recent_matches
                        exist_check = await db.execute(
                            select(TeamSeasonStats).where(
                                TeamSeasonStats.team_id == team.id
                            ).order_by(TeamSeasonStats.season.desc()).limit(1)
                        )
                        target = exist_check.scalar_one_or_none()
                        if not target:
                            target = TeamSeasonStats(team_id=team.id, season="latest")
                            db.add(target)
                            await db.flush()
                        if target:
                            if form_chars and not target.form:
                                target.form = form_chars
                            if recent:
                                target.recent_matches = recent

                            # V4.12: 从 latest 比赛数据聚合赛季统计（played/goals_for/goals_against 等）
                            aggregated = self._aggregate_from_latest(latest_matches, sm_id)
                            if aggregated:
                                for type_id, field_name in self.STAT_TYPE_MAP.items():
                                    if type_id in aggregated:
                                        val = aggregated[type_id]
                                        if getattr(target, field_name, None) in (None, 0):
                                            setattr(target, field_name, val)
                                stats_count += 1

                    # ── 阶段 3：尝试拉取历史赛季详细统计（xG、控球率等）──
                    # 此阶段为可选增强数据，失败不影响界面展示
                    statistics_list = team_data.get("statistics", [])
                    season_id_str = None
                    if isinstance(statistics_list, list):
                        candidates = [s for s in statistics_list if isinstance(s, dict) and s.get("season_id")]
                        active = [s for s in candidates if s.get("has_values")]
                        if active:
                            best = max(active, key=lambda s: s["season_id"])
                        else:
                            best = max(candidates, key=lambda s: s["season_id"]) if candidates else None
                        if best:
                            season_id_str = str(best["season_id"])

                    if season_id_str:
                        season_stats = []
                        try:
                            season_stats = await self.sm.get_statistics_by_season_team(int(season_id_str), sm_id)
                        except Exception:
                            try:
                                season_stats = await self.sm.get_statistics_by_season_team(0, sm_id)
                            except Exception as e:
                                AppLogger.warning("update_teams", f"team={sm_id} 统计详情拉取失败: {e}")

                        # 解析赛季统计数据
                        matched = None
                        for ss in (season_stats if isinstance(season_stats, list) else []):
                            if isinstance(ss, dict) and str(ss.get("season_id", "")) == season_id_str:
                                matched = ss
                                break
                        if not matched and isinstance(season_stats, list) and season_stats:
                            matched = season_stats[-1] if isinstance(season_stats[-1], dict) else None

                        if matched and isinstance(matched, dict):
                            details = matched.get("details", [])
                            if isinstance(details, list):
                                stat_values = {}
                                for item in details:
                                    if not isinstance(item, dict):
                                        continue
                                    type_id = item.get("type_id")
                                    val = item.get("value")
                                    if type_id is None or val is None:
                                        continue
                                    try:
                                        type_id = int(type_id)
                                        if isinstance(val, dict):
                                            extract_key = self.STAT_VALUE_KEY.get(type_id)
                                            if extract_key and extract_key in val:
                                                stat_values[type_id] = float(val[extract_key])
                                            elif "total" in val:
                                                stat_values[type_id] = float(val["total"])
                                            elif "count" in val:
                                                stat_values[type_id] = float(val["count"])
                                            elif "average" in val:
                                                stat_values[type_id] = float(val["average"])
                                        else:
                                            stat_values[type_id] = float(val)
                                    except (ValueError, TypeError):
                                        pass

                                if stat_values:
                                    exist_result = await db.execute(
                                        select(TeamSeasonStats).where(
                                            (TeamSeasonStats.team_id == team.id) &
                                            (TeamSeasonStats.season == season_id_str)
                                        )
                                    )
                                    existing_stats = exist_result.scalar_one_or_none()

                                    if existing_stats:
                                        for type_id, field_name in self.STAT_TYPE_MAP.items():
                                            if type_id in stat_values:
                                                setattr(existing_stats, field_name, stat_values[type_id])
                                    else:
                                        kwargs = {"team_id": team.id, "season": season_id_str}
                                        for type_id, field_name in self.STAT_TYPE_MAP.items():
                                            if type_id in stat_values:
                                                kwargs[field_name] = stat_values[type_id]
                                        db.add(TeamSeasonStats(**kwargs))
                                        stats_count += 1

                                    # V4.12: 将 Stage 2 的 form + recent_matches 合并到赛季统计记录
                                    if season_id_str and target:
                                        # target 是 Stage 2 中 season="latest" 的记录
                                        # 将其数据合并到 season_id_str 记录中
                                        merge_result = await db.execute(
                                            select(TeamSeasonStats).where(
                                                (TeamSeasonStats.team_id == team.id) &
                                                (TeamSeasonStats.season == season_id_str)
                                            )
                                        )
                                        season_record = merge_result.scalar_one_or_none()
                                        if season_record:
                                            if not season_record.form and target.form:
                                                season_record.form = target.form
                                            if not season_record.recent_matches and target.recent_matches:
                                                season_record.recent_matches = target.recent_matches
                                            # 如果 API 统计不完整，用 aggregated stats 补全
                                            if not season_record.played and target.played:
                                                season_record.played = target.played
                                                season_record.wins = target.wins
                                                season_record.draws = target.draws
                                                season_record.losses = target.losses
                                                season_record.goals_for = target.goals_for
                                                season_record.goals_against = target.goals_against
                                        # 同时更新 target 的 season 字段，避免 future queries 读到 "latest"

                    # ── 处理伤病信息 ──
                    sidelined = team_data.get("sidelined", [])
                    if isinstance(sidelined, list):
                        for injury_item in sidelined:
                            player = injury_item.get("player", {}) or {}
                            player_name = player.get("display_name", "")
                            if not player_name:
                                continue

                            db.add(Injury(
                                team_id=team.id,
                                player_name=player_name,
                                type=injury_item.get("type", ""),
                                reason=injury_item.get("reason", ""),
                                start_date=datetime.utcnow(),
                                expected_return=None,
                                status="out",
                            ))
                            injury_count += 1

                await db.commit()

                # ── 同步历史交锋 ──
                h2h_count = await self._sync_head_to_head(db)

            duration = int((datetime.utcnow() - start).total_seconds() * 1000)
            msg = f"更新 {len(teams)} 支球队：{stats_count} 条统计，{injury_count} 条伤病，{h2h_count} 条交锋"
            await self._log_task("update_teams", "success", msg, duration)

        except Exception as e:
            duration = int((datetime.utcnow() - start).total_seconds() * 1000)
            await self._log_task("update_teams", "failed", str(e), duration)

    def _aggregate_from_latest(self, latest_matches: list, sm_id: int) -> dict:
        """从最近比赛结果聚合 played/wins/draws/losses/goals_for/goals_against"""
        if not isinstance(latest_matches, list):
            return {}
        played = wins = draws = losses = gf = ga = 0
        for match in latest_matches:
            if not isinstance(match, dict):
                continue
            result_info = (match.get("result_info") or "").lower()

            # 获取比分（SportMonks scores 格式: [{participant_id, score: {goals}, description: "CURRENT"}]）
            scores = match.get("scores", []) or []
            participants = match.get("participants", []) or []

            # 找到主队和客队的 participant_id
            home_pid = away_pid = None
            for p in participants:
                if not isinstance(p, dict):
                    continue
                loc = (p.get("meta") or {}).get("location", "")
                if loc == "home":
                    home_pid = p.get("id")
                elif loc == "away":
                    away_pid = p.get("id")

            # 提取全场比分（description == "CURRENT"）
            home_goals = away_goals = None
            for s in scores:
                if not isinstance(s, dict):
                    continue
                if s.get("description") != "CURRENT":
                    continue
                pid = s.get("participant_id")
                goals = (s.get("score") or {}).get("goals")
                if pid == home_pid:
                    home_goals = goals
                elif pid == away_pid:
                    away_goals = goals

            if home_goals is not None and away_goals is not None:
                played += 1
                hg = int(home_goals)
                ag = int(away_goals)
                our_pid = sm_id
                if our_pid == home_pid:
                    gf += hg
                    ga += ag
                    if hg > ag:
                        wins += 1
                    elif hg == ag:
                        draws += 1
                    else:
                        losses += 1
                elif our_pid == away_pid:
                    gf += ag
                    ga += hg
                    if ag > hg:
                        wins += 1
                    elif ag == hg:
                        draws += 1
                    else:
                        losses += 1
                else:
                    # 无法判断主客，仅通过 result_info 判断胜负
                    played -= 1  # 回退
                    if "won" in result_info:
                        wins += 1
                    elif "draw" in result_info:
                        draws += 1
                    elif "loss" in result_info or "lost" in result_info:
                        losses += 1
                    else:
                        continue
                    played += 1
            else:
                # 无完整比分数据，通过 result_info 判断
                if "won" in result_info:
                    wins += 1
                elif "draw" in result_info:
                    draws += 1
                elif "loss" in result_info or "lost" in result_info:
                    losses += 1
                else:
                    continue
                played += 1

        return {
            83: played, 87: gf, 88: ga,
            84: wins, 85: draws, 86: losses,
        } if played > 0 else {}

    def _extract_recent_matches(self, latest_matches: list, sm_id: int) -> list:
        """从 latest 比赛提取最近比赛详情 [{opponent, score, result, date}]"""
        if not isinstance(latest_matches, list):
            return []
        results = []
        for match in latest_matches[:10]:
            if not isinstance(match, dict):
                continue
            result_info = (match.get("result_info") or "").lower()
            participants = match.get("participants", []) or []
            scores_list = match.get("scores", []) or []

            # 找到对手 + 判断主客
            opponent_name = "?"
            opponent_sm_id = None
            is_home = False
            for p in participants:
                if not isinstance(p, dict):
                    continue
                pid = p.get("id")
                loc = (p.get("meta") or {}).get("location", "")
                if pid == sm_id:
                    is_home = (loc == "home")
                else:
                    opponent_name = p.get("name") or "?"
                    opponent_sm_id = pid

            # 提取比分
            our_goals = opp_goals = 0
            has_score = False
            for s in scores_list:
                if not isinstance(s, dict) or s.get("description") != "CURRENT":
                    continue
                goals = (s.get("score") or {}).get("goals", 0)
                if s.get("participant_id") == sm_id:
                    our_goals = int(goals) if goals is not None else 0
                else:
                    opp_goals = int(goals) if goals is not None else 0
                has_score = True

            # 判定结果：有比分数据时才判断，否则标记为无数据
            if has_score:
                if our_goals > opp_goals:
                    result = "W"
                elif our_goals == opp_goals:
                    result = "D"
                else:
                    result = "L"
            else:
                result = "-"

            score_str = f"{our_goals}:{opp_goals}" if has_score else "?:?"
            match_date = match.get("starting_at", "")[:10]

            results.append({
                "opponent": opponent_name,
                "opponent_sm_id": opponent_sm_id,
                "score": score_str,
                "result": result,
                "date": match_date,
                "is_home": is_home,
            })
        return results

    def _derive_form_from_matches(self, latest_matches: list, sm_id: int) -> str:
        """从最近比赛推导 W/D/L（基于比分比较，不依赖 result_info）"""
        if not isinstance(latest_matches, list):
            return ""
        form_chars = []
        for match in latest_matches:
            if not isinstance(match, dict):
                continue
            # 解析比分
            participants = match.get("participants", []) or []
            scores_list = match.get("scores", []) or []
            our_goals = opp_goals = -1
            for s in scores_list:
                if not isinstance(s, dict) or s.get("description") != "CURRENT":
                    continue
                goals = (s.get("score") or {}).get("goals")
                if goals is None:
                    continue
                g = int(goals)
                if s.get("participant_id") == sm_id:
                    our_goals = g
                else:
                    opp_goals = g

            if our_goals >= 0 and opp_goals >= 0:
                if our_goals > opp_goals: form_chars.append("W")
                elif our_goals == opp_goals: form_chars.append("D")
                else: form_chars.append("L")
            # 无比分数据的跳过

        return "".join(form_chars[-10:])

    # H2H fixture statistics 关注的 type.code → 内部存储 key（与雷达计算对齐）
    H2H_STAT_CODES = {
        # 进攻
        "shots-total":        "shots",
        "shots-on-target":    "shots_on_target",
        "shots-off-target":   "shots_off",
        "shots-blocked":      "shots_blocked",
        "attacks":            "attacks",
        "dangerous-attacks":  "dangerous",
        # 防守
        "saves":              "saves",
        "fouls":              "fouls",
        # 控球
        "ball-possession":    "possession",
        # 定位球/越位
        "corners":            "corners",
    }

    async def sync_standings(self):
        """V4: 同步联赛积分榜排名数据到 TeamSeasonStats"""
        start = datetime.utcnow()
        try:
            async with async_session() as db:
                from sqlalchemy.orm import joinedload

                # 获取有 SM ID 的活跃联赛
                result = await db.execute(
                    select(League).where(
                        League.sportmonks_id.isnot(None),
                        League.active == True,
                    )
                )
                leagues = result.scalars().all()

                updated = 0
                for league in leagues:
                    # 获取当前赛季
                    try:
                        league_data = await self.sm.get_league_by_id(league.sportmonks_id)
                    except Exception as e:
                        AppLogger.warning("sync_standings", f"get_league_by_id({league.sportmonks_id}) 失败: {e}")
                        continue
                    if not league_data:
                        AppLogger.warning("sync_standings", f"get_league_by_id({league.sportmonks_id}) 返回空")
                        continue
                    d = league_data.get("data", league_data)
                    seasons = d.get("seasons", [])
                    current_season = None
                    for s in seasons:
                        if s.get("is_current"):
                            current_season = s
                            break
                    if not current_season:
                        continue

                    season_id = current_season["id"]
                    season_name = current_season.get("name", "")

                    # 获取积分榜
                    try:
                        standings = await self.sm.get_standings_by_season(season_id)
                    except Exception as e:
                        AppLogger.warning("sync_standings", f"联赛 {league.name_zh} standings 拉取失败: {e}")
                        continue

                    if not standings:
                        continue

                    # 提取排名数据（standings 是平铺列表，不是分组结构）
                    total_teams = len(standings)
                    for row in standings:
                        if not isinstance(row, dict):
                            continue
                        team_id_sm = row.get("participant_id") or row.get("team_id")
                        if not team_id_sm:
                            continue

                        position = row.get("position")
                        if position is None:
                            position = row.get("rank")
                        points = row.get("points")
                        # goal_diff 可能在 stats 子对象中
                        goal_diff = row.get("goal_diff") or row.get("goal_difference")
                        if goal_diff is None and isinstance(row.get("stats"), dict):
                            goal_diff = row["stats"].get("goal_diff") or row["stats"].get("goal_difference")

                        # 找到对应的本地球队
                        team_result = await db.execute(
                            select(Team).where(Team.sportmonks_id == team_id_sm)
                        )
                        team = team_result.scalar_one_or_none()
                        if not team:
                            continue

                        # 更新 TeamSeasonStats（按 team_id 匹配，league_id 可能为 None）
                        stats_result = await db.execute(
                            select(TeamSeasonStats).where(
                                TeamSeasonStats.team_id == team.id,
                                or_(
                                    TeamSeasonStats.league_id == league.id,
                                    TeamSeasonStats.league_id.is_(None),
                                ),
                            ).order_by(TeamSeasonStats.id.desc()).limit(1)
                        )
                        stats = stats_result.scalar_one_or_none()
                        if stats:
                            if position is not None:
                                stats.league_position = int(position)
                            if points is not None:
                                stats.league_points = int(points)
                            if goal_diff is not None:
                                stats.league_goal_diff = int(goal_diff)
                            if not stats.league_id:
                                stats.league_id = league.id
                            if not stats.season or stats.season != season_name:
                                stats.season = season_name
                            updated += 1

                    if updated > 0:
                        AppLogger.info("sync_standings",
                            f"  联赛 {league.name_zh}: 更新 {updated} 队排名")
                    await db.commit()

            duration = int((datetime.utcnow() - start).total_seconds() * 1000)
            await self._log_task("sync_standings", "success",
                f"更新 {updated} 支球队的联赛排名", duration)
            AppLogger.info("sync_standings", f"更新 {updated} 支球队的联赛排名")

        except Exception as e:
            duration = int((datetime.utcnow() - start).total_seconds() * 1000)
            await self._log_task("sync_standings", "failed", str(e), duration)
            raise

    async def _sync_head_to_head(self, db) -> int:
        """同步当前赛事球队的历史交锋记录（含比赛统计数据 + xG）"""
        h2h_count = 0
        try:
            # 同步7天内的赛事（覆盖近期所有比赛）
            cutoff = datetime.utcnow() - timedelta(days=7)
            result = await db.execute(
                select(Match).where(
                    Match.kickoff_time >= cutoff,
                    Match.home_team_id.isnot(None),
                    Match.away_team_id.isnot(None),
                )
            )
            matches = result.scalars().all()

            seen_pairs = set()
            for match in matches:
                t1, t2 = match.home_team_id, match.away_team_id
                pair_key = tuple(sorted([t1, t2]))
                if pair_key in seen_pairs:
                    continue
                seen_pairs.add(pair_key)

                t1_result = await db.execute(select(Team.sportmonks_id).where(Team.id == t1))
                t2_result = await db.execute(select(Team.sportmonks_id).where(Team.id == t2))
                sm_id1 = t1_result.scalar()
                sm_id2 = t2_result.scalar()
                if not sm_id1 or not sm_id2:
                    continue

                # 检查是否已存在
                exist_result = await db.execute(
                    select(HeadToHead).where(
                        ((HeadToHead.home_team_id == t1) & (HeadToHead.away_team_id == t2)) |
                        ((HeadToHead.home_team_id == t2) & (HeadToHead.away_team_id == t1))
                    )
                )
                existing_records = list(exist_result.scalars().all())
                
                # 已有记录且全部有 stats → 跳过
                all_have_stats = existing_records and all(
                    (r.home_stats is not None) for r in existing_records
                )
                if all_have_stats:
                    continue
                
                # 需要补数据的已有记录（按 fixture_id 索引）
                existing_by_fid = {}
                for r in existing_records:
                    if r.sportmonks_fixture_id and (r.home_stats is None):
                        existing_by_fid[r.sportmonks_fixture_id] = r
                # 同时纳入有stats但缺xG的记录（需要补xG）
                for r in existing_records:
                    if r.sportmonks_fixture_id and r.home_stats is not None and r.sportmonks_fixture_id not in existing_by_fid:
                        hs = json.loads(r.home_stats) if isinstance(r.home_stats, str) else r.home_stats
                        if isinstance(hs, dict) and "xG" not in hs:
                            existing_by_fid[r.sportmonks_fixture_id] = r

                try:
                    h2h_data = await self.sm.get_head_to_head(sm_id1, sm_id2)
                except Exception as e:
                    AppLogger.warning("update_teams", f"H2H team={sm_id1} vs {sm_id2} 拉取失败: {e}")
                    continue

                if not isinstance(h2h_data, list):
                    continue

                for h in h2h_data[:6]:
                    fixture_id = h.get("id")
                    match_date_str = h.get("starting_at", "")
                    try:
                        match_date = datetime.strptime(match_date_str[:10], "%Y-%m-%d")
                    except (ValueError, TypeError):
                        match_date = datetime.utcnow()

                    participants = h.get("participants", [])
                    if len(participants) < 2:
                        continue

                    local_team_sm = None
                    # 按 meta.location 识别主队（participants[0] 顺序不可靠）
                    for p in participants:
                        if isinstance(p, dict):
                            meta = p.get("meta") or {}
                            if meta.get("location") == "home":
                                local_team_sm = p.get("id")
                                break
                    if not local_team_sm and participants:
                        local_team_sm = participants[0].get("id")  # fallback

                    # ── 解析比分（include=scores 后为数组格式）──
                    scores_list = h.get("scores", []) or []
                    home_score = away_score = None
                    for s in scores_list:
                        if not isinstance(s, dict) or s.get("description") != "CURRENT":
                            continue
                        goals = (s.get("score") or {}).get("goals")
                        pid = s.get("participant_id")
                        if pid == local_team_sm:
                            home_score = goals
                        else:
                            away_score = goals

                    # 确定主客方向（local_team_sm 已按 meta.location 精确识别为主队）
                    if local_team_sm == sm_id1:
                        ht_id, at_id = t1, t2
                    else:
                        ht_id, at_id = t2, t1

                    home_stats = {}
                    away_stats = {}

                    # 尝试获取该场比赛的详细统计数据
                    if fixture_id:
                        try:
                            fx_data = await self.sm.get_fixture_by_id(
                                fixture_id, includes="statistics.type"
                            )
                            fx_stats = fx_data.get("statistics", [])
                            if isinstance(fx_stats, list):
                                for s in fx_stats:
                                    if not isinstance(s, dict):
                                        continue
                                    tid = s.get("participant_id")
                                    # 使用 type.code 识别统计指标
                                    type_obj = s.get("type") or {}
                                    code = type_obj.get("code", "") if isinstance(type_obj, dict) else ""
                                    if code not in self.H2H_STAT_CODES or not tid:
                                        continue
                                    # fixture statistics 值在 data.value 中
                                    val = (s.get("data") or {}).get("value") if isinstance(s.get("data"), dict) else s.get("data")
                                    if val is None:
                                        continue
                                    try:
                                        v = float(val)
                                    except (ValueError, TypeError):
                                        continue
                                    stat_key = self.H2H_STAT_CODES[code]
                                    if tid == local_team_sm:
                                        home_stats[stat_key] = v
                                    else:
                                        away_stats[stat_key] = v
                        except Exception as e:
                            AppLogger.warning("update_teams", f"H2H fixture={fixture_id} 统计拉取失败: {e}")

                    # ── 获取 xG 数据（来自 trends endpoint，type_id=117）──
                    if fixture_id:
                        try:
                            tx_data = await self.sm.get_fixture_by_id(fixture_id, includes="trends")
                            trends = tx_data.get("trends", [])
                            if isinstance(trends, list):
                                # xG 是随时间累积的，取每队最后（最大 minute）的值
                                xg_vals: dict = {}
                                for t in trends:
                                    if t.get("type_id") == 117:
                                        pid = t.get("participant_id")
                                        minute = t.get("minute", 0)
                                        val = t.get("value", 0)
                                        if pid and val:
                                            cur = xg_vals.get(pid)
                                            if not cur or minute > cur[0]:
                                                xg_vals[pid] = (minute, val)
                                for pid, (_, val) in xg_vals.items():
                                    xg = val / 100
                                    if pid == local_team_sm:
                                        home_stats["xG"] = round(xg, 2)
                                    else:
                                        away_stats["xG"] = round(xg, 2)
                        except Exception:
                            pass  # xG 非关键数据，获取失败不影响主流程

                    # 按 fixture_id 去重 + 存量回填
                    if fixture_id:
                        # 优先检查是否需要回填已有记录
                        if fixture_id in existing_by_fid:
                            existing = existing_by_fid[fixture_id]
                            if home_stats or away_stats:
                                existing.home_stats = home_stats or None
                                existing.away_stats = away_stats or None
                                h2h_count += 1
                            del existing_by_fid[fixture_id]  # 已处理，从待补列表中移除
                            continue
                        
                        # 否则按去重逻辑检查
                        dup_check = await db.execute(
                            select(HeadToHead).where(HeadToHead.sportmonks_fixture_id == fixture_id)
                        )
                        if dup_check.scalar_one_or_none():
                            continue

                    db.add(HeadToHead(
                        home_team_id=ht_id,
                        away_team_id=at_id,
                        match_date=match_date,
                        competition=h.get("league", {}).get("name", "") if isinstance(h.get("league"), dict) else "",
                        home_score=home_score,
                        away_score=away_score,
                        sportmonks_fixture_id=fixture_id,
                        home_stats=home_stats or None,
                        away_stats=away_stats or None,
                    ))
                    h2h_count += 1

                await db.flush()

            # ── 对 existing_by_fid 中未匹配的记录，逐条补拉 stats ──
            if existing_by_fid:
                for fid, existing_rec in list(existing_by_fid.items()):
                    try:
                        fx_data = await self.sm.get_fixture_by_id(fid, includes="statistics.type")
                        fx_stats = fx_data.get("statistics", [])
                        if not isinstance(fx_stats, list) or not fx_stats:
                            continue

                        h_stats, a_stats = {}, {}
                        for s in fx_stats:
                            if not isinstance(s, dict):
                                continue
                            tid = s.get("participant_id")
                            type_obj = s.get("type") or {}
                            code = type_obj.get("code", "") if isinstance(type_obj, dict) else ""
                            if code not in self.H2H_STAT_CODES or not tid:
                                continue
                            val = (s.get("data") or {}).get("value") if isinstance(s.get("data"), dict) else s.get("data")
                            if val is None:
                                continue
                            try:
                                v = float(val)
                            except (ValueError, TypeError):
                                continue
                            stat_key = self.H2H_STAT_CODES[code]
                            # 用 participants 的 meta.location 区分主客
                            participants = fx_data.get("participants", [])
                            pid_side = {}
                            for p in participants:
                                pid = p.get("id") if isinstance(p, dict) else None
                                if pid:
                                    pid_side[pid] = (p.get("meta") or {}).get("location", "home")
                            target = h_stats if pid_side.get(tid, "home") == "home" else a_stats
                            target[stat_key] = v

                        if h_stats:
                            # 代理 xG
                            h_shots = h_stats.get("shots", 0) or 0
                            h_sot = h_stats.get("shots_on_target", 0) or 0
                            a_shots = a_stats.get("shots", 0) or 0
                            a_sot = a_stats.get("shots_on_target", 0) or 0
                            h_stats["xG"] = round(0.07 * float(h_shots) + 0.10 * float(h_sot), 2)
                            a_stats["xG"] = round(0.07 * float(a_shots) + 0.10 * float(a_sot), 2)

                            existing_rec.home_stats = h_stats
                            existing_rec.away_stats = a_stats
                            h2h_count += 1
                    except Exception:
                        pass  # 单条失败不阻塞整体

            await db.commit()
        except Exception as e:
            AppLogger.warning("update_teams", f"H2H 同步异常: {e}")
        return h2h_count
