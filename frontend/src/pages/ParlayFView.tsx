import { useCallback, useEffect, useMemo, useState } from "react";
import {
  getMarketFlowParlayF,
  getMarketFlowParlayFinalHistory,
  predictMarketFlow,
  syncMarketFlowOdds,
  syncMarketFlowSmOdds,
  type ParlayFResponse,
  type ParlayFPick,
  type ParlayFLeg,
  type ParlayFinalHistory,
  type ParlayFinalHistoryRow,
  type ParlayFinalHistLeg,
} from "../api/client";
import EmptyState from "../components/EmptyState";
import ErrorState from "../components/ErrorState";
import { useToast } from "../components/Toast";

const today = () => new Date().toISOString().slice(0, 10);

function LegCard({ lg }: { lg: ParlayFLeg }) {
  const isHalf = lg.kind === "halfdraw";
  const code = lg.code ?? "";
  const badge = isHalf
    ? code === "R1"
      ? "半平·首选 R1"
      : code === "R2"
        ? "半全场·次选 R2"
        : "半全场"
    : "D方向腿";
  const badgeColor = isHalf
    ? code === "R1" ? "bg-sand/15 text-sand" : "bg-amber/15 text-amber"
    : "bg-moss/15 text-moss";
  return (
    <div className="flex-1 min-w-[220px] bg-parchment-light/50 rounded-md border border-highlight p-3">
      <div className="flex items-center gap-2 flex-wrap">
        <span className="font-mono text-[11px] text-ink">{lg.match_num || "-"}</span>
        <span className="text-[10px] text-ink-light">{lg.league_name || ""}</span>
        <span className={`ml-auto text-[10px] px-1.5 py-0.5 rounded ${badgeColor}`}>{badge}</span>
      </div>
      <div className="text-[12px] text-ink mt-1">
        {lg.home_team || "?"} <span className="text-ink-muted text-[10px]">vs</span> {lg.away_team || "?"}
      </div>
      <div className="flex items-center gap-2 mt-1.5 flex-wrap">
        {isHalf && lg.played && lg.played.length > 0 ? (
          lg.played.map((p) => (
            <span key={p.key} className="font-heading font-bold text-[15px] text-ink">
              {p.zh}@{p.odds.toFixed(2)}
            </span>
          ))
        ) : (
          <span className="font-heading font-bold text-[15px] text-ink">
            {lg.pick}@{lg.odds?.toFixed(2)}
          </span>
        )}
        {isHalf && lg.stake ? (
          <span className="text-[10px] text-ink-muted">{lg.stake} 注</span>
        ) : null}
        {lg.hit == null ? (
          <span className="text-[10px] text-ink-light bg-parchment-dark rounded px-1.5 py-0.5">未结算</span>
        ) : lg.hit ? (
          <span className="text-[10px] text-win font-bold bg-win/10 rounded px-1.5 py-0.5">命中 {lg.actual}</span>
        ) : (
          <span className="text-[10px] text-lose font-bold bg-lose/10 rounded px-1.5 py-0.5">未中 {lg.actual}</span>
        )}
      </div>
    </div>
  );
}

function FinalStatBox({ label, value, sub, highlight }: { label: string; value: string; sub?: string; highlight?: boolean }) {
  return (
    <div className={`rounded-md border p-3 text-center ${highlight ? "border-moss bg-moss/[0.03]" : "border-border bg-white"}`}>
      <div className={`text-[10px] ${highlight ? "text-moss" : "text-ink-light"}`}>{label}</div>
      <div className={`text-xl font-bold font-heading mt-0.5 leading-tight ${highlight ? "text-moss" : "text-ink"}`}>{value}</div>
      {sub && <div className="text-[10px] text-ink-muted mt-0.5">{sub}</div>}
    </div>
  );
}

function comboLabel(code?: string) {
  if (code === "R1") return "R1·半平";
  if (code === "R2") return "R2·半全场次选";
  if (code === "R2_fallback") return "R2兜底（R1与方向腿同场）";
  return code ?? "-";
}

export default function ParlayFView({
  initialDate,
  onNavigate,
}: {
  initialDate?: string;
  onNavigate?: (k: string) => void;
}) {
  const [mode, setMode] = useState<"range" | "day">("day");
  const [date, setDate] = useState(initialDate || today());
  const [start, setStart] = useState("2026-08-01");
  const [end, setEnd] = useState(today());
  const toast = useToast();
  const [data, setData] = useState<ParlayFResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchData = useCallback(() => {
    setLoading(true);
    setError(null);
    const params = mode === "day" ? { date } : { start_date: start, end_date: end };
    getMarketFlowParlayF(params)
      .then((res) => setData(res))
      .catch(() => setError("加载失败"))
      .finally(() => setLoading(false));
  }, [mode, date, start, end]);
  useEffect(() => {
    fetchData();
  }, [fetchData]);

  // 生成方案：同步当日赔率（TTG/HAFU + SM O/U）→ 执行 MarketFlow 预测 → 重新拉取方案G
  const genPlans = useCallback(async () => {
    setGenerating(true);
    setError(null);
    try {
      const d = mode === "day" ? date : today();
      await syncMarketFlowOdds({ date: d });
      await syncMarketFlowSmOdds({ date: d });
      await predictMarketFlow({ date: d, overwrite: true });
      toast.toast(`已同步 ${d} 赔率并生成 MarketFlow 预测`, "success");
      fetchData();
    } catch {
      setError("同步/生成失败");
    } finally {
      setGenerating(false);
    }
  }, [mode, date, fetchData, toast]);

  const st = data?.stats;
  const stats = useMemo(
    () => [
      { label: "已结算串", value: `${st?.n ?? 0}` },
      { label: "命中", value: `${st?.hit ?? 0}` },
      { label: "命中率 p_hit", value: st?.p_hit != null ? `${(st.p_hit * 100).toFixed(1)}%` : "-" },
      { label: "注数", value: `${st?.total_stake ?? 0}` },
      { label: "返奖", value: st?.total_payout != null ? st.total_payout.toFixed(2) : "-" },
      { label: "ROI（返奖/下注，倍率）", value: st?.roi != null ? `${st.roi.toFixed(2)}×` : "-" },
    ],
    [st]
  );

  // 人工终稿 · 已确认（parlay-final 落库）：方案G 系统默认 vs 人工终稿 分开显示/统计
  const [finalHist, setFinalHist] = useState<ParlayFinalHistory | null>(null);
  const [finalCollapsed, setFinalCollapsed] = useState(false);
  const loadFinalHist = useCallback(async () => {
    try {
      setFinalHist(await getMarketFlowParlayFinalHistory());
    } catch {
      setFinalHist(null);
    }
  }, []);
  useEffect(() => {
    loadFinalHist();
  }, [loadFinalHist]);

  const finalRows = useMemo<ParlayFinalHistoryRow[]>(() => {
    if (!finalHist) return [];
    return (finalHist.rows || [])
      .filter((r) => r.plan === "G" && (mode === "day" ? r.pick_date === date : r.pick_date >= start && r.pick_date <= end))
      .sort((a, b) => b.pick_date.localeCompare(a.pick_date));
  }, [finalHist, mode, date, start, end]);
  const finalStats = useMemo(() => {
    const n = finalRows.length;
    const settled = finalRows.filter((r) => r.default.settled && r.final.settled);
    const defHit = settled.filter((r) => r.default.hit === true).length;
    const finHit = settled.filter((r) => r.final.hit === true).length;
    const sum = (xs: Array<number | null | undefined>) => xs.reduce<number>((a, b) => a + (Number(b) || 0), 0);
    const defStake = sum(finalRows.map((r) => r.default.stake));
    const finStake = sum(finalRows.map((r) => r.final.stake));
    return {
      n,
      settled_n: settled.length,
      def_p_hit: settled.length ? defHit / settled.length : null,
      fin_p_hit: settled.length ? finHit / settled.length : null,
      def_roi: defStake ? sum(finalRows.map((r) => r.default.payout)) / defStake : null,
      fin_roi: finStake ? sum(finalRows.map((r) => r.final.payout)) / finStake : null,
      def_hit_n: defHit,
      fin_hit_n: finHit,
      improved_n: settled.filter((r) => r.default.hit !== true && r.final.hit === true).length,
      worsened_n: settled.filter((r) => r.default.hit === true && r.final.hit !== true).length,
    };
  }, [finalRows]);
  const legBrief = (lg: ParlayFinalHistLeg) => {
    const picks = (lg.picks?.length ? lg.picks.map((p) => p.pick ?? p.key).join("/") : lg.pick) ?? "";
    const hitTxt = lg.hit == null ? "未结算" : lg.hit ? "✓命中" : "✗未中";
    return `${lg.match_num ? `${lg.match_num} ` : ""}${lg.home_team ?? "?"} vs ${lg.away_team ?? "?"} ${picks}@${lg.odds?.toFixed(2) ?? "-"} ${hitTxt}${lg.hit === false && lg.actual ? `(${lg.actual})` : ""}`;
  };

  return (
    <div className="space-y-4 tabular-nums">
      <div className="px-4 py-3 bg-parchment-dark rounded-lg border border-border-dark space-y-2">
        <div className="flex items-center gap-3 min-h-8">
          <span className="flex items-center gap-2 min-w-0 flex-1 text-xs text-moss font-semibold"
            title="方案G · halfdraw(R1半平 / R2半全场次选) × 方案D方向腿 · 2串1 每日1串">
            <span className="w-2 h-2 rounded-full bg-moss shrink-0" />
            <span className="truncate">方案G · halfdraw(R1半平 / R2半全场次选) × 方案D方向腿 · 2串1 每日1串</span>
          </span>
          <div className="flex items-center gap-2 shrink-0">
            <button onClick={genPlans} disabled={generating}
              className="px-3 py-1.5 text-xs bg-amber text-white rounded hover:bg-amber/90 transition-colors disabled:opacity-50 font-semibold"
              title="同步当日赔率（TTG/HAFU + SM O/U）→ 执行 MarketFlow 预测 → 生成方案G（halfdraw × 方案D方向腿）">
              {generating ? "同步赔率生成中..." : "生成方案"}
            </button>
            <div className="flex gap-0.5 bg-white rounded p-0.5 border border-border text-[11px]">
              <span className={`px-2 py-0.5 rounded cursor-pointer ${mode === "day" ? "bg-moss text-white" : "text-ink-muted"}`}
                onClick={() => setMode("day")}>单日推荐</span>
              <span className={`px-2 py-0.5 rounded cursor-pointer ${mode === "range" ? "bg-moss text-white" : "text-ink-muted"}`}
                onClick={() => setMode("range")}>区间回测</span>
            </div>
            {mode === "day" ? (
              <input type="date" value={date} onChange={(e) => setDate(e.target.value)}
                className="font-mono text-xs text-ink bg-white border border-border rounded-md px-2 py-1 outline-none [color-scheme:light]" />
            ) : (
              <>
                <input type="date" value={start} max={end} onChange={(e) => setStart(e.target.value)}
                  className="font-mono text-xs text-ink bg-white border border-border rounded-md px-2 py-1 outline-none [color-scheme:light]" />
                <span className="text-ink-muted text-xs">至</span>
                <input type="date" value={end} min={start} onChange={(e) => setEnd(e.target.value)}
                  className="font-mono text-xs text-ink bg-white border border-border rounded-md px-2 py-1 outline-none [color-scheme:light]" />
              </>
            )}
            <button onClick={fetchData}
              className="px-3 py-1.5 text-xs text-ink-muted border border-border rounded hover:border-moss transition-colors">刷新</button>
            {onNavigate ? (
              <button onClick={() => onNavigate("all")}
                className="px-3 py-1.5 text-xs text-ink-muted border border-border rounded hover:border-moss transition-colors">返回</button>
            ) : null}
          </div>
        </div>
        {/* 方案切换 tab：固定第二行，各子方案视图位置一致 */}
        <div className="flex gap-0.5 bg-white rounded p-0.5 border border-border text-[11px] w-fit flex-wrap">
          {([["all", "方案A·全方向"], ["d", "方案D·进球半全场"], ["dir", "方案C·方向二串一"], ["e", "方案E·进球确认"], ["f", "终稿·人工确认"], ["g", "方案G·半平×方向"]] as const).map(([k, label]) => (
            <span key={k}
              className={`px-2 py-0.5 rounded cursor-pointer ${k === "g" ? "bg-amber text-white font-semibold" : "text-ink-muted"}`}
              onClick={() => onNavigate?.(k)}>{label}</span>
          ))}
        </div>
      </div>

      {loading ? <div className="h-64 bg-parchment-light rounded animate-pulse" /> : null}
      {error ? <ErrorState message={error} onRetry={fetchData} /> : null}
      {!loading && !error && !data ? <EmptyState icon="🧩" message="暂无方案G数据" /> : null}
      {!loading && !error && data ? (
        <>
          <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-7 gap-3">
            {stats.map((s) => (
              <div key={s.label} className="bg-white rounded-md border border-border p-3 text-center">
                <div className="text-[10px] text-ink-light">{s.label}</div>
                <div className={`text-xl font-heading font-bold mt-0.5 leading-tight ${
                  s.label.startsWith("ROI") && st?.roi != null
                    ? st.roi > 1 ? "text-moss" : st.roi < 1 ? "text-lose" : "text-ink"
                    : "text-ink"
                }`}>
                  {s.value}
                </div>
              </div>
            ))}
          </div>

          {/* 人工终稿 · 已确认（方案G 系统默认 vs 人工终稿 分开显示/统计；可收缩；无确认时也展示板块） */}
          <div className="bg-white rounded-md border border-amber/40 overflow-hidden">
              <div className="px-4 py-3 border-b border-amber/30 bg-amber/5 flex items-center gap-3 flex-wrap select-none"
                onClick={() => { if (finalRows.length > 0) setFinalCollapsed((v) => !v); }}
                title="点击展开/收起对比">
                {finalRows.length > 0 && <span className="text-[10px] text-amber font-bold">{finalCollapsed ? "▶" : "▼"}</span>}
                <span className="text-xs font-bold text-amber uppercase tracking-wide">人工终稿 · 已确认（方案G · 半平×方向，与原系统方案分开显示/统计）</span>
                <span className="text-[10px] text-ink-light">
                  窗口中已确认 {finalStats.n} 天 · 已结算 {finalStats.settled_n} 天；系统默认与人工终稿仅在「已确认日」内分别统计
                </span>
                {finalRows.length > 0 && <span className="ml-auto text-[10px] text-ink-muted">{finalCollapsed ? "展开对比" : "收起对比"}</span>}
              </div>
              {finalRows.length === 0 ? (
                <div className="px-4 py-4 text-center text-xs text-ink-light">
                  暂无已确认终稿：在「终稿·人工确认」页对方案G 确认后，这里会展示「系统默认 vs 人工终稿」对照
                  （当前窗口 {mode === "day" ? `单日 ${date}` : `区间 ${start} ~ ${end}`}）
                </div>
              ) : !finalCollapsed ? (<>
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3 p-3">
                  <FinalStatBox label="确认天数" value={String(finalStats.n)} sub={`已结算 ${finalStats.settled_n} 天`} />
                  <FinalStatBox label="系统默认 p_hit（确认日）" value={finalStats.def_p_hit == null ? "-" : `${(finalStats.def_p_hit * 100).toFixed(1)}%`}
                    sub={`命中 ${finalStats.def_hit_n} / ${finalStats.settled_n}`} />
                  <FinalStatBox label="人工终稿 p_hit" value={finalStats.fin_p_hit == null ? "-" : `${(finalStats.fin_p_hit * 100).toFixed(1)}%`}
                    sub={`命中 ${finalStats.fin_hit_n} / ${finalStats.settled_n} · 改好 ${finalStats.improved_n} / 改差 ${finalStats.worsened_n}`}
                    highlight={finalStats.fin_p_hit != null && finalStats.fin_p_hit >= 0.4} />
                  <div className="rounded-md border border-border bg-white p-3">
                    <div className="text-[10px] text-ink-light mb-1">ROI（返奖/下注，倍率，确认日）</div>
                    <div className="text-[11px] text-ink-muted">系统默认{" "}
                      <span className="font-bold text-ink">{finalStats.def_roi == null ? "-" : `${finalStats.def_roi.toFixed(2)}×`}</span>
                    </div>
                    <div className="text-[11px] mt-1">人工终稿{" "}
                      <span className={`font-bold ${(finalStats.fin_roi ?? 0) > (finalStats.def_roi ?? 0) ? "text-moss" : (finalStats.fin_roi ?? 0) < (finalStats.def_roi ?? 0) ? "text-lose" : "text-ink"}`}>
                        {finalStats.fin_roi == null ? "-" : `${finalStats.fin_roi.toFixed(2)}×`}
                      </span>
                    </div>
                  </div>
                </div>
                <div className="divide-y divide-highlight border-t border-highlight">
                  {finalRows.map((r) => {
                    const defLegs = r.default_legs || [];
                    const finLegs = r.final_legs || [];
                    return (
                      <div key={r.pick_date} className="px-4 py-3">
                        <div className="flex items-center gap-2 flex-wrap mb-2">
                          <span className="font-mono text-xs text-ink font-semibold">{r.pick_date}</span>
                          {(r.default_legs || []).length === 0 ? (
                            <span className="text-[11px] px-1.5 py-0.5 rounded bg-amber/15 text-amber font-semibold">系统 无方案（手工新增）</span>
                          ) : r.default.settled ? (
                            r.default.hit ? (
                              <span className="text-[11px] px-1.5 py-0.5 rounded bg-win text-white font-bold">系统 ✓全中</span>
                            ) : (
                              <span className="text-[11px] px-1.5 py-0.5 rounded bg-lose/10 text-lose font-bold">系统 ✗未中</span>
                            )
                          ) : (
                            <span className="text-[11px] px-1.5 py-0.5 rounded bg-parchment-dark text-ink-muted">系统 未结算</span>
                          )}
                          {r.final.settled ? (
                            r.final.hit ? (
                              <span className="text-[11px] px-1.5 py-0.5 rounded bg-win text-white font-bold">终稿 ✓全中</span>
                            ) : (
                              <span className="text-[11px] px-1.5 py-0.5 rounded bg-lose/10 text-lose font-bold">终稿 ✗未中</span>
                            )
                          ) : (
                            <span className="text-[11px] px-1.5 py-0.5 rounded bg-parchment-dark text-ink-muted">终稿 未结算</span>
                          )}
                          {r.final.parlay_odds != null && <span className="text-xs text-ink">终稿串关 {r.final.parlay_odds.toFixed(2)}</span>}
                          {r.final.settled && r.final.payout != null && (
                            <span className="text-[11px] text-ink-muted">返奖 {r.final.payout.toFixed(2)} / {r.final.stake ?? 1}注</span>
                          )}
                          {r.changed_legs.length > 0 && (
                            <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber/15 text-amber font-semibold">改选 {r.changed_legs.length} 腿</span>
                          )}
                        </div>
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                          <div className="rounded border border-highlight bg-parchment-light/30 px-2 py-1.5">
                            <div className="text-[10px] text-ink-muted mb-1">系统默认（原方案）</div>
                            <div className="space-y-0.5 text-[11px] text-ink-light">
                              {defLegs.length ? defLegs.map((lg, i) => <div key={i} className="leading-relaxed">{legBrief(lg)}</div>) : <div>-</div>}
                            </div>
                          </div>
                          <div className="rounded border border-amber/40 bg-amber/5 px-2 py-1.5">
                            <div className="text-[10px] text-amber mb-1">人工终稿（已确认）</div>
                            <div className="space-y-0.5 text-[11px] text-ink">
                              {finLegs.length ? finLegs.map((lg, i) => <div key={i} className="leading-relaxed">{legBrief(lg)}</div>) : <div>-</div>}
                            </div>
                          </div>
                        </div>
                      </div>
                    );
                  })}
                 </div>
               </>) : null}
             </div>

           {data.picks.length === 0 ? (
            <EmptyState icon="🧩" message="窗口内无方案G串（需当日 halfdraw 候选 × 方案D方向腿）" />
          ) : (
            [...data.picks]
              .sort((a, b) => (b.matchday ?? "").localeCompare(a.matchday ?? ""))
              .map((p: ParlayFPick) => {
                const l1 = p.legs[0];
                const l2 = p.legs[1];
                const hitPlayed = l1?.played?.find((x) => x.zh === l1?.actual) ?? l1?.played?.[0];
                const calc = hitPlayed && l2?.odds != null
                  ? `${hitPlayed.zh}@${hitPlayed.odds.toFixed(2)} × ${l2.pick}@${l2.odds.toFixed(2)} = ${(p.parlay_odds ?? hitPlayed.odds * l2.odds).toFixed(2)}`
                  : null;
                return (
                  <div key={p.matchday} className="bg-parchment-light rounded-lg border border-border p-4 space-y-3">
                    <div className="flex items-center gap-3 flex-wrap">
                      <span className="font-mono text-xs text-ink font-semibold">{p.matchday}</span>
                      {p.hit === true ? (
                        <span className="text-[12px] px-2.5 py-1 rounded bg-win text-white font-extrabold shadow-sm">✓ 全中</span>
                      ) : p.hit === false ? (
                        <span className="text-[11px] px-2 py-0.5 rounded bg-lose/10 text-lose font-bold">✗ 未中</span>
                      ) : (
                        <span className="text-[11px] px-2 py-0.5 rounded bg-parchment-dark text-ink-muted">未结算</span>
                      )}
                      <span className="text-xs text-ink">
                        {p.settled && p.hit ? `返奖 ${p.payout?.toFixed(2)}` : "串关"}
                        （{p.stake} 注{p.settled && p.hit && p.roi != null ? ` ROI ${p.roi.toFixed(2)}` : ""}）
                      </span>
                      <span className={`text-[10px] px-1.5 py-0.5 rounded font-semibold ${p.combo_level?.startsWith("R2") ? "bg-amber/15 text-amber" : "bg-moss/10 text-moss"}`}
                        title={p.combo_level === "R2_fallback" ? "R1 候选与方向腿同场，降级用当日 R2 候选兜底" : "halfdraw 规则档位"}>
                        {comboLabel(p.combo_level)}
                      </span>
                    </div>
                    {p.hit === true && calc ? (
                      <div className="text-[11px] font-bold text-win bg-win/10 rounded px-2 py-1 inline-block tabular-nums">
                        命中赔率计算：{calc}
                      </div>
                    ) : null}
                    <div className="flex gap-3 flex-wrap">
                      {l1 ? <LegCard lg={l1} /> : null}
                      {l2 ? <LegCard lg={l2} /> : null}
                    </div>
                  </div>
                );
              })
          )}
        </>
      ) : null}
    </div>
  );
}
