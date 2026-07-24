import { useState, useEffect, useCallback } from "react";
import { getMatches, getMatchDates, getLeagues } from "../api/client";
import MatchCard from "../components/MatchCard";
import EmptyState from "../components/EmptyState";
import SkeletonCard from "../components/Skeleton";

export default function Dashboard() {
  const [matches, setMatches] = useState<any[]>([]);
  const [dates, setDates] = useState<any[]>([]);
  const [leagues, setLeagues] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedDate, setSelectedDate] = useState("");
  const [selectedLeague, setSelectedLeague] = useState<number | null>(null);
  const [activeTab, setActiveTab] = useState<"all" | "cold" | "hot">("all");

  const fetchData = useCallback(() => {
    setLoading(true);
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
        if (d.value.data?.length && !selectedDate) setSelectedDate(d.value.data[0].date);
      }
      if (l.status === "fulfilled") setLeagues(l.value.data || []);
    }).finally(() => setLoading(false));
  }, [selectedDate, selectedLeague]);

  useEffect(() => { fetchData(); }, [fetchData]);

  const filteredMatches = activeTab === "cold"
    ? matches.filter((m: any) => m.is_cold_match)
    : activeTab === "hot"
    ? matches.filter((m: any) => m.is_hot_match)
    : matches;

  return (
    <div className="flex">
      <aside className="w-[150px] bg-parchment-light border-r border-border p-3.5 font-body text-xs text-ink-muted leading-loose flex-shrink-0">
        <div className="font-semibold text-ink mb-1">日期</div>
        {dates.map((d: any) => (
          <div
            key={d.date}
            className={`cursor-pointer ${d.date === selectedDate ? "text-moss font-semibold" : ""}`}
            onClick={() => setSelectedDate(d.date)}
          >
            {d.date.slice(5)} ({d.count}场)
          </div>
        ))}
        <div className="mt-3 font-semibold text-ink mb-1">联赛</div>
        <div
          className={`cursor-pointer ${!selectedLeague ? "text-moss font-semibold" : ""}`}
          onClick={() => setSelectedLeague(null)}
        >
          全部
        </div>
        {leagues.map((l: any) => (
          <div
            key={l.id}
            className={`cursor-pointer hover:text-moss ${l.id === selectedLeague ? "text-moss font-semibold" : ""}`}
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
          <span className="font-body text-[10px] text-ink-light">共 {filteredMatches.length} 场</span>
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
            onNextDay={() => {
              const idx = dates.findIndex((d: any) => d.date === selectedDate);
              if (idx >= 0 && idx < dates.length - 1) setSelectedDate(dates[idx + 1].date);
            }}
          />
        ) : filteredMatches.length === 0 ? (
          activeTab === "cold" ? (
            <div className="flex items-center justify-center py-20">
              <div className="text-center bg-white rounded-lg p-10 border border-border max-w-md">
                <div className="text-5xl mb-3 opacity-30">☐</div>
                <div className="text-base font-bold text-ink mb-2">暂无冷门预警赛事</div>
                <div className="font-body text-xs text-ink-light leading-relaxed">
                  当前筛选日期内的赛事模型置信度较高<br />暂无冷门预警触发
                </div>
                <div className="mt-4">
                  <button onClick={() => setActiveTab("all")} className="bg-moss text-white px-3.5 py-1.5 rounded text-[11px]">
                    查看全部赛事
                  </button>
                </div>
              </div>
            </div>
          ) : activeTab === "hot" ? (
            <div className="flex items-center justify-center py-20">
              <div className="text-center bg-white rounded-lg p-10 border border-border max-w-md">
                <div className="text-5xl mb-3 opacity-30">☐</div>
                <div className="text-base font-bold text-ink mb-2">暂无热门推荐赛事</div>
                <div className="font-body text-xs text-ink-light leading-relaxed">
                  当前暂无模型与市场共识高度一致的赛事<br />建议扩大日期范围查看
                </div>
                <div className="mt-4">
                  <button onClick={() => setActiveTab("all")} className="bg-moss text-white px-3.5 py-1.5 rounded text-[11px]">
                    查看全部赛事
                  </button>
                </div>
              </div>
            </div>
          ) : (
            <EmptyState />
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
