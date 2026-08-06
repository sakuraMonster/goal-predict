import { useState, useEffect, useCallback } from "react";
import { getMatches, getMatchDates, getLeagues } from "../api/client";
import MatchCard from "../components/MatchCard";
import EmptyState from "../components/EmptyState";
import SkeletonCard from "../components/Skeleton";
import ErrorState from "../components/ErrorState";

export default function Dashboard() {
  const [matches, setMatches] = useState<any[]>([]);
  const [dates, setDates] = useState<any[]>([]);
  const [leagues, setLeagues] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedDate, setSelectedDate] = useState(new Date().toISOString().slice(0, 10));
  const [selectedLeague, setSelectedLeague] = useState<number | null>(null);
  const [activeTab, setActiveTab] = useState<"all" | "cold" | "hot">("all");

  const fetchData = useCallback(() => {
    setLoading(true);
    setError(null);
    const params: any = {};
    if (selectedDate) params.date = selectedDate;
    if (selectedLeague) params.league_id = selectedLeague;

    Promise.allSettled([
      getMatches(params),
      getMatchDates(),
      getLeagues(selectedDate),
    ]).then((results) => {
      const [m, d, l] = results;
      if (m.status === "fulfilled") setMatches(m.value.data || []);
      if (d.status === "fulfilled") {
        setDates(d.value.data || []);
        // 默认选中今天或最近的未来日期
        if (!selectedDate && d.value.data?.length) {
          const today = new Date().toISOString().slice(0, 10);
          const todayItem = d.value.data.find((item: any) => item.date === today);
          const futureDates = d.value.data.filter((item: any) => !item.is_past);
          if (todayItem) {
            setSelectedDate(todayItem.date);
          } else if (futureDates.length > 0) {
            setSelectedDate(futureDates[0].date);
          } else {
            setSelectedDate(d.value.data[d.value.data.length - 1].date);
          }
        }
      }
      if (l.status === "fulfilled") setLeagues(l.value.data || []);
    }).catch(() => {
      setError("加载失败");
    }).finally(() => setLoading(false));
  }, [selectedDate, selectedLeague]);

  useEffect(() => { fetchData(); }, [fetchData]);

  const filteredMatches = activeTab === "cold"
    ? matches.filter((m: any) => m.is_cold_match)
    : activeTab === "hot"
    ? matches.filter((m: any) => m.is_hot_match)
    : matches;

  if (error) return <ErrorState message={error} onRetry={fetchData} />;

  return (
    <div className="flex">
      <aside className="w-[150px] bg-parchment-light border-r border-border p-3.5 font-body text-xs text-ink-muted leading-loose flex-shrink-0 overflow-y-auto max-h-[calc(100vh-100px)]">
        <div className="font-semibold text-ink mb-1">日期</div>

        {/* 日期快捷输入 */}
        <div className="mb-2">
          <input
            type="date"
            value={selectedDate}
            onChange={(e) => setSelectedDate(e.target.value)}
            className="w-full border border-border rounded px-1.5 py-1 text-[11px] bg-white text-ink focus:outline-none focus:border-moss"
          />
        </div>

        {/* 历史日期（有赛事的） */}
        {dates.filter((d: any) => d.is_past).length > 0 && (
          <>
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

        {/* 未来日期 */}
        {dates.filter((d: any) => !d.is_past).length > 0 && (
          <>
            <div className="text-[10px] text-ink-muted/60 mb-0.5 mt-1">未来</div>
            {dates.filter((d: any) => !d.is_past).map((d: any) => (
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
        <div className="mt-3 font-semibold text-ink mb-1">联赛</div>
        <div
          className={`cursor-pointer hover:text-moss transition-colors ${!selectedLeague ? "text-moss font-semibold" : ""}`}
          onClick={() => setSelectedLeague(null)}
        >
          全部
        </div>
        {leagues.map((l: any) => (
          <div
            key={l.id}
            className={`cursor-pointer hover:text-moss truncate ${l.id === selectedLeague ? "text-moss font-semibold" : ""}`}
            title={l.name}
            onClick={() => setSelectedLeague(l.id === selectedLeague ? null : l.id)}
          >
            {l.name}{l.count !== undefined ? ` (${l.count})` : ""}
          </div>
        ))}
      </aside>

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
          <span className="font-body text-xs text-ink-light">共 {filteredMatches.length} 场</span>
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
        ) : filteredMatches.length === 0 ? (
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
          <div className="grid grid-cols-3 gap-3">
            {filteredMatches.map((m: any) => (
              <MatchCard key={m.id} {...m} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
