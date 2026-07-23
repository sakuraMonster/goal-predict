import { useState } from "react";

export default function Mapping() {
  const [tab, setTab] = useState<"league" | "team">("league");

  return (
    <div className="bg-parchment">
      <div className="flex items-center justify-between px-5 py-3 bg-parchment-dark border-b border-border-dark">
        <span className="font-heading text-base font-bold tracking-wider">名称映射管理</span>
        <span className="font-body text-xs text-ink-muted">赛季 2025-2026</span>
      </div>

      {/* Tab 切换 */}
      <div className="px-5 py-2.5 flex gap-0.5 font-body text-xs">
        <span className={`px-4 py-1.5 rounded cursor-pointer ${tab==="league" ? "bg-white shadow-sm font-semibold text-ink" : "text-ink-muted"}`}
              onClick={() => setTab("league")}>联赛映射</span>
        <span className={`px-4 py-1.5 rounded cursor-pointer ${tab==="league" ? "text-ink-muted" : "bg-white shadow-sm font-semibold text-ink"}`}
              onClick={() => setTab("team")}>球队映射</span>
      </div>

      {/* 统计概览 */}
      <div className="flex gap-3 px-5 mb-3">
        {["总需映射","已自动匹配","待人工确认"].map((label, i) => (
          <div key={i} className="flex-1 bg-white rounded-md border border-border p-3 text-center font-body">
            <div className="text-[10px] text-ink-light">{label}</div>
            <div className={`text-xl font-bold mt-1 ${i===1?"text-moss":i===2?"text-amber":"text-ink"}`}>
              {tab === "league" ? ["12","10","2"][i] : ["186","158","28"][i]}
            </div>
            <div className="text-[10px] text-ink-muted">{i===1?"L1+L2" : i===2?"L3" : ""}</div>
          </div>
        ))}
      </div>

      <div className="px-5">
        <div className="bg-white rounded-md border border-border p-6 text-center text-ink-muted font-body text-sm">
          {tab === "league" ? "联赛映射表（数据接入后显示联赛名→SportMonks 映射关系）" : "球队映射表（数据接入后显示中文队名别名 + 候选匹配）"}
        </div>
      </div>
    </div>
  );
}
