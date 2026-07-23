import { useState, useEffect } from "react";
import { getDailyReport, getDailySummary } from "../api/client";

export default function Report() {
  const [summary, setSummary] = useState<any>({});
  const [report, setReport] = useState<any[]>([]);

  useEffect(() => {
    getDailySummary().then(r => setSummary(r.data || {}));
    getDailyReport().then(r => setReport(r.data || []));
  }, []);

  const coldMatches = report.filter((m: any) => m.is_cold_match);
  const hotMatches = report.filter((m: any) => m.is_hot_match);

  return (
    <div className="bg-parchment">
      <div className="flex items-center justify-between px-5 py-3 bg-parchment-dark border-b border-border-dark">
        <span className="font-heading text-base font-bold tracking-wider">每日预测报告</span>
        <span className="font-body text-xs text-ink-muted">{new Date().toISOString().slice(0,10)}</span>
      </div>

      {/* 概览 */}
      <div className="flex gap-3 px-5 py-3.5 border-b border-border">
        {[
          {label:"开售赛事", value: summary.total_matches || 0, unit:"场"},
          {label:"冷门预警", value: summary.cold_match_count || 0, unit:"场", color:"text-amber"},
          {label:"模型置信度", value: (summary.avg_confidence || 0).toFixed(0) + "%", unit:""},
          {label:"模型版本", value: summary.model_version || "v0.1.0", unit:"", textSm: true},
        ].map((item, i) => (
          <div key={i} className="flex-1 bg-white rounded-md border border-border p-3 text-center font-body">
            <div className="text-[10px] text-ink-light tracking-wide">{item.label}</div>
            <div className={`text-xl font-bold mt-1 ${item.color || "text-ink"} ${item.textSm ? "text-base" : ""}`}>{item.value}</div>
            <div className="text-[10px] text-ink-muted">{item.unit}</div>
          </div>
        ))}
      </div>

      {/* 冷热提示 */}
      <div className="px-5 py-3.5 border-b border-border">
        <div className="flex items-center gap-2 mb-2.5">
          <span className="text-sm font-bold">冷热赛事提示</span>
        </div>
        <div className="flex gap-2.5 font-body text-[11px]">
          {coldMatches.slice(0,2).map((m: any) => (
            <div key={m.id} className="flex-1 bg-white border-l-2 border-amber rounded-r-md p-2.5 border border-l-2">
              <div className="flex items-center gap-1.5">
                <span className="bg-cold-bg text-amber px-1.5 py-0.5 rounded-sm font-semibold text-[10px]">冷</span>
                <span className="font-semibold text-ink">{m.home_team} vs {m.away_team}</span>
              </div>
              <div className="mt-1 text-ink-muted">建议观望</div>
            </div>
          ))}
          {hotMatches.slice(0,2).map((m: any) => (
            <div key={m.id} className="flex-1 bg-white border-l-2 border-moss rounded-r-md p-2.5 border border-l-2">
              <div className="flex items-center gap-1.5">
                <span className="bg-hot-bg text-moss px-1.5 py-0.5 rounded-sm font-semibold text-[10px]">热</span>
                <span className="font-semibold text-ink">{m.home_team} vs {m.away_team}</span>
              </div>
              <div className="mt-1 text-ink-muted">高置信度</div>
            </div>
          ))}
        </div>
      </div>

      {/* 预测清单 */}
      <div className="px-5 py-3.5">
        <div className="text-sm font-bold mb-2.5">预测结果清单</div>
        <div className="bg-white rounded-md border border-border overflow-hidden font-body text-[11px]">
          <div className="flex bg-parchment-light border-b border-border font-semibold text-[10px] text-ink-muted">
            <span className="p-2 w-14">时间</span>
            <span className="p-2 w-14">联赛</span>
            <span className="p-2 flex-1">对阵</span>
            <span className="p-2 w-20 text-center">胜平负</span>
            <span className="p-2 w-16 text-center">进球</span>
            <span className="p-2 w-16 text-center">比分</span>
            <span className="p-2 w-20 text-center">提示</span>
          </div>
          {report.length === 0 && (
            <div className="p-6 text-center text-ink-light">暂无数据</div>
          )}
          {report.map((m: any) => (
            <div key={m.id} className="flex items-center border-b border-highlight last:border-0">
              <span className="p-2 w-14 text-ink-muted">{m.kickoff_time?.slice(11,16)}</span>
              <span className="p-2 w-14 text-ink-light">{m.league_name}</span>
              <span className="p-2 flex-1 font-semibold text-ink">{m.home_team} vs {m.away_team}</span>
              <span className="p-2 w-20 text-center text-moss font-semibold">主 {((m.home_prob||0)*100).toFixed(0)}%</span>
              <span className="p-2 w-16 text-center">{m.expected_goals?.toFixed(1) || "-"} 球</span>
              <span className="p-2 w-16 text-center font-semibold">{m.reference_score || "-"}</span>
              <span className="p-2 w-20 text-center">
                {m.is_cold_match && <span className="bg-cold-bg text-amber px-1.5 py-0.5 rounded-sm text-[10px]">冷门预警</span>}
                {m.is_hot_match && <span className="bg-hot-bg text-moss px-1.5 py-0.5 rounded-sm text-[10px]">热门推荐</span>}
              </span>
            </div>
          ))}
        </div>
        <div className="flex justify-end gap-2.5 mt-3 font-body">
          <button className="bg-white border border-border-dark text-ink px-4 py-1.5 rounded text-[11px]">复制摘要</button>
          <button className="bg-white border border-border-dark text-ink px-4 py-1.5 rounded text-[11px]">导出 CSV</button>
          <button className="bg-white border border-border-dark text-ink px-4 py-1.5 rounded text-[11px]">导出 Excel</button>
          <button className="bg-moss border border-moss text-white px-4 py-1.5 rounded text-[11px]">导出 PDF 报告</button>
        </div>
      </div>
    </div>
  );
}
