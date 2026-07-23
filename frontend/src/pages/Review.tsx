import { useState, useEffect } from "react";
import { getReviewSummary, getReviewTrend, getPnL } from "../api/client";

export default function Review() {
  const [summary, setSummary] = useState<any>({});
  const [pnl, setPnl] = useState<any>({});

  useEffect(() => {
    Promise.all([getReviewSummary(), getReviewTrend(), getPnL()]).then(([s, , p]) => {
      setSummary(s.data || {});
      setPnl(p.data || {});
    });
  }, []);

  return (
    <div className="bg-parchment">
      <div className="flex items-center justify-between px-5 py-3 bg-parchment-dark border-b border-border-dark">
        <span className="font-heading text-base font-bold tracking-wider">复盘统计</span>
        <span className="font-body text-xs text-ink-muted">近 30 天 · 模型 v0.1.0</span>
      </div>

      {/* 概览卡片 */}
      <div className="flex gap-3 px-5 py-3.5 border-b border-border">
        {[
          {label:"累计预测场次", value: summary.total_predictions || 0, color:""},
          {label:"胜平负准确率", value: ((summary.wl_accuracy||0)*100).toFixed(1)+"%", color:"text-moss", sub: "上周 " + ((summary.wl_accuracy_last_week||0)*100).toFixed(1)+"% ↑"},
          {label:"让球准确率", value: ((summary.handicap_accuracy||0)*100).toFixed(1)+"%", color:"text-moss"},
          {label:"进球±1命中率", value: ((summary.goal_accuracy||0)*100).toFixed(1)+"%", color:"text-moss"},
          {label:"比分Top3命中率", value: ((summary.score_top3_accuracy||0)*100).toFixed(1)+"%", color:"text-amber"},
        ].map((item, i) => (
          <div key={i} className="flex-1 bg-white rounded-md border border-border p-3 text-center font-body">
            <div className="text-[10px] text-ink-light tracking-wide">{item.label}</div>
            <div className={`text-2xl font-bold mt-1.5 ${item.color || "text-ink"}`}>{item.value}</div>
            {item.sub && <div className="text-[10px] text-ink-muted mt-0.5">{item.sub}</div>}
          </div>
        ))}
      </div>

      {/* 图表占位行 */}
      <div className="flex gap-3 px-5 py-3.5 border-b border-border">
        <div className="flex-[1.2] bg-white rounded-md border border-border p-3.5">
          <div className="text-[13px] font-bold mb-2">预测准确率趋势（近30天）</div>
          <div className="h-36 bg-parchment-light rounded flex items-center justify-center text-sand font-body text-[11px]">
            折线图 · 胜平负/让球/进球±1
          </div>
        </div>
        <div className="flex-[0.8] bg-white rounded-md border border-border p-3.5">
          <div className="text-[13px] font-bold mb-2">盈亏模拟（均注100元）</div>
          <div className="h-28 bg-parchment-light rounded flex items-center justify-center text-sand font-body text-[11px]">盈亏曲线图</div>
          <div className="flex justify-between mt-2 font-body text-xs">
            <div><div className="text-[10px] text-ink-light">累计盈亏</div><span className="text-moss font-bold">{pnl.total_profit != null ? `${pnl.total_profit} 元` : "-- 元"}</span></div>
            <div><div className="text-[10px] text-ink-light">回报率</div><span className="text-moss font-bold">{pnl.roi != null ? `${pnl.roi}%` : "--%"}</span></div>
            <div><div className="text-[10px] text-ink-light">胜率</div><span className="text-ink font-bold">{pnl.win_rate != null ? `${pnl.win_rate}%` : "--%"}</span></div>
          </div>
        </div>
      </div>

      <div className="p-5 text-center text-ink-muted font-body text-sm">完整复盘统计图表将在数据积累后逐步上线</div>
    </div>
  );
}
