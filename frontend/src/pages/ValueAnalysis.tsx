import { useEffect, useMemo, useState } from "react";
import {
  getValueAnalysis,
  ValueAnalysisResponse,
  ValueAnalysisItem,
  ValueMarket,
  ValueRiskFlag,
  MarketValue,
} from "../api/client";

const SIDE_ZH: Record<string, string> = { home: "主胜", draw: "平", away: "客胜" };

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
  const keys = Object.keys(outcomes);
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

function MatchCard({ item }: { item: ValueAnalysisItem }) {
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
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {a ? (
            <RiskBadge level={a.risk_summary.overall_risk} />
          ) : (
            <span className="text-xs text-ink-muted">无盘口数据</span>
          )}
          <span className="text-xs text-ink-muted">{open ? "▴" : "▾"}</span>
        </div>
      </button>

      {open && !a && (
        <div className="px-4 pb-4">
          <p className="text-xs text-ink-muted">该场无竞彩盘口快照，无法分析。</p>
        </div>
      )}

      {open && a && (
        <div className="px-4 pb-4 space-y-3 border-t border-border pt-3">
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
  const [date, setDate] = useState<string>(beijingToday);
  const [data, setData] = useState<ValueAnalysisResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

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

  // 按比赛周期分组（保持接口返回的 kickoff 顺序）
  const groups = useMemo(() => {
    const map = new Map<string, ValueAnalysisItem[]>();
    (data?.matches ?? []).forEach((m) => {
      const day = matchday(m);
      if (!map.has(day)) map.set(day, []);
      map.get(day)!.push(m);
    });
    return Array.from(map.entries());
  }, [data]);

  return (
    <div className="max-w-4xl mx-auto px-6 py-6">
      <div className="flex items-center justify-between mb-5">
        <div>
          <h1 className="text-xl font-bold font-heading text-ink">价值分析</h1>
          <p className="text-xs text-ink-muted mt-1">
            锐利盘基准 · 去水 · EV · 风险提示（价值判定仅 1X2 / 大小球方向；其余玩法仅风险）
          </p>
        </div>
        <input
          type="date"
          value={date}
          onChange={(e) => setDate(e.target.value)}
          className="border border-border rounded px-2 py-1 text-sm text-ink bg-white"
        />
      </div>

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
        </div>
      )}

      {loading && <p className="text-sm text-ink-muted">分析中…</p>}
      {error && <p className="text-sm text-red-600">{error}</p>}

      {data && !loading && (
        <div className="space-y-5">
          {data.matches.length === 0 && <p className="text-sm text-ink-muted">该日期无比赛。</p>}
          {groups.map(([day, matches]) => (
            <div key={day}>
              <div className="flex items-center gap-2 mb-2">
                <h2 className="text-sm font-semibold text-ink">{day}</h2>
                <span className="text-xs text-ink-muted">{matches.length} 场</span>
              </div>
              <div className="space-y-2">
                {matches.map((m) => (
                  <MatchCard key={m.match_id} item={m} />
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
