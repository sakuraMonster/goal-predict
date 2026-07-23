import { useState, useEffect } from "react";
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
  const [activeTab, setActiveTab] = useState<"all" | "cold" | "hot">("all");

  useEffect(() => {
    Promise.all([getMatches(), getMatchDates(), getLeagues()]).then(([m, d, l]) => {
      setMatches(m.data || []);
      setDates(d.data || []);
      setLeagues(l.data || []);
      if (d.data?.length) setSelectedDate(d.data[0].date);
    }).finally(() => setLoading(false));
  }, []);

  const filteredMatches = activeTab === "cold"
    ? matches.filter((m: any) => m.is_cold_match)
    : activeTab === "hot"
    ? matches.filter((m: any) => m.is_hot_match)
    : matches;

  return (
    <div className="flex">
      {/* 左侧日期联赛栏 */}
      <aside className="w-[150px] bg-parchment-light border-r border-border p-3.5 font-body text-xs text-ink-muted leading-loose flex-shrink-0">
        <div className="font-semibold text-ink mb-1">日期</div>
        {dates.map((d: any) => (
          <div key={d.date} className={`cursor-pointer ${d.date === selectedDate ? "text-moss font-semibold" : ""}`}
               onClick={() => setSelectedDate(d.date)}>
            {d.date} ({d.count}场)
          </div>
        ))}
        <div className="mt-3 font-semibold text-ink mb-1">联赛</div>
        {leagues.map((l: any) => (
          <div key={l.id} className="cursor-pointer hover:text-moss">{l.name}</div>
        ))}
      </aside>

      {/* 右侧内容区 */}
      <div className="flex-1 p-4">
        {/* Tab 筛选 */}
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
          <span className="font-body text-[10px] text-ink-light ml-auto">共 {filteredMatches.length} 场</span>
        </div>

        {/* 卡片网格 */}
        {loading ? (
          <div className="grid grid-cols-3 gap-3">
            {[1,2,3,4,5,6].map((i) => <SkeletonCard key={i} />)}
          </div>
        ) : filteredMatches.length === 0 ? (
          <EmptyState />
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
