import { useState, useEffect, useCallback } from "react";
import { getReviewSummary, getReviewTrend, getPnL } from "../api/client";
import ErrorState from "../components/ErrorState";

export default function Review() {
  const [summary, setSummary] = useState<any>({});
  const [pnl, setPnl] = useState<any>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [dateRange, setDateRange] = useState("30");

  const fetchData = useCallback(() => {
    setLoading(true);
    setError(null);
    const days = Number(dateRange);
    Promise.all([getReviewSummary(days), getReviewTrend(days), getPnL(days)]).then(([s, , p]) => {
      setSummary(s.data || {});
      setPnl(p.data || {});
    }).catch(() => {
      setError("加载失败");
    }).finally(() => setLoading(false));
  }, [dateRange]);

  useEffect(() => { fetchData(); }, [fetchData]);

  const wlLastWeek = (summary.wl_accuracy_last_week||0)*100;
  const wlCurrent = (summary.wl_accuracy||0)*100;
  const wlTrend = wlCurrent >= wlLastWeek ? "↑" : "↓";
  const wlTrendColor = wlCurrent >= wlLastWeek ? "text-moss" : "text-rust";

  const overviewCards = [
    {label:"累计预测场次", value: summary.total_predictions || 0, color:""},
    {label:"胜平负准确率", value: wlCurrent.toFixed(1)+"%", color:"text-moss", subEl: <span>上周 {wlLastWeek.toFixed(1)}% <span className={wlTrendColor}>{wlTrend}</span></span>},
    {label:"让球准确率", value: ((summary.handicap_accuracy||0)*100).toFixed(1)+"%", color:"text-moss"},
    {label:"进球±1命中率", value: ((summary.goal_accuracy||0)*100).toFixed(1)+"%", color:"text-moss"},
    {label:"比分Top3命中率", value: ((summary.score_top3_accuracy||0)*100).toFixed(1)+"%", color:"text-amber"},
  ];

  if (error) return <ErrorState message={error} onRetry={fetchData} />;

  return (
    <div className="bg-parchment">
      <div className="flex items-center justify-between px-5 py-3 bg-parchment-dark border-b border-border-dark">
        <span className="font-heading text-base font-bold tracking-wider">复盘统计</span>
        <div className="flex items-center gap-3">
          <select
            className="font-body text-xs bg-white border border-border rounded px-2 py-1 text-ink-muted outline-none"
            value={dateRange}
            onChange={(e) => setDateRange(e.target.value)}
          >
            <option value="7">近 7 天</option>
            <option value="30">近 30 天</option>
            <option value="90">近 90 天</option>
          </select>
          <button
            onClick={fetchData}
            disabled={loading}
            className="font-body text-[11px] text-ink-muted hover:text-moss border border-border rounded px-2.5 py-1 transition-colors disabled:opacity-50"
          >
            {loading ? "刷新中..." : "刷新"}
          </button>
        </div>
      </div>

      {/* 概览卡片 */}
      {loading ? (
        <div className="flex gap-3 px-5 py-3.5 border-b border-border">
          {[1,2,3,4,5].map(i => (
            <div key={i} className="flex-1 bg-white rounded-md border border-border p-3 text-center animate-pulse">
              <div className="w-16 h-2 bg-border rounded mx-auto mb-2" />
              <div className="w-12 h-5 bg-border rounded mx-auto" />
            </div>
          ))}
        </div>
      ) : (
        <div className="flex gap-3 px-5 py-3.5 border-b border-border">
          {overviewCards.map((item, i) => (
            <div key={i} className="flex-1 bg-white rounded-md border border-border p-3 text-center font-body">
              <div className="text-xs text-ink-light tracking-wide">{item.label}</div>
              <div className={`text-2xl font-bold mt-1.5 ${item.color || "text-ink"}`}>{item.value}</div>
              {item.sub && <div className="text-xs text-ink-muted mt-0.5">{item.sub}</div>}
              {item.subEl && <div className="text-xs text-ink-muted mt-0.5">{item.subEl}</div>}
            </div>
          ))}
        </div>
      )}

      {/* 图表占位行 */}
      <div className="flex gap-3 px-5 py-3.5 border-b border-border">
        <div className="flex-[1.2] bg-white rounded-md border border-border p-3.5">
          <div className="text-[13px] font-bold mb-2">预测准确率趋势（近{dateRange}天）</div>
          <div className="h-36 bg-parchment-light rounded flex flex-col items-center justify-center font-body text-[11px]">
            <svg className="w-full h-24" viewBox="0 0 300 80">
              <polyline points="10,60 40,55 70,45 100,48 130,30 160,35 190,20 220,25 250,15 280,10" fill="none" stroke="#d4c9b5" strokeWidth="2" strokeLinecap="round"/>
            </svg>
            <span className="text-ink-light mt-1">胜平负/让球/进球±1 趋势 · 数据积累中</span>
          </div>
        </div>
        <div className="flex-[0.8] bg-white rounded-md border border-border p-3.5">
          <div className="text-[13px] font-bold mb-2">盈亏模拟（均注100元）</div>
          <div className="h-28 bg-parchment-light rounded flex flex-col items-center justify-center font-body text-[11px]">
            <svg className="w-full h-20" viewBox="0 0 300 60">
              <polyline points="10,40 40,35 70,42 100,30 130,25 160,28 190,15 220,20 250,10 280,5" fill="none" stroke="#d4c9b5" strokeWidth="2" strokeLinecap="round"/>
              <line x1="0" y1="30" x2="300" y2="30" stroke="#e5dccb" strokeWidth="1" strokeDasharray="4,4" />
            </svg>
            <span className="text-ink-light mt-1">盈亏曲线 · 数据积累中</span>
          </div>
          <div className="flex justify-between mt-2 font-body text-xs">
            <div><div className="text-xs text-ink-light">累计盈亏</div><span className="text-moss font-bold">{pnl.total_profit != null ? `${pnl.total_profit} 元` : "-- 元"}</span></div>
            <div><div className="text-xs text-ink-light">回报率</div><span className="text-moss font-bold">{pnl.roi != null ? `${pnl.roi}%` : "--%"}</span></div>
            <div><div className="text-xs text-ink-light">胜率</div><span className="text-ink font-bold">{pnl.win_rate != null ? `${pnl.win_rate}%` : "--%"}</span></div>
          </div>
        </div>
      </div>
    </div>
  );
}
