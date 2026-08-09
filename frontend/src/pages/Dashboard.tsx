import { useState, useEffect, useCallback, useMemo } from "react";
import { getMatches, getMatchDates, getLeagues } from "../api/client";
import MatchCard from "../components/MatchCard";
import EmptyState from "../components/EmptyState";
import SkeletonCard from "../components/Skeleton";
import ErrorState from "../components/ErrorState";

const WEEKDAY_CN = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];

/** 根据开球时间计算竞彩比赛日（当日12:00~次日12:00归属同一比赛日）
 *  kickoffTime 为北京时间字符串，如 "2026-08-08 03:00:00" */
function getMatchDay(kickoffTime: string): string {
  const [datePart, timePart] = kickoffTime.split(" ");
  const hour = parseInt(timePart, 10);
  if (hour < 12) {
    // 12:00 前的比赛归属前一天比赛周期
    const [y, m, d] = datePart.split("-").map(Number);
    const prev = new Date(Date.UTC(y, m - 1, d - 1));
    return prev.toISOString().slice(0, 10);
  }
  return datePart;
}

/** 格式化日期为 "MM-DD（周X）"，dateStr 为 "YYYY-MM-DD" */
function formatDayLabel(dateStr: string): string {
  const [y, m, d] = dateStr.split("-").map(Number);
  const wday = WEEKDAY_CN[new Date(Date.UTC(y, m - 1, d)).getUTCDay()];
  return `${dateStr.slice(5)}（${wday}）`;
}

/** 解析 match_num（如 "周四001"）为可排序的数值 */
const WEEKDAY_ORDER: Record<string, number> = { "周一": 1, "周二": 2, "周三": 3, "周四": 4, "周五": 5, "周六": 6, "周日": 7 };
function matchNumOrder(matchNum: string): number {
  for (const [prefix, order] of Object.entries(WEEKDAY_ORDER)) {
    if (matchNum.startsWith(prefix)) {
      const num = parseInt(matchNum.slice(prefix.length), 10) || 0;
      return order * 1000 + num;
    }
  }
  return 99999;
}

export default function Dashboard() {
  const [matches, setMatches] = useState<any[]>([]);
  const [dates, setDates] = useState<any[]>([]);
  const [leagues, setLeagues] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedDate, setSelectedDate] = useState(() => {
    const now = new Date();
    return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
  });
  const [selectedLeague, setSelectedLeague] = useState<number | null>(null);
  const [activeTab, setActiveTab] = useState<"all" | "cold" | "hot">("all");
  /** 当前选中的比赛日（默认=今日），用于左侧高亮 */
  const [focusedDay, setFocusedDay] = useState<string | null>(null);

  /** 本地时区的今天日期（非 UTC） */
  const today = (() => {
    const now = new Date();
    return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
  })();
  const isTodayOrFuture = selectedDate >= today;

  /** 计算查询起始日期：未来从 selectedDate 起，历史向前推 (days-1) 天 */
  const queryDate = useMemo(() => {
    if (isTodayOrFuture) return selectedDate;
    // 历史：往前推4天，使 days=5 覆盖 [selectedDate-4, selectedDate]
    const [y, m, d] = selectedDate.split("-").map(Number);
    return new Date(Date.UTC(y, m - 1, d - 4)).toISOString().slice(0, 10);
  }, [selectedDate, isTodayOrFuture]);

  const queryDays = isTodayOrFuture ? 3 : 5;

  const fetchData = useCallback(() => {
    setLoading(true);
    setMatches([]);  // 清空旧数据，避免切换日期时短暂显示上一批数据
    setError(null);
    const params: any = {};
    if (queryDate) params.date = queryDate;
    if (selectedLeague) params.league_id = selectedLeague;
    params.days = queryDays;

    Promise.allSettled([
      getMatches(params),
      getMatchDates(),
      getLeagues(selectedDate),
    ]).then((results) => {
      const [m, d, l] = results;
      if (m.status === "fulfilled") {
        setMatches(m.value.data || []);
      } else {
        setError("赛事加载失败，请重试");
      }
      if (d.status === "fulfilled") {
        const datesData = d.value.data || [];
        setDates(datesData);
        if (datesData.length) {
          const dateExists = datesData.some((item: any) => item.date === selectedDate);
          if (!dateExists) {
            const todayItem = datesData.find((item: any) => item.date === today);
            const futureDates = datesData.filter((item: any) => !item.is_past);
            if (todayItem) {
              setSelectedDate(todayItem.date);
            } else if (futureDates.length > 0) {
              setSelectedDate(futureDates[0].date);
            } else {
              setSelectedDate(datesData[0].date);
            }
          }
        }
      }
      if (l.status === "fulfilled") setLeagues(l.value.data || []);
    }).catch(() => {
      setError("加载失败");
    }).finally(() => setLoading(false));
  }, [queryDate, selectedLeague, queryDays]);

  useEffect(() => { fetchData(); }, [fetchData]);

  // 同步 focusedDay：未来默认聚焦今天，历史默认聚焦 selectedDate
  useEffect(() => {
    setFocusedDay(isTodayOrFuture ? today : selectedDate);
  }, [selectedDate, today, isTodayOrFuture]);

  // 生成侧边栏展示的比赛日列表：未来3天 / 历史5天
  const matchDays = useMemo(() => {
    const count = isTodayOrFuture ? 3 : 5;
    const base = isTodayOrFuture ? selectedDate : queryDate;
    const [by, bm, bd] = base.split("-").map(Number);
    return Array.from({ length: count }, (_, i) => {
      return new Date(Date.UTC(by, bm - 1, bd + i)).toISOString().slice(0, 10);
    });
  }, [selectedDate, queryDate, isTodayOrFuture]);

  // 按比赛日分组，每组内按 match_num 正序排列
  const groupedMatches = useMemo(() => {
    const groups: Record<string, any[]> = {};
    const allFiltered = activeTab === "cold"
      ? matches.filter((m: any) => m.is_cold_match)
      : activeTab === "hot"
      ? matches.filter((m: any) => m.is_hot_match)
      : matches;

    for (const m of allFiltered) {
      const day = getMatchDay(m.kickoff_time);
      if (!groups[day]) groups[day] = [];
      groups[day].push(m);
    }
    // 每组内按 match_num 正序排序
    for (const day of Object.keys(groups)) {
      groups[day].sort((a, b) => matchNumOrder(a.match_num || "") - matchNumOrder(b.match_num || ""));
    }
    return groups;
  }, [matches, activeTab]);

  // 按比赛日顺序排列
  const sortedDays = useMemo(() => {
    return Object.keys(groupedMatches).sort();
  }, [groupedMatches]);

  const totalFiltered = Object.values(groupedMatches).reduce((sum, arr) => sum + arr.length, 0);

  if (error) return <ErrorState message={error} onRetry={fetchData} />;

  return (
    <div className="flex">
      {/* ── 左侧边栏 ── */}
      <aside className="w-[150px] bg-parchment-light border-r border-border p-3.5 font-body text-xs text-ink-muted leading-loose flex-shrink-0 overflow-y-auto max-h-[calc(100vh-100px)]">
        <div className="font-semibold text-ink mb-1">日期</div>
        <div className="mb-2">
          <input
            type="date"
            value={selectedDate}
            onChange={(e) => setSelectedDate(e.target.value)}
            className="w-full border border-border rounded px-1.5 py-1 text-[11px] bg-white text-ink focus:outline-none focus:border-moss"
          />
        </div>

        {/* 比赛周期列表 */}
        {matchDays.length > 0 && (
          <>
            <div className="text-[10px] text-ink-muted/60 mb-0.5 mt-1">
              {isTodayOrFuture ? "未来3日" : "近5日"}
            </div>
            {matchDays.map((day) => {
              const dayMatches = groupedMatches[day] || [];
              const isFocused = focusedDay === day;
              return (
                <div
                  key={day}
                  className={`cursor-pointer hover:text-moss transition-colors flex justify-between ${isFocused ? "text-moss font-semibold" : ""}`}
                  onClick={() => setFocusedDay(day === focusedDay ? null : day)}
                >
                  <span>{formatDayLabel(day)}</span>
                  <span className="text-ink-light font-normal">({dayMatches.length})</span>
                </div>
              );
            })}
          </>
        )}

        {/* 历史日期快捷入口（仅未来模式显示） */}
        {isTodayOrFuture && dates.filter((d: any) => d.is_past).length > 0 && (
          <>
            <div className="border-t border-border my-2" />
            <div className="text-[10px] text-ink-muted/60 mb-0.5 mt-1">历史</div>
            {dates.filter((d: any) => d.is_past).slice(-7).map((d: any) => (
              <div
                key={d.date}
                className={`cursor-pointer hover:text-moss transition-colors ${d.date === selectedDate ? "text-moss font-semibold" : ""}`}
                onClick={() => setSelectedDate(d.date)}
              >
                {d.date.slice(5)} ({d.count}场)
              </div>
            ))}
          </>
        )}

        <div className="border-t border-border my-2" />

        <div className="mt-3 font-semibold text-ink mb-1">联赛</div>
        <div
          className={`cursor-pointer hover:text-moss transition-colors ${!selectedLeague ? "text-moss font-semibold" : ""}`}
          onClick={() => setSelectedLeague(null)}
        >
          全部
        </div>
        {leagues.map((l: any) => (
          <div
            key={l.id != null ? l.id : `venue:${l.name}`}
            className={`cursor-pointer hover:text-moss truncate ${l.id === selectedLeague ? "text-moss font-semibold" : ""}`}
            title={l.name}
            onClick={() => setSelectedLeague(l.id === selectedLeague ? null : l.id)}
          >
            {l.name}{l.count !== undefined ? ` (${l.count})` : ""}
          </div>
        ))}
      </aside>

      {/* ── 主内容区 ── */}
      <div className="flex-1 p-4">
        <div className="flex items-center gap-3 mb-3">
          <div className="flex gap-0.5 bg-parchment-dark rounded p-0.5 font-body text-xs">
            {(["all", "cold", "hot"] as const).map((tab) => (
              <span key={tab}
                className={`px-3.5 py-1.5 rounded cursor-pointer transition-colors ${
                  activeTab === tab ? "bg-white text-ink font-semibold shadow-sm" : "text-ink-muted"
                }`}
                onClick={() => setActiveTab(tab)}
              >
                {tab === "all" ? "全部赛事" : tab === "cold" ? "冷门预警" : "热门推荐"}
              </span>
            ))}
          </div>
          <span className="font-body text-xs text-ink-light">共 {totalFiltered} 场</span>
          <button
            onClick={fetchData}
            disabled={loading}
            className="ml-auto font-body text-[11px] text-ink-muted hover:text-moss border border-border rounded px-2.5 py-1 transition-colors disabled:opacity-50"
          >
            {loading ? "刷新中..." : "刷新"}
          </button>
        </div>

        {loading ? (
          <div className="grid grid-cols-3 gap-3">
            {[1,2,3,4,5,6].map((i) => <SkeletonCard key={i} />)}
          </div>
        ) : matches.length === 0 ? (
          <EmptyState
            message="当日无竞彩赛事"
            description="暂无竞彩足球开售赛事，请选择其他日期查看"
            actionLabel="查看明日"
            onAction={() => {
              const idx = dates.findIndex((d: any) => d.date === selectedDate);
              if (idx >= 0 && idx < dates.length - 1) setSelectedDate(dates[idx + 1].date);
            }}
          />
        ) : totalFiltered === 0 ? (
          activeTab === "cold" ? (
            <EmptyState
              message="暂无冷门预警赛事"
              description="当前筛选日期内的赛事模型置信度较高，暂无冷门预警触发"
              actionLabel="查看全部赛事"
              onAction={() => setActiveTab("all")}
            />
          ) : activeTab === "hot" ? (
            <EmptyState
              message="暂无热门推荐赛事"
              description="当前暂无模型与市场共识高度一致的赛事，建议扩大日期范围查看"
              actionLabel="查看全部赛事"
              onAction={() => setActiveTab("all")}
            />
          ) : (
            <EmptyState message="当日无竞彩赛事" description="暂无竞彩足球开售赛事，请选择其他日期查看" />
          )
        ) : (
          <div className="space-y-4">
            {sortedDays.map((day) => {
              const dayMatches = groupedMatches[day];
              if (!dayMatches || dayMatches.length === 0) return null;
              // focusedDay 筛选：如果设置了 focusedDay，只展示该日
              if (focusedDay && day !== focusedDay) return null;
              return (
                <div key={day}>
                  <h3 className="font-body text-sm font-semibold text-ink mb-2 pl-1 border-l-2 border-moss">
                    {formatDayLabel(day)}
                    <span className="text-ink-muted font-normal text-xs ml-1.5">
                      {dayMatches.length} 场
                    </span>
                  </h3>
                  <div className="grid grid-cols-3 gap-3">
                    {dayMatches.map((m: any) => (
                      <MatchCard key={m.id} {...m} />
                    ))}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
