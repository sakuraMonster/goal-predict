import { useEffect, useMemo, useState } from "react";
import {
  getValueAnalysis,
  computeValueAnalysis,
  getPredictionLabelStats,
  getPredictionLabelMatches,
  ValueAnalysisResponse,
  ValueAnalysisItem,
  ValueMarket,
  ValueRiskFlag,
  MarketValue,
  PredictionLabelStatsResponse,
  PredictionLabelStat,
  PredictionLabelMatchesResponse,
} from "../api/client";
import ValueHistory from "./ValueHistory";

const SIDE_ZH: Record<string, string> = { home: "主胜", draw: "平", away: "客胜" };
const HHAD_ZH: Record<string, string> = { home: "让胜", draw: "让平", away: "让负" };

const RISK_BADGE: Record<string, string> = {
  低: "bg-emerald-50 text-emerald-700 border-emerald-200",
  中: "bg-amber-50 text-amber-700 border-amber-200",
  高: "bg-red-50 text-red-700 border-red-200",
};

// 整体「冷门风险系数」徽标语义：高=正路高(绿)、中=冷门风险中(黄)、低=冷门风险低(绿)
const OVERALL_RISK_LABEL: Record<string, string> = {
  高: "正路高",
  中: "冷门风险中",
  低: "冷门风险低",
};
const OVERALL_RISK_BADGE: Record<string, string> = {
  高: "bg-emerald-50 text-emerald-700 border-emerald-200",
  中: "bg-amber-50 text-amber-700 border-amber-200",
  低: "bg-emerald-50 text-emerald-700 border-emerald-200",
};

const VERDICT_BADGE: Record<string, string> = {
  有价值: "bg-emerald-100 text-emerald-800",
  中性: "bg-gray-100 text-gray-600",
  无价值: "bg-gray-100 text-gray-500",
  大球偏贵: "bg-amber-100 text-amber-700",
  小球偏贵: "bg-amber-100 text-amber-700",
  仅风险: "bg-gray-100 text-gray-500",
  无基准: "bg-gray-100 text-gray-400",
};

function RiskBadge({ level }: { level: string }) {
  return (
    <span className={`inline-block px-2 py-0.5 rounded text-xs font-medium border ${OVERALL_RISK_BADGE[level] ?? OVERALL_RISK_BADGE["中"]}`}>
      {OVERALL_RISK_LABEL[level] ?? `冷门风险·${level}`}
    </span>
  );
}

function VerdictBadge({ verdict }: { verdict: string }) {
  return (
    <span className={`inline-block px-2 py-0.5 rounded text-xs font-semibold ${VERDICT_BADGE[verdict] ?? "bg-gray-100 text-gray-600"}`}>
      {verdict}
    </span>
  );
}

function beijingToday(): string {
  const now = new Date();
  const bj = new Date(now.getTime() + (now.getTimezoneOffset() + 480) * 60000);
  return bj.toISOString().slice(0, 10);
}

const fmtPct = (p: number | null | undefined) => (p == null ? "—" : `${(p * 100).toFixed(1)}%`);
const rateColor = (rate: number | null | undefined) =>
  rate == null ? "text-ink-muted" : rate >= 0.7 ? "text-emerald-600" : rate < 0.5 ? "text-red-500" : "text-ink-muted";
const fmtEv = (ev: number | null | undefined) => (ev == null ? "—" : `${ev >= 0 ? "+" : ""}${(ev * 100).toFixed(1)}%`);
const fmtPp = (pp: number | null | undefined) => (pp == null ? "—" : `${pp >= 0 ? "+" : ""}${(pp * 100).toFixed(1)}pp`);

function sideZh(s: string | undefined | null): string {
  return (s && SIDE_ZH[s]) || s || "";
}

// 比赛周期：优先取 match_num 的「周X」前缀，缺失则用开赛日期
function matchday(item: ValueAnalysisItem): string {
  const m = item.match_num?.match(/^(周[一二三四五六日])/);
  if (m) return m[1];
  return item.kickoff?.slice(0, 10) ?? "未分组";
}

// 视角二（Buchdahl 视角）简要结论
function viewpoint2(item: ValueAnalysisItem): string {
  const a = item.analysis;
  if (!a) return "无竞彩盘口，无法分析。";
  const s = a.risk_summary;
  const v = a.markets["1x2"]?.value;
  const side = sideZh(v?.best_side);
  if (v?.verdict === "有价值" && side && v.best_ev != null) {
    return `有价值：${side} EV ${fmtEv(v.best_ev)}；但整体风险${s.overall_risk}，冷门端方差大，单场不可依赖，需大样本验证。`;
  }
  return `无价值：市场近有效，抽水已吃掉 edge；整体风险${s.overall_risk}。`;
}

// 1X2 赔率/概率/EV 表格
function MarketTable({ market }: { market: ValueMarket }) {
  const outcomes = market.value.outcomes;
  const sharp = market.sharp_odds ?? {};
  if (!outcomes) return null;
  // 固定顺序：主胜 → 平 → 客胜（避免 JSONB 持久化后键序被打乱成 away/draw/home）
  const keys = ["home", "draw", "away"].filter((k) => k in outcomes);
  const best = market.value.best_side;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs border-collapse">
        <thead>
          <tr className="text-ink-muted border-b border-border">
            <th className="text-left font-medium py-1 pr-2">选项</th>
            <th className="text-right font-medium py-1 px-2">竞彩赔率</th>
            <th className="text-right font-medium py-1 px-2">竞彩隐含概率</th>
            <th className="text-right font-medium py-1 px-2">锐利盘赔率</th>
            <th className="text-right font-medium py-1 px-2">真实概率</th>
            <th className="text-right font-medium py-1 px-2">EV差值</th>
            <th className="text-right font-medium py-1 px-2">EV</th>
          </tr>
        </thead>
        <tbody>
          {keys.map((k) => {
            const o = outcomes[k];
            const edgePp = o.fair_prob - 1 / o.odds;
            const isBest = k === best;
            return (
              <tr key={k} className={`border-b border-border ${isBest ? "bg-amber-50/50" : ""}`}>
                <td className="py-1 pr-2 font-medium text-ink whitespace-nowrap">
                  {sideZh(k)}{isBest ? " ★" : ""}
                </td>
                <td className="py-1 px-2 text-right text-ink">{o.odds}</td>
                <td className="py-1 px-2 text-right text-ink-muted">{fmtPct(1 / o.odds)}</td>
                <td className="py-1 px-2 text-right text-ink-muted">{sharp[k] ?? "—"}</td>
                <td className="py-1 px-2 text-right text-ink">{fmtPct(o.fair_prob)}</td>
                <td className={`py-1 px-2 text-right font-medium ${edgePp >= 0 ? "text-emerald-600" : "text-red-500"}`}>
                  {fmtPp(edgePp)}
                </td>
                <td className={`py-1 px-2 text-right font-medium ${o.ev >= 0 ? "text-emerald-600" : "text-red-500"}`}>
                  {fmtEv(o.ev)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function MarketRow({ name, value }: { name: string; value: MarketValue }) {
  return (
    <div className="flex items-center gap-3 text-xs flex-wrap">
      <span className="w-16 shrink-0 text-ink-muted">{name}</span>
      <VerdictBadge verdict={value.verdict} />
      {value.cheap_side && value.edge != null && (
        <span className="text-ink-muted">
          相对便宜 <span className="text-ink font-medium">{value.cheap_side}</span>（错价 {(value.edge * 100).toFixed(1)}pp）
        </span>
      )}
      {value.reason && <span className="text-ink-muted">{value.reason}</span>}
    </div>
  );
}

function RiskFlags({ flags }: { flags: ValueRiskFlag[] }) {
  const [open, setOpen] = useState(false);
  if (!flags?.length) return null;
  return (
    <div>
      <button onClick={() => setOpen((v) => !v)} className="text-xs text-moss hover:underline">
        {open ? "收起风险提示 ▴" : `风险提示（${flags.length} 条） ▾`}
      </button>
      {open && (
        <ul className="mt-1.5 space-y-1.5">
          {flags.map((f) => (
            <li key={f.key} className="text-xs flex gap-2 items-start">
              <span className={`shrink-0 mt-0.5 inline-block px-1.5 py-0.5 rounded text-[11px] border ${RISK_BADGE[f.level]}`}>
                {f.level}
              </span>
              <span className="text-ink-muted leading-relaxed">
                <span className="text-ink font-medium">{f.label}：</span>
                {f.desc}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function MatchCard({ item, labelStat }: { item: ValueAnalysisItem; labelStat?: PredictionLabelStat | null }) {
  const [open, setOpen] = useState(false);
  const a = item.analysis;
  const kickoff = item.kickoff ? item.kickoff.slice(11, 16) : "";
  const oneX2 = a?.markets?.["1x2"];
  const otherMarkets = a ? Object.entries(a.markets).filter(([k]) => k !== "1x2") : [];

  return (
    <div className="bg-white border border-border rounded-lg overflow-hidden">
      <button
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center justify-between gap-3 px-4 py-3 text-left hover:bg-highlight/30 transition-colors"
      >
        <div className="flex items-center gap-2 min-w-0 flex-wrap">
          <span className="text-xs text-ink-muted shrink-0">{item.match_num ?? ""}</span>
          <span className="text-xs text-moss shrink-0">{item.league ?? ""}</span>
          <span className="font-medium text-ink truncate">
            {item.home} <span className="text-ink-muted">vs</span> {item.away}
          </span>
          <span className="text-xs text-ink-muted shrink-0">{kickoff}</span>
          {oneX2 && <VerdictBadge verdict={oneX2.value.verdict} />}
          {a?.prediction && (
            <span className="text-xs font-semibold px-2 py-0.5 rounded bg-moss/10 text-moss border border-moss/20 whitespace-nowrap">
              规则：{a.prediction.label}
            </span>
          )}
          {a?.prediction && labelStat && (
            <span className="text-xs font-medium px-2 py-0.5 rounded bg-white border border-border whitespace-nowrap">
              命中 {labelStat.hit}/{labelStat.n}{" "}
              <span className={rateColor(labelStat.rate)}>{fmtPct(labelStat.rate)}</span>
            </span>
          )}
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {item.computed && a ? (
            <RiskBadge level={a.risk_summary.overall_risk} />
          ) : (
            <span className="text-xs text-ink-muted">未计算</span>
          )}
          <span className="text-xs text-ink-muted">{open ? "▴" : "▾"}</span>
        </div>
      </button>

      {open && !item.computed && (
        <div className="px-4 pb-4">
          <p className="text-xs text-ink-muted">该场尚未计算，请点击右上角「计算」按钮。</p>
        </div>
      )}

      {open && a && (
        <div className="px-4 pb-4 space-y-3 border-t border-border pt-3">
          {/* 预测信息提取 */}
          {a.prediction && (
            <div className="text-xs rounded px-3 py-2 bg-moss/5 border border-moss/20 flex items-center gap-2 flex-wrap">
              <span className="font-semibold text-ink">预测提取</span>
              <span className="font-semibold text-moss">{a.prediction.label}</span>
              {a.prediction.picks.length > 0 && (
                <span className="text-ink-muted">（{a.prediction.picks.map((p) => p.label).join(" + ")}）</span>
              )}
            </div>
          )}

          {/* 视角二结论 */}
          <p className="text-xs bg-parchment-dark rounded px-3 py-2 text-ink leading-relaxed">
            <span className="font-semibold">视角二：</span>
            {viewpoint2(item)}
          </p>

          {/* 1X2 表格 */}
          {oneX2?.value?.outcomes && (
            <div>
              <h4 className="text-xs font-semibold text-ink-muted mb-1.5">胜平负</h4>
              <MarketTable market={oneX2} />
              <RiskFlags flags={oneX2.risk.flags} />
            </div>
          )}

          {/* 其他玩法（紧凑） */}
          {otherMarkets.length > 0 && (
            <div className="space-y-3 border-t border-border pt-2">
              {otherMarkets.map(([k, m]) => (
                <div key={k}>
                  <MarketRow name={m.name} value={m.value} />
                  <RiskFlags flags={m.risk.flags} />
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function ValueAnalysis() {
  const [tab, setTab] = useState<"day" | "history">("day");
  const [date, setDate] = useState<string>(beijingToday);
  const [data, setData] = useState<ValueAnalysisResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [computing, setComputing] = useState(false);
  const [labelStats, setLabelStats] = useState<PredictionLabelStatsResponse | null>(null);
  const [sortMode, setSortMode] = useState<"default" | "hitrate">("default");
  const [selectedLabel, setSelectedLabel] = useState<string | null>(null);
  const [labelMatches, setLabelMatches] = useState<PredictionLabelMatchesResponse | null>(null);
  const [labelMatchesLoading, setLabelMatchesLoading] = useState(false);

  useEffect(() => {
    let alive = true;
    getPredictionLabelStats({ days: 30 })
      .then((d) => { if (alive) setLabelStats(d); })
      .catch(() => {});
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    if (!selectedLabel) {
      setLabelMatches(null);
      return;
    }
    let alive = true;
    setLabelMatchesLoading(true);
    getPredictionLabelMatches({ label: selectedLabel, days: 30 })
      .then((d) => { if (alive) setLabelMatches(d); })
      .catch(() => { if (alive) setLabelMatches(null); })
      .finally(() => { if (alive) setLabelMatchesLoading(false); });
    return () => { alive = false; };
  }, [selectedLabel]);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError(null);
    getValueAnalysis({ date })
      .then((d) => { if (alive) setData(d); })
      .catch((e) => { if (alive) setError(e?.message ?? "加载失败"); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [date]);

  const handleCompute = async () => {
    setComputing(true);
    setError(null);
    try {
      await computeValueAnalysis({ date });
      const d = await getValueAnalysis({ date });
      setData(d);
    } catch (e: any) {
      setError(e?.message ?? "计算失败");
    } finally {
      setComputing(false);
    }
  };

  // 规则标签 → 命中率统计
  const labelRateMap = useMemo(() => {
    const map = new Map<string, PredictionLabelStat>();
    (labelStats?.labels ?? []).forEach((s) => map.set(s.label, s));
    return map;
  }, [labelStats]);

  // 规则标签按优选结果分组（胜 / 负 / 平）
  const labelGroups = useMemo(() => {
    const groups = [
      { key: "home", title: "胜", labels: [] as PredictionLabelStat[] },
      { key: "away", title: "负", labels: [] as PredictionLabelStat[] },
      { key: "draw", title: "平", labels: [] as PredictionLabelStat[] },
    ];
    const other: PredictionLabelStat[] = [];
    (labelStats?.labels ?? []).forEach((s) => {
      if (s.label.endsWith("-胜")) groups[0].labels.push(s);
      else if (s.label.endsWith("-负")) groups[1].labels.push(s);
      else if (s.label.endsWith("-平")) groups[2].labels.push(s);
      else other.push(s);
    });
    if (other.length) groups.push({ key: "other", title: "其他", labels: other });
    return groups.filter((g) => g.labels.length > 0);
  }, [labelStats]);

  // 选中规则的实际赛果统计（胜平负 / 让球胜平负）
  const labelMatchesStats = useMemo(() => {
    const had = { home: 0, draw: 0, away: 0 };
    const hhad = { home: 0, draw: 0, away: 0 };
    (labelMatches?.matches ?? []).forEach((mm) => {
      if (mm.actual in had) had[mm.actual as "home" | "draw" | "away"]++;
      if (mm.hhad) hhad[mm.hhad]++;
    });
    return { had, hhad };
  }, [labelMatches]);

  // 排序后的比赛列表（default：保持接口 kickoff 顺序；hitrate：按规则命中率降序，无预测置底）
  const sortedMatches = useMemo(() => {
    const list = [...(data?.matches ?? [])];
    if (sortMode === "hitrate") {
      const rateOf = (m: ValueAnalysisItem): number | null => {
        const label = m.analysis?.prediction?.label;
        return label ? (labelRateMap.get(label)?.rate ?? null) : null;
      };
      list.sort((a, b) => (rateOf(b) ?? -1) - (rateOf(a) ?? -1));
    }
    return list;
  }, [data, sortMode, labelRateMap]);

  // 按比赛周期分组（保持排序后的顺序）
  const groups = useMemo(() => {
    const map = new Map<string, ValueAnalysisItem[]>();
    sortedMatches.forEach((m) => {
      const day = matchday(m);
      if (!map.has(day)) map.set(day, []);
      map.get(day)!.push(m);
    });
    return Array.from(map.entries());
  }, [sortedMatches]);

  return (
    <div className="max-w-6xl mx-auto px-6 py-6">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h1 className="text-xl font-bold font-heading text-ink">价值分析</h1>
          <p className="text-xs text-ink-muted mt-1">
            锐利盘基准 · 去水 · EV · 风险提示（价值判定仅 1X2 / 大小球方向；其余玩法仅风险）
          </p>
        </div>
        {tab === "day" && (
          <div className="flex items-center gap-2">
            <input
              type="date"
              value={date}
              onChange={(e) => setDate(e.target.value)}
              className="border border-border rounded px-2 py-1 text-sm text-ink bg-white"
            />
            <button
              onClick={handleCompute}
              disabled={computing}
              className="px-3 py-1 rounded text-xs font-medium bg-moss text-white disabled:opacity-50 hover:opacity-90"
            >
              {computing ? "计算中…" : "计算"}
            </button>
          </div>
        )}
      </div>

      <div className="flex gap-1 mb-5 border-b border-border">
        <button
          onClick={() => setTab("day")}
          className={`px-4 py-2 text-sm ${tab === "day" ? "text-moss font-semibold border-b-2 border-moss" : "text-ink-muted hover:text-ink"}`}
        >
          当日分析
        </button>
        <button
          onClick={() => setTab("history")}
          className={`px-4 py-2 text-sm ${tab === "history" ? "text-moss font-semibold border-b-2 border-moss" : "text-ink-muted hover:text-ink"}`}
        >
          历史报告
        </button>
      </div>

      {tab === "day" && (
        <div className="flex gap-6 items-start">
          <div className="flex-1 min-w-0">
            {data && (
            <div className="flex gap-4 mb-5 text-sm">
              <div className="bg-white border border-border rounded-lg px-4 py-2">
                <span className="text-ink-muted">总场次 </span>
                <span className="font-semibold text-ink">{data.total}</span>
              </div>
              <div className="bg-white border border-border rounded-lg px-4 py-2">
                <span className="text-ink-muted">有价值场次 </span>
                <span className={`font-semibold ${data.value_match_count > 0 ? "text-emerald-600" : "text-ink"}`}>
                  {data.value_match_count}
                </span>
              </div>
              <div className="bg-white border border-border rounded-lg px-4 py-2">
                <span className="text-ink-muted">已计算 </span>
                <span className={`font-semibold ${data.computed_count === data.total ? "text-emerald-600" : "text-amber-600"}`}>
                  {data.computed_count} / {data.total}
                </span>
              </div>
            </div>
          )}

          {labelStats && labelStats.labels.length > 0 && (
            <div className="bg-white border border-border rounded-lg p-4 mb-5">
              <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
                <h3 className="text-sm font-semibold text-ink">规则标签命中率</h3>
                <span className="text-xs text-ink-muted">
                  近 {labelStats.days} 天 · 合计 {labelStats.total_hit}/{labelStats.total_n}（{fmtPct(labelStats.total_rate)}）
                </span>
              </div>
              <div className="space-y-3">
                {labelGroups.map((g) => (
                  <div key={g.key}>
                    <div className="text-xs font-semibold text-ink-muted mb-1.5">{g.title}</div>
                    <div className="flex flex-wrap gap-2">
                      {g.labels.map((s) => {
                        const isSelected = selectedLabel === s.label;
                        return (
                          <button
                            key={s.label}
                            onClick={() => setSelectedLabel(isSelected ? null : s.label)}
                            className={`inline-flex items-center gap-1.5 px-2 py-1 rounded border text-xs transition-colors ${
                              s.n === 0 ? "border-border opacity-50" : "border-moss/20"
                            } ${isSelected ? "bg-moss border-moss" : "bg-white hover:bg-highlight/30"}`}
                          >
                            <span className={`font-medium ${isSelected ? "text-white" : "text-ink"}`}>{s.label}</span>
                            <span className={isSelected ? "text-white/80" : "text-ink-muted"}>{s.hit}/{s.n}</span>
                            <span className={`font-semibold ${isSelected ? "text-white" : rateColor(s.rate)}`}>{fmtPct(s.rate)}</span>
                          </button>
                        );
                      })}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {loading && <p className="text-sm text-ink-muted">分析中…</p>}
          {error && <p className="text-sm text-red-600">{error}</p>}

          {data && !loading && (
            <div className="space-y-5">
              {data.matches.length > 0 && (
                <div className="flex items-center justify-end">
                  <div className="inline-flex rounded border border-border overflow-hidden text-xs">
                    <button
                      onClick={() => setSortMode("default")}
                      className={`px-3 py-1 ${sortMode === "default" ? "bg-moss text-white" : "bg-white text-ink-muted hover:bg-highlight/30"}`}
                    >
                      默认排序
                    </button>
                    <button
                      onClick={() => setSortMode("hitrate")}
                      className={`px-3 py-1 ${sortMode === "hitrate" ? "bg-moss text-white" : "bg-white text-ink-muted hover:bg-highlight/30"}`}
                    >
                      按命中率排序
                    </button>
                  </div>
                </div>
              )}
              {data.matches.length === 0 && <p className="text-sm text-ink-muted">该日期无比赛。</p>}
              {groups.map(([day, matches]) => (
                <div key={day}>
                  <div className="flex items-center gap-2 mb-2">
                    <h2 className="text-sm font-semibold text-ink">{day}</h2>
                    <span className="text-xs text-ink-muted">{matches.length} 场</span>
                  </div>
                  <div className="space-y-2">
                    {matches.map((m) => (
                      <MatchCard
                        key={m.match_id}
                        item={m}
                        labelStat={m.analysis?.prediction?.label ? labelRateMap.get(m.analysis.prediction.label) : null}
                      />
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}
          </div>
          {selectedLabel && (
            <aside className="w-80 shrink-0 sticky top-6 bg-white border border-border rounded-lg p-4 max-h-[calc(100vh-3rem)] overflow-y-auto">
              <div className="flex items-start justify-between gap-2 mb-2">
                <h3 className="text-sm font-semibold text-ink break-all">{selectedLabel}</h3>
                <button onClick={() => setSelectedLabel(null)} className="text-xs text-ink-muted hover:text-ink shrink-0">关闭 ✕</button>
              </div>
              {labelMatchesLoading && <p className="text-xs text-ink-muted">加载中…</p>}
              {!labelMatchesLoading && labelMatches && (
                <>
                  <div className="text-xs text-ink-muted mb-3">
                    近 {labelMatches.days} 天 · {labelMatches.hit}/{labelMatches.n}（{fmtPct(labelMatches.rate)}）
                  </div>
                  <div className="bg-parchment-dark rounded px-3 py-2 mb-3">
                    <div className="text-xs font-semibold text-ink mb-1">实际赛果统计</div>
                    <div className="flex gap-3 text-xs">
                      <span className="text-ink-muted">胜 <b className="text-ink">{labelMatchesStats.had.home}</b></span>
                      <span className="text-ink-muted">平 <b className="text-ink">{labelMatchesStats.had.draw}</b></span>
                      <span className="text-ink-muted">负 <b className="text-ink">{labelMatchesStats.had.away}</b></span>
                    </div>
                    <div className="flex gap-3 text-xs mt-1 text-ink-muted">
                      <span>让胜 <b>{labelMatchesStats.hhad.home}</b></span>
                      <span>让平 <b>{labelMatchesStats.hhad.draw}</b></span>
                      <span>让负 <b>{labelMatchesStats.hhad.away}</b></span>
                    </div>
                  </div>
                  {labelMatches.matches.length === 0 ? (
                    <p className="text-xs text-ink-muted">该规则近30天无已结算场次。</p>
                  ) : (
                    <ul className="space-y-2.5">
                      {labelMatches.matches.map((mm, i) => (
                        <li key={i} className="text-xs border-b border-border pb-2.5 last:border-0 last:pb-0">
                          <div className="flex items-center gap-1 flex-wrap text-ink-muted">
                            <span>{mm.matchday.slice(5)}</span>
                            <span>{mm.match_num ?? ""}</span>
                            <span className="text-moss">{mm.league ?? ""}</span>
                          </div>
                          <div className="font-medium text-ink mt-0.5">
                            {mm.home} vs {mm.away}
                            {mm.kickoff && <span className="text-ink-muted font-normal"> {mm.kickoff}</span>}
                          </div>
                          <div className="flex items-center gap-2 mt-1 flex-wrap">
                            <span className="font-semibold text-ink">{mm.score}</span>
                            <span className={mm.prediction_hit ? "text-emerald-600 font-medium" : "text-red-500 font-medium"}>
                              {mm.prediction_hit ? "命中" : "未中"}
                            </span>
                            <span className="text-ink-muted">{mm.picks}</span>
                          </div>
                          <div className="text-ink-muted mt-0.5">
                            实际：{SIDE_ZH[mm.actual]}{mm.hhad ? ` / ${HHAD_ZH[mm.hhad]}` : ""}
                          </div>
                        </li>
                      ))}
                    </ul>
                  )}
                </>
              )}
              {!labelMatchesLoading && !labelMatches && (
                <p className="text-xs text-ink-muted">加载失败。</p>
              )}
            </aside>
          )}
        </div>
      )}

      {tab === "history" && <ValueHistory />}
    </div>
  );
}
