import { useCallback, useEffect, useMemo, useState } from "react";
import {
  confirmMarketFlowParlayE,
  getMarketFlowParlayE,
  getMarketFlowParlayEHistory,
  predictMarketFlow,
  syncMarketFlowOdds,
  syncMarketFlowSmOdds,
  type ParlayEHistory,
  type ParlayEMatch,
  type ParlayEResponse,
} from "../api/client";
import EmptyState from "../components/EmptyState";
import ErrorState from "../components/ErrorState";
import { useToast } from "../components/Toast";

type SubTabKey = "all" | "d" | "dir" | "e" | "f" | "g";

function todayStr() {
  const d = new Date();
  return d.toISOString().slice(0, 10);
}

const LEG_LABEL: Record<string, string> = {
  "12": "12复式(1∪2)",
  "23": "23复式(2∪3)",
  "34": "34复式(3∪4)",
};

export default function ParlayEView({
  initialDate,
  onNavigate,
}: {
  initialDate?: string;
  onNavigate?: (tab: SubTabKey) => void;
}) {
  const toast = useToast();
  const [d, setD] = useState<string>(initialDate || todayStr());
  const [eData, setEData] = useState<ParlayEResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [history, setHistory] = useState<ParlayEHistory | null>(null);
  // 人工确认选择：进球场 + 双选类型（动态 12/23/34）
  const [selMatchId, setSelMatchId] = useState<number | null>(null);
  const [selType, setSelType] = useState<string>("");

  const loadHistory = useCallback(async () => {
    try {
      setHistory(await getMarketFlowParlayEHistory());
    } catch {
      setHistory(null);
    }
  }, []);

  useEffect(() => {
    loadHistory();
  }, [loadHistory]);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const e = await getMarketFlowParlayE({ date: d });
      setEData(e);
      // 回显已确认快照；否则默认选「第一场」（双选概率最高）
      if (e.confirm) {
        setSelMatchId(e.confirm.match_id);
        setSelType(e.confirm.leg_type);
      } else if (e.suggestions.length > 0) {
        setSelMatchId(e.suggestions[0].match_id);
        setSelType(e.suggestions[0].leg_type);
      } else {
        setSelMatchId(null);
        setSelType("");
      }
    } catch {
      setError("方案E数据加载失败");
    } finally {
      setLoading(false);
    }
  }, [d]);

  useEffect(() => {
    load();
  }, [load]);

  const genPlans = useCallback(async () => {
    setGenerating(true);
    setError(null);
    try {
      await syncMarketFlowOdds({ date: d });
      await syncMarketFlowSmOdds({ date: d });
      await predictMarketFlow({ date: d, overwrite: true });
      toast.toast(`已同步 ${d} 赔率并生成 MarketFlow 预测`, "success");
      await load();
    } catch {
      setError("同步/生成失败");
    } finally {
      setGenerating(false);
    }
  }, [d, load, toast]);

  const selEntry = useMemo<ParlayEMatch | null>(() => {
    if (selMatchId == null || !eData) return null;
    return (
      eData.suggestions.find((s) => s.match_id === selMatchId) ??
      eData.day_matches.find((m) => m.match_id === selMatchId) ??
      null
    );
  }, [selMatchId, eData]);

  const pickSel = (m: ParlayEMatch) => {
    if (!m.pick_odds || Object.keys(m.pick_odds).length < 2) {
      toast.toast(`该场缺 ${m.leg_type} 双选赔率，无法下注`, "error");
      return;
    }
    setSelMatchId(m.match_id);
    setSelType(m.leg_type);
  };

  const doConfirm = useCallback(async () => {
    if (selMatchId == null || !selType) return;
    setConfirming(true);
    try {
      const res = await confirmMarketFlowParlayE({
        date: d,
        match_id: selMatchId,
        leg_type: selType,
      });
      if (res.success) {
        toast.toast(res.message || "方案E已确认落库", "success");
        await load();
        await loadHistory();
      } else {
        toast.toast("确认失败", "error");
      }
    } catch {
      toast.toast("确认失败", "error");
    } finally {
      setConfirming(false);
    }
  }, [selMatchId, selType, d, toast, load, loadHistory]);

  const pills: Array<[SubTabKey, string]> = [
    ["all", "方案A·全方向"],
    ["d", "方案D·进球半全场"],
    ["dir", "方案C·方向二串一"],
    ["e", "方案E·单关进球"],
    ["f", "终稿·人工确认"],
    ["g", "方案G·半平×方向"],
  ];

  if (loading) return <div className="h-64 bg-parchment-light rounded animate-pulse" />;
  if (error) return <ErrorState message={error} onRetry={load} />;

  const goalOddShow = (m: ParlayEMatch) => {
    const o = m.pick_odds;
    if (!o) return null;
    return (
      <span className="text-[10px] text-ink-light">
        复式 {m.pick_nums.join("&")} @ {m.pick_nums.map((n) => `${n}:${o[n]}`).join(" / ")}
      </span>
    );
  };

  const legTag = (lt: string) =>
    LEG_LABEL[lt] ?? `${lt}复式`;

  return (
    <div className="space-y-4 tabular-nums">
      <div className="px-4 py-3 bg-parchment-dark rounded-lg border border-border-dark space-y-2">
        <div className="flex items-center gap-3 min-h-8">
          <span className="flex items-center gap-2 min-w-0 flex-1 text-xs text-moss font-semibold"
            title="方案E · 每日单关进球数双选（从1/2/3/4取隐含最高2档）→ 默认第一场，可人工改指其他场次">
            <span className="w-2 h-2 rounded-full bg-moss shrink-0" />
            <span className="truncate">方案E · 每日单关进球数双选（1/2/3/4 中隐含最高 2 档）→ 默认第一场，可人工改指其他场次</span>
          </span>
          <div className="flex items-center gap-2 shrink-0">
            <input type="date" value={d} onChange={(e) => setD(e.target.value)}
              className="font-mono text-xs text-ink bg-white border border-border rounded-md px-2 py-1 outline-none [color-scheme:light]" />
            <button onClick={genPlans} disabled={generating}
              className="px-3 py-1.5 text-xs bg-amber text-white rounded hover:bg-amber/90 transition-colors disabled:opacity-50 font-semibold">
              {generating ? "同步生成中..." : "同步并生成方案"}
            </button>
          </div>
        </div>
        <div className="flex gap-0.5 bg-white rounded p-0.5 border border-border text-[11px] w-fit flex-wrap">
          {pills.map(([k, label]) => (
            <span key={k} className={`px-2 py-0.5 rounded cursor-pointer ${k === "e" ? "bg-amber text-white font-semibold" : "text-ink-muted"}`}
              onClick={() => onNavigate?.(k)}>{label}</span>
          ))}
        </div>
      </div>

      {/* 命中率统计（已确认记录结算） */}
      <div className="bg-white rounded-md border border-border overflow-hidden">
        <div className="px-4 py-2.5 border-b border-highlight bg-parchment-light/60 text-xs font-bold text-ink-light uppercase tracking-wide">
          方案E 命中率统计 · 已确认记录结算（单关双选）
        </div>
        {history && history.stats.confirm_n > 0 ? (
          <>
            <div className="flex gap-2 flex-wrap p-3">
              {[
                ["已确认", `${history.stats.confirm_n} 天`],
                ["双选命中", history.stats.settled_n > 0 ? `${history.stats.hit_n}/${history.stats.settled_n} = ${(history.stats.p_hit! * 100).toFixed(1)}%` : "-"],
                ["ROI（返奖/下注，倍率）", history.stats.roi != null ? `${history.stats.roi.toFixed(2)}×` : "-"],
              ].map(([k, v]) => (
                <div key={k} className="flex-1 min-w-[150px] bg-white rounded-md border border-border px-3 py-2 text-center">
                  <div className="text-[10px] text-ink-light">{k}</div>
                  <div className={`font-heading font-bold text-xl mt-0.5 leading-tight ${
                    k.startsWith("ROI") && history.stats.roi != null
                      ? history.stats.roi > 1 ? "text-moss" : history.stats.roi < 1 ? "text-lose" : "text-ink"
                      : "text-ink"
                  }`}>{v}</div>
                </div>
              ))}
            </div>
            <div className="divide-y divide-highlight max-h-[300px] overflow-y-auto">
              {history.rows.map((r) => {
                const g = r.goal;
                const hitTxt = g.hit == null ? "未结算" : g.hit ? `命中✓(${g.act}球)` : `未中✗(${g.act}球)`;
                return (
                  <div key={r.pick_date} className="px-4 py-2 flex items-center gap-3 flex-wrap text-[11px]">
                    <span className="font-mono text-ink font-semibold w-24">{r.pick_date}</span>
                    <span className="px-1.5 py-0.5 rounded text-[10px] bg-moss/10 text-moss">{legTag(r.leg_type)}</span>
                    <span className="text-ink flex-1 min-w-[140px]">{g.home_team} vs {g.away_team}</span>
                    <span className={`text-[11px] ${g.hit === true ? "text-moss" : g.hit === false ? "text-rust" : "text-ink-light"}`}>{hitTxt}</span>
                    {r.payout != null && <span className="text-ink-light">返奖 {r.payout.toFixed(2)} / 2注</span>}
                  </div>
                );
              })}
            </div>
          </>
        ) : (
          <div className="px-4 py-6 text-center text-xs text-ink-light">
            暂无已确认记录：确认后每日自动累计（双选命中率 / ROI）
          </div>
        )}
      </div>

      {/* 当日已确认方案（快照展示） */}
      {eData?.confirm && (
        <div className="bg-white rounded-md border border-moss/50 overflow-hidden">
          <div className="px-4 py-2.5 border-b border-moss/20 bg-moss/10 flex items-center gap-3 flex-wrap">
            <span className="text-xs font-bold text-moss uppercase tracking-wide">✓ 本日已确认 · {eData.confirm.pick_date}</span>
            <span className="text-[10px] text-moss/70">已落库 · 重新选择并确认可覆盖</span>
          </div>
          <div className="p-3">
            <div className="flex items-center gap-2">
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-moss/10 text-moss">{legTag(eData.confirm.leg_type)}</span>
              <span className="text-[10px] text-ink-light">{eData.confirm.league_name || ""} · {eData.confirm.match_num || "-"}</span>
            </div>
            <div className="text-[13px] text-ink mt-1">{eData.confirm.home_team} vs {eData.confirm.away_team}</div>
            <div className="text-[10px] text-ink-muted mt-1">
              {eData.confirm.goal_pick_odds && Object.entries(eData.confirm.goal_pick_odds).map(([k, v]) => `${k}球@${v}`).join(" / ")}
              {eData.confirm.goal_p_hat != null ? ` · p_hat ${Math.round(eData.confirm.goal_p_hat * 100)}%` : ""}
            </div>
          </div>
        </div>
      )}

      {!eData || eData.day_matches.length === 0 ? (
        <EmptyState icon="⚽" message={`${d} 无可用进球数候选（TTG 赔率缺失或已全部结算）`}
          description="请先在“当日预测/生成方案”同步赔率并生成 MarketFlow 预测" />
      ) : (
        <>
          {/* 每日两个建议 */}
          <div className="bg-white rounded-md border border-border overflow-hidden">
            <div className="px-4 py-2.5 border-b border-highlight bg-parchment-light/60 text-xs font-bold text-ink-light uppercase tracking-wide">
              每日单关进球双选建议 · {d}（第 1 场为默认）
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3 p-3">
              {eData.suggestions.map((s, idx) => {
                const active = selMatchId === s.match_id;
                return (
                  <div key={`${s.match_id}-${s.leg_type}`}
                    className={`rounded-md border p-3 cursor-pointer transition-all ${active ? "border-moss shadow-[0_0_0_2px_rgba(45,90,59,0.15)]" : "border-highlight hover:border-moss/60"}`}
                    onClick={() => pickSel(s)}>
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="text-[10px] px-1.5 py-0.5 rounded font-semibold bg-moss/10 text-moss">{legTag(s.leg_type)}</span>
                      {idx === 0 && <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber/15 text-amber font-semibold">默认</span>}
                      <span className="font-mono text-[11px] text-ink">{s.match_num || "-"}</span>
                      <span className="text-[10px] text-ink-light">{s.league_name}</span>
                      <span className="ml-auto text-[10px] font-bold text-moss">p_hat {Math.round(s.p_hat * 100)}%</span>
                    </div>
                    <div className="text-[13px] text-ink mt-1">{s.home_team} <span className="text-ink-muted text-[10px]">vs</span> {s.away_team}</div>
                    <div className="mt-1">{goalOddShow(s)}</div>
                    {active && <div className="mt-1.5 text-[10px] font-bold text-moss">✓ 已选为该日单关场次</div>}
                  </div>
                );
              })}
              {eData.suggestions.length < 2 && (
                <div className="col-span-full text-[11px] text-rust">{eData.suggestion_reason ?? "当日不足两场建议"}</div>
              )}
            </div>
          </div>

          {/* 当日其他场次（手动确认） */}
          <div className="bg-white rounded-md border border-border overflow-hidden">
            <div className="px-4 py-2.5 border-b border-highlight bg-parchment-light/60 flex items-center gap-2 flex-wrap">
              <span className="text-xs font-bold text-ink-light uppercase tracking-wide">当日其他场次 · 手动选场确认</span>
              <span className="text-[10px] text-ink-light">点击任一场直接将该场作为确认的单关（可覆盖默认第一场）</span>
            </div>
            <div className="divide-y divide-highlight max-h-[420px] overflow-y-auto">
              {eData.day_matches.map((m) => {
                const active = selMatchId === m.match_id;
                return (
                  <div key={m.match_id} className={`px-4 py-2.5 flex items-center gap-3 flex-wrap ${active ? "bg-parchment-light/40" : ""}`}>
                    <span className="font-mono text-[11px] text-ink w-8">{m.match_num || "-"}</span>
                    <span className="text-[10px] text-ink-light w-14">{m.league_name}</span>
                    <span className="text-[12px] text-ink flex-1 min-w-[150px]">{m.home_team} vs {m.away_team}</span>
                    <span className="text-[10px] text-ink-muted">{legTag(m.leg_type)} p_hat {Math.round(m.p_hat * 100)}%</span>
                    <button onClick={() => pickSel(m)} className={`px-2.5 py-1 text-[11px] rounded border ${active ? "bg-moss text-white border-moss" : "border-border text-ink-muted hover:border-moss"}`}>
                      打{m.leg_type}
                    </button>
                  </div>
                );
              })}
            </div>
          </div>

          {/* 单关预览 + 确认 */}
          <div className="bg-white rounded-md border border-border p-3">
            <div className="text-xs font-bold text-ink mb-2">单关预览（确认的进球场）</div>
            <div className="flex gap-2 flex-wrap items-stretch">
              <div className="flex-1 min-w-[240px] bg-parchment-light/50 rounded-md border border-highlight p-3">
                <div className="flex items-center gap-2">
                  <span className="text-[10px] text-ink-light">{selEntry?.match_num || "-"}</span>
                  <span className="text-[10px] px-1.5 py-0.5 rounded bg-moss/10 text-moss">{selEntry ? legTag(selType) : "—"}</span>
                </div>
                <div className="text-[12px] text-ink mt-1">
                  {selEntry ? `${selEntry.home_team} vs ${selEntry.away_team}` : "尚未选择进球场"}
                </div>
                {selEntry && goalOddShow(selEntry)}
              </div>
            </div>
            <div className="mt-3 flex items-center gap-3 flex-wrap">
              <button onClick={doConfirm} disabled={selMatchId == null || confirming}
                className="px-4 py-1.5 text-xs bg-amber text-white rounded hover:bg-amber/90 disabled:opacity-50 font-semibold">
                {confirming ? "确认中..." : selMatchId == null ? "请先选择一个进球场" : "确认方案E（落库）"}
              </button>
            </div>
            {eData.confirm && (
              <div className="mt-3 text-[11px] rounded-md bg-moss/10 text-moss px-3 py-2">
                ✓ 已确认（{eData.confirm.pick_date}）：{eData.confirm.home_team} vs {eData.confirm.away_team} 打{eData.confirm.leg_type}
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
