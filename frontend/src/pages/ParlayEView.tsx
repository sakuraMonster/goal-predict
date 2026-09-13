import { useCallback, useEffect, useMemo, useState } from "react";
import {
  confirmMarketFlowParlayE,
  getMarketFlowParlayD,
  getMarketFlowParlayE,
  getMarketFlowParlayEHistory,
  predictMarketFlow,
  syncMarketFlowOdds,
  syncMarketFlowSmOdds,
  type MarketFlowParlayPick,
  type MarketFlowParlayResponse,
  type ParlayEDirSnapshot,
  type ParlayEHistory,
  type ParlayEMatch,
  type ParlayEResponse,
  type ParlayESuggestion,
} from "../api/client";
import EmptyState from "../components/EmptyState";
import ErrorState from "../components/ErrorState";
import { useToast } from "../components/Toast";

type SubTabKey = "all" | "d" | "dir" | "e" | "f" | "g";

function todayStr() {
  const d = new Date();
  return d.toISOString().slice(0, 10);
}

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
  const [planD, setPlanD] = useState<MarketFlowParlayResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [history, setHistory] = useState<ParlayEHistory | null>(null);
  // 人工确认选择：进球场 + 类型
  const [selMatchId, setSelMatchId] = useState<number | null>(null);
  const [selType, setSelType] = useState<"23" | "34">("23");
  // 方向腿人工指定（玩法池与方案D一致）：null = 沿用方案D当日默认方向腿
  const [dirOpt, setDirOpt] = useState<ParlayEDirSnapshot | null>(null);

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
      const [e, dp] = await Promise.all([
        getMarketFlowParlayE({ date: d }),
        getMarketFlowParlayD({ date: d }).catch(() => null),
      ]);
      setEData(e);
      setPlanD(dp);
      // 回显已确认快照（含人工指定的方向腿）；未确认则回到方案D默认方向腿
      setDirOpt(e.confirm?.dir ?? null);
      if (e.confirm) {
        setSelMatchId(e.confirm.match_id);
        setSelType(e.confirm.leg_type);
      }
    } catch {
      setError("方案E数据加载失败");
    } finally {
      setLoading(false);
    }
  }, [d]);

  useEffect(() => {
    setSelMatchId(null);
    setSelType("23");
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

  const dayPick = useMemo(() => {
    if (!planD || !planD.picks || planD.picks.length === 0) return null;
    const p = planD.picks.find((x: MarketFlowParlayPick) => x.matchday === d) ?? planD.picks[0];
    return p;
  }, [planD, d]);

  const dirLeg = useMemo(() => {
    const p = dayPick;
    if (!p) return null;
    return p.legs.find((l) => l.kind === "dir") ?? null;
  }, [dayPick]);

  const dirSnap = useMemo<ParlayEDirSnapshot | null>(() => {
    const lg = dirLeg;
    if (!lg) return null;
    // match_id 交由后端权威解析（仅用比赛编号对齐；不再按开球时间兜底，避免同开球误配）
    return {
      match_id: 0,
      match_num: lg.match_num ?? null,
      league_name: lg.league_name ?? null,
      home_team: lg.home_team ?? null,
      away_team: lg.away_team ?? null,
      kickoff_time: lg.kickoff_time,
      pick: lg.pick,
      odds: lg.odds,
      p_hat: lg.p_hat ?? null,
      source: lg.source ?? null,
    };
  }, [dirLeg, eData]);

  const selEntry = useMemo<ParlayEMatch | null>(() => {
    if (selMatchId == null || !eData) return null;
    return (
      eData.suggestions.find((s) => s.match_id === selMatchId) ??
      eData.day_matches.find((m) => m.match_id === selMatchId) ??
      null
    );
  }, [selMatchId, eData]);

  /** 方向腿人工指定候选（与方案D玩法池一致）：按当前默认方向腿 source 过滤（模糊池/正路池） */
  const dirOpts = useMemo(() => {
    const opts = eData?.dir_options || [];
    const src = dirLeg?.source;
    if (src === "ambiguous_had" || src === "favorite_hafu") {
      return opts.filter((o) => o.source === src);
    }
    return opts;
  }, [eData, dirLeg]);

  /** 场次身份 key：优先比赛编号（周四002…），否则主客队名兜底 */
  const matchKey = (o: { match_num?: string | null; home_team?: string | null; away_team?: string | null } | null | undefined): string =>
    o?.match_num ? `n:${o.match_num}` : o?.home_team ? `t:${o.home_team}|${o.away_team ?? ""}` : "";

  /** 最终生效的方向腿：人工指定（dirOpt）或 方案D默认（dirSnap） */
  const effDir = useMemo<ParlayEDirSnapshot | null>(() => dirOpt ?? dirSnap, [dirOpt, dirSnap]);

  const sameMatchGoal = selEntry != null && effDir != null && matchKey(selEntry) === matchKey(effDir);
  /** 是否已人工改指（与方案D默认方向腿不同场） */
  const manualDirPicked = !!dirOpt && (dirSnap == null || matchKey(dirOpt) !== matchKey(dirSnap));

  /** 勾选/取消方向腿候选：点已选项 → 回到方案D默认；与进球腿同场不可选 */
  const toggleDir = (o: ParlayEDirSnapshot) => {
    if (selEntry && matchKey(o) === matchKey(selEntry)) {
      toast.toast("方向腿不能与进球腿同场，请换一场或先改进球场", "error");
      return;
    }
    setDirOpt((cur) => (cur && matchKey(cur) === matchKey(o) ? null : o));
  };

  const pickSel = (m: ParlayEMatch | ParlayESuggestion, type: "23" | "34") => {
    const odds = type === "23" ? m.pick_odds23 : m.pick_odds34;
    if (!odds || Object.keys(odds).length < 2) {
      toast.toast(`该场缺 ${type} 复式赔率，无法下注`, "error");
      return;
    }
    setSelMatchId(m.match_id);
    setSelType(type);
  };

  const doConfirm = useCallback(async () => {
    if (selMatchId == null) return;
    if (!effDir) {
      toast.toast("当日无方案D方向腿可组合（请先点“同步并生成方案”确认方案D出串）", "error");
      return;
    }
    if (selEntry && matchKey(effDir) === matchKey(selEntry)) {
      toast.toast("方向腿与进球腿不能同场：请人工指定其他方向场次或更换进球场", "error");
      return;
    }
    setConfirming(true);
    try {
      const res = await confirmMarketFlowParlayE({
        date: d,
        match_id: selMatchId,
        leg_type: selType,
        dir: effDir,
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
  }, [selMatchId, selType, effDir, selEntry, d, toast, load, loadHistory]);

  const pills: Array<[SubTabKey, string]> = [
    ["all", "方案A·全方向"],
    ["d", "方案D·进球半全场"],
    ["dir", "方案C·方向二串一"],
    ["e", "方案E·进球确认"],
    ["f", "终稿·人工确认"],
    ["g", "方案G·半平×方向"],
  ];

  if (loading) return <div className="h-64 bg-parchment-light rounded animate-pulse" />;
  if (error) return <ErrorState message={error} onRetry={load} />;

  const goalOddShow = (m: ParlayEMatch, type: "23" | "34") => {
    const o = type === "23" ? m.pick_odds23 : m.pick_odds34;
    if (!o) return null;
    const nums = type === "23" ? [2, 3] : [3, 4];
    return (
      <span className="text-[10px] text-ink-light">
        复式 {nums.join("&")} @ {nums.map((n) => `${n}:${o[n]}`).join(" / ")}
      </span>
    );
  };
  const goalHighOdd = (m: ParlayEMatch | null, type: "23" | "34") => {
    if (!m) return 0;
    const o = type === "23" ? m.pick_odds23 ?? {} : m.pick_odds34 ?? {};
    const vals = Object.values(o).filter((v) => typeof v === "number" && v > 0);
    return vals.length ? Math.max(...vals) : 0;
  };
  const comboMaxOdd = selEntry && effDir ? (goalHighOdd(selEntry, selType) * effDir.odds).toFixed(2) : null;

  const sugTag = (s: ParlayESuggestion) =>
    s.leg_type === "34" && s.downgraded
      ? "34降级23"
      : s.leg_type === "34"
        ? "34复式"
        : "23复式";

  return (
    <div className="space-y-4 tabular-nums">
      <div className="px-4 py-3 bg-parchment-dark rounded-lg border border-border-dark space-y-2">
        <div className="flex items-center gap-3 min-h-8">
          <span className="flex items-center gap-2 min-w-0 flex-1 text-xs text-moss font-semibold"
            title="方案E · 每日进球双选（23腿/34腿，34 缺货降级 23）→ 人工确认 1 场 → 与方案D方向腿组成 2串1">
            <span className="w-2 h-2 rounded-full bg-moss shrink-0" />
            <span className="truncate">方案E · 每日进球双选（23腿/34腿，34 缺货降级 23）→ 人工确认 1 场 → 与方案D方向腿组成 2串1</span>
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
        {/* 方案切换 tab：固定第二行，各子方案视图位置一致 */}
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
          方案E 命中率统计 · 已确认记录结算
        </div>
        {history && history.stats.confirm_n > 0 ? (
          <>
            <div className="flex gap-2 flex-wrap p-3">
              {[
                ["已确认", `${history.stats.confirm_n} 天`],
                ["进球腿命中", history.stats.goal_settled_n > 0 ? `${history.stats.goal_hit_n}/${history.stats.goal_settled_n} = ${(history.stats.goal_p_hit! * 100).toFixed(1)}%` : "-"],
                ["方向腿命中", history.stats.dir_verified_n > 0 ? `${history.stats.dir_hit_n}/${history.stats.dir_verified_n} = ${(history.stats.dir_p_hit! * 100).toFixed(1)}%` : "-"],
                ["2串1命中", history.stats.combo_n > 0 ? `${history.stats.combo_hit_n}/${history.stats.combo_n} = ${(history.stats.combo_p_hit! * 100).toFixed(1)}%` : "-"],
                ["ROI（返奖/下注，倍率）", history.stats.roi != null ? `${history.stats.roi.toFixed(2)}×` : "-"],
              ].map(([k, v]) => (
                <div key={k} className="flex-1 min-w-[150px] bg-white rounded-md border border-border px-3 py-2 text-center">
                  <div className="text-[10px] text-ink-light">{k}</div>
                  <div className={`font-heading font-bold text-xl mt-0.5 leading-tight ${
                    k === "2串1命中" && (history.stats.combo_p_hit ?? 0) >= 0.4
                      ? "text-moss"
                      : k.startsWith("ROI") && history.stats.roi != null
                        ? history.stats.roi > 1 ? "text-moss" : history.stats.roi < 1 ? "text-lose" : "text-ink"
                        : "text-ink"
                  }`}>{v}</div>
                </div>
              ))}
            </div>
            <div className="divide-y divide-highlight max-h-[300px] overflow-y-auto">
              {history.rows.map((r) => {
                const g = r.goal;
                const dg = r.dir;
                const glHitTxt = g.hit == null ? "未结算" : g.hit ? `进球✓(${g.act}球)` : `进球✗(${g.act}球)`;
                const dirTxt = dg ? (dg.hit == null ? "方向未验证" : dg.hit ? "方向✓" : "方向✗") : "无方向腿";
                const combo = r.combo_hit == null ? "组合未结算" : r.combo_hit ? "2串1 ✓" : "2串1 ✗";
                return (
                  <div key={r.pick_date} className="px-4 py-2 flex items-center gap-3 flex-wrap text-[11px]">
                    <span className="font-mono text-ink font-semibold w-24">{r.pick_date}</span>
                    <span className={`px-1.5 py-0.5 rounded text-[10px] ${r.leg_type === "23" ? "bg-moss/10 text-moss" : "bg-sand/15 text-sand"}`}>{r.leg_type === "23" ? "23复式" : "34复式"}</span>
                    <span className="text-ink flex-1 min-w-[140px]">{g.home_team} vs {g.away_team}</span>
                    <span className={`text-[11px] ${g.hit === true ? "text-moss" : g.hit === false ? "text-rust" : "text-ink-light"}`}>{glHitTxt}</span>
                    <span className="text-ink-muted flex-1 min-w-[120px]">{dg ? `${dg.home_team} vs ${dg.away_team} ${dg.pick}@${dg.odds?.toFixed(2)}` : "—"}</span>
                    <span className={`text-[11px] ${dg?.hit === true ? "text-moss" : dg?.hit === false ? "text-rust" : "text-ink-light"}`}>{dirTxt}</span>
                    <span className={`font-bold ${r.combo_hit === true ? "text-moss" : r.combo_hit === false ? "text-rust" : "text-ink-light"}`}>{combo}</span>
                    {r.payout != null && <span className="text-ink-light">返奖 {r.payout.toFixed(2)} / 2注</span>}
                  </div>
                );
              })}
            </div>
          </>
        ) : (
          <div className="px-4 py-6 text-center text-xs text-ink-light">
            暂无已确认记录：确认后每日自动累计（进球腿命中率 / 方向腿命中率 / 2串1命中率 / ROI）
          </div>
        )}
      </div>

      {/* 当日已确认方案（快照展示，来自落库数据） */}
      {eData?.confirm && (
        <div className="bg-white rounded-md border border-moss/50 overflow-hidden">
          <div className="px-4 py-2.5 border-b border-moss/20 bg-moss/10 flex items-center gap-3 flex-wrap">
            <span className="text-xs font-bold text-moss uppercase tracking-wide">✓ 本日已确认 · {eData.confirm.pick_date}</span>
            <span className="text-[10px] text-moss/70">已落库 · 重新选择并确认可覆盖</span>
          </div>
          <div className="flex gap-3 flex-wrap p-3">
            <div className="flex-1 min-w-[240px] bg-parchment-light/50 rounded-md border border-highlight p-3">
              <div className="flex items-center gap-2">
                <span className={`text-[10px] px-1.5 py-0.5 rounded ${eData.confirm.leg_type === "23" ? "bg-moss/10 text-moss" : "bg-sand/15 text-sand"}`}>
                  {eData.confirm.leg_type === "23" ? "23复式(2∪3)" : "34复式(3∪4)"}
                </span>
                <span className="text-[10px] text-ink-light">{eData.confirm.league_name || ""} · {eData.confirm.match_num || "-"}</span>
              </div>
              <div className="text-[13px] text-ink mt-1">{eData.confirm.home_team} vs {eData.confirm.away_team}</div>
              <div className="text-[10px] text-ink-muted mt-1">
                {eData.confirm.goal_pick_odds && Object.entries(eData.confirm.goal_pick_odds).map(([k, v]) => `${k}球@${v}`).join(" / ")}
                {eData.confirm.goal_p_hat != null ? ` · p_hat ${Math.round(eData.confirm.goal_p_hat * 100)}%` : ""}
              </div>
            </div>
            <div className="self-center text-ink-muted text-lg">×</div>
            <div className="flex-1 min-w-[240px] bg-parchment-light/50 rounded-md border border-highlight p-3">
              <div className="flex items-center gap-2">
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-moss/15 text-moss">方案D方向腿</span>
                <span className="text-[10px] text-ink-light">{eData.confirm.dir?.league_name || ""} · {eData.confirm.dir?.match_num || "-"}</span>
              </div>
              <div className="text-[13px] text-ink mt-1">
                {eData.confirm.dir ? `${eData.confirm.dir.home_team} vs ${eData.confirm.dir.away_team}` : "未附带方向腿（方案D当日无输出）"}
              </div>
              {eData.confirm.dir && (
                <div className="text-[10px] text-ink-muted mt-1">
                  {eData.confirm.dir.pick}@{eData.confirm.dir.odds?.toFixed(2)}
                  {eData.confirm.dir.p_hat != null ? ` · p_hat ${Math.round(eData.confirm.dir.p_hat * 100)}%` : ""}
                </div>
              )}
            </div>
            <div className="flex flex-col justify-center gap-1 text-[11px]">
              {eData.confirm.parlay_odds_max != null && (
                <span className="text-ink font-semibold">最高单注串关赔率 ≈ {eData.confirm.parlay_odds_max}</span>
              )}
              <span className="text-ink-light">更新于 {eData.confirm.updated_at ? eData.confirm.updated_at.slice(0, 16).replace("T", " ") : "-"}</span>
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
              每日进球双选建议 · {d}
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3 p-3">
              {eData.suggestions.map((s) => {
                const active = selMatchId === s.match_id && selType === s.leg_type;
                return (
                  <div key={`${s.match_id}-${s.leg_type}`}
                    className={`rounded-md border p-3 cursor-pointer transition-all ${active ? "border-moss shadow-[0_0_0_2px_rgba(45,90,59,0.15)]" : "border-highlight hover:border-moss/60"}`}
                    onClick={() => pickSel(s, s.leg_type)}>
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className={`text-[10px] px-1.5 py-0.5 rounded font-semibold ${s.leg_type === "23" ? "bg-moss/10 text-moss" : "bg-sand/15 text-sand"}`}>{sugTag(s)}</span>
                      <span className="font-mono text-[11px] text-ink">{s.match_num || "-"}</span>
                      <span className="text-[10px] text-ink-light">{s.league_name}</span>
                      <span className="ml-auto text-[10px] font-bold text-moss">p_hat {Math.round(s.p_hat * 100)}%</span>
                    </div>
                    <div className="text-[13px] text-ink mt-1">{s.home_team} <span className="text-ink-muted text-[10px]">vs</span> {s.away_team}</div>
                    <div className="mt-1 flex items-center gap-2 flex-wrap">{goalOddShow(s, s.leg_type)}</div>
                    {active && <div className="mt-1.5 text-[10px] font-bold text-moss">✓ 已选为该日进球腿</div>}
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
              <span className="text-[10px] text-ink-light">点击「打23 / 打34」直接将该场作为确认的进球腿（可为任意场次）</span>
            </div>
            <div className="divide-y divide-highlight max-h-[420px] overflow-y-auto">
              {eData.day_matches.map((m) => {
                const active = selMatchId === m.match_id;
                return (
                  <div key={m.match_id} className={`px-4 py-2.5 flex items-center gap-3 flex-wrap ${active ? "bg-parchment-light/40" : ""}`}>
                    <span className="font-mono text-[11px] text-ink w-8">{m.match_num || "-"}</span>
                    <span className="text-[10px] text-ink-light w-14">{m.league_name}</span>
                    <span className="text-[12px] text-ink flex-1 min-w-[150px]">{m.home_team} vs {m.away_team}</span>
                    <span className="text-[10px] text-ink-muted">P23 {Math.round(m.p23 * 100)}% · P34 {Math.round(m.p34 * 100)}%</span>
                    <div className="flex gap-1.5">
                      <button onClick={() => pickSel(m, "23")} className={`px-2.5 py-1 text-[11px] rounded border ${active && selType === "23" ? "bg-moss text-white border-moss" : "border-border text-ink-muted hover:border-moss"}`}>打23</button>
                      <button onClick={() => pickSel(m, "34")} className={`px-2.5 py-1 text-[11px] rounded border ${active && selType === "34" ? "bg-sand text-white border-sand" : "border-border text-ink-muted hover:border-moss"}`}>打34</button>
                    </div>
                    {m.excl23 || m.excl34 ? (
                      <span className="text-[9px] text-rust">{(m.excl23 ? "葡超/法乙(23规则排除) " : "")}{(m.excl34 ? "美职联/芬超/瑞典超(34规则排除)" : "")}</span>
                    ) : null}
                  </div>
                );
              })}
            </div>
          </div>

          {/* 组合预览 + 确认 */}
          <div className="bg-white rounded-md border border-border p-3">
            <div className="text-xs font-bold text-ink mb-2">2串1 预览（确认的进球场 × 方案D方向腿）</div>
            <div className="flex gap-2 flex-wrap items-stretch">
              {/* 进球腿 */}
              <div className="flex-1 min-w-[240px] bg-parchment-light/50 rounded-md border border-highlight p-3">
                <div className="flex items-center gap-2">
                  <span className="text-[10px] text-ink-light">{selEntry?.match_num || "-"}</span>
                  <span className={`text-[10px] px-1.5 py-0.5 rounded ${selType === "23" ? "bg-moss/10 text-moss" : "bg-sand/15 text-sand"}`}>
                    {selType === "23" ? "23复式" : "34复式"}
                  </span>
                </div>
                <div className="text-[12px] text-ink mt-1">
                  {selEntry ? `${selEntry.home_team} vs ${selEntry.away_team}` : "尚未选择进球场"}
                </div>
                {selEntry && goalOddShow(selEntry, selType)}
              </div>
              <div className="self-center text-ink-muted text-lg">×</div>
              {/* 方案D方向腿（默认 / 人工指定） */}
              <div className="flex-1 min-w-[240px] bg-parchment-light/50 rounded-md border border-highlight p-3">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="text-[10px] px-1.5 py-0.5 rounded bg-moss/15 text-moss">方案D方向腿</span>
                  {manualDirPicked ? (
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber/15 text-amber font-semibold">已人工改指</span>
                  ) : (
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-parchment-dark text-ink-muted">默认场</span>
                  )}
                  <span className="text-[10px] text-ink-light">{effDir?.match_num || "-"}</span>
                </div>
                <div className="text-[12px] text-ink mt-1">
                  {effDir ? `${effDir.home_team} vs ${effDir.away_team}` : "方案D当日无方向腿（可从下方候选改指）"}
                </div>
                {effDir && (
                  <div className="text-[10px] text-ink-muted mt-1">
                    {effDir.pick}@{effDir.odds.toFixed(2)} · p_hat {Math.round((effDir.p_hat ?? 0) * 100)}%
                  </div>
                )}
              </div>
            </div>
            {/* 方向腿候选 · 人工指定（与方案D玩法池一致：模糊池fav≥1.8 / 正路池胜胜负负≥1.8） */}
            <div className="mt-3 border-t border-highlight pt-3">
              <div className="flex items-center gap-2 flex-wrap mb-1.5">
                <span className="text-[10px] font-bold text-ink-light uppercase tracking-wide">方向腿 · 人工指定</span>
                <span className="text-[10px] text-ink-light">默认=方案D当日方向腿；勾选其他方向场次即改指，再点取消回到默认；与进球腿同场不可选</span>
              </div>
              {dirOpts.length === 0 ? (
                <div className="text-[10px] text-rust">当日无其他可改指的方向场次（当前玩法池为空）</div>
              ) : (
                <div className="space-y-1.5 max-h-[240px] overflow-y-auto">
                  {dirOpts.map((o) => {
                    const isDefault = dirLeg != null && matchKey(o) === matchKey(dirLeg);
                    const active = effDir != null && matchKey(o) === matchKey(effDir);
                    const sameGoal = selEntry != null && matchKey(o) === matchKey(selEntry);
                    return (
                      <label key={o.match_num ?? `${o.home_team}${o.away_team}`}
                        className={`flex items-center gap-2 rounded border px-2 py-1.5 text-[11px] cursor-pointer ${active ? "border-moss bg-moss/10" : sameGoal ? "opacity-45 cursor-not-allowed" : "border-highlight bg-white hover:border-moss/60"}`}>
                        <input type="checkbox" className="accent-moss" checked={active}
                          disabled={sameGoal}
                          onChange={() => toggleDir(o)} />
                        <span className="font-mono text-[10px] text-ink-light shrink-0">{o.match_num || "-"}</span>
                        <span className="text-ink">{o.home_team} vs {o.away_team}</span>
                        <span className="font-semibold text-ink">{o.pick}</span>
                        <span className="text-ink-muted">@{o.odds.toFixed(2)}</span>
                        <span className="text-ink-light">p_hat {Math.round((o.p_hat ?? 0) * 100)}%</span>
                        {isDefault && <span className="text-[10px] px-1 py-0.5 rounded bg-parchment-dark text-ink-muted">方案D默认</span>}
                        {active && !isDefault && <span className="text-[10px] px-1 py-0.5 rounded bg-amber/15 text-amber font-semibold">已改指</span>}
                        {sameGoal && <span className="text-[9px] text-rust">与进球腿同场，不可选</span>}
                      </label>
                    );
                  })}
                </div>
              )}
              {sameMatchGoal && (
                <div className="mt-2 text-[11px] text-rust">
                  ⚠ 当前方向腿与进球腿同场（{effDir?.match_num}）：请人工改指其他方向场次，或更换进球场后再确认
                </div>
              )}
            </div>
            <div className="mt-3 flex items-center gap-3 flex-wrap">
              <button onClick={doConfirm} disabled={selMatchId == null || confirming}
                className="px-4 py-1.5 text-xs bg-amber text-white rounded hover:bg-amber/90 disabled:opacity-50 font-semibold">
                {confirming ? "确认中..." : selMatchId == null ? "请先选择一个进球场" : "确认方案E（落库）"}
              </button>
              {selEntry && effDir && comboMaxOdd && (
                <span className="text-[10px] text-ink-muted">
                  最高单注串关赔率 ≈ {goalHighOdd(selEntry, selType).toFixed(2)} × {effDir.odds.toFixed(2)} = {comboMaxOdd}
                </span>
              )}
            </div>
            {eData.confirm && (
              <div className="mt-3 text-[11px] rounded-md bg-moss/10 text-moss px-3 py-2">
                ✓ 已确认（{eData.confirm.pick_date}）：{eData.confirm.home_team} vs {eData.confirm.away_team} 打{eData.confirm.leg_type}
                {eData.confirm.dir ? ` · 串 {eData.confirm.dir.home_team} vs {eData.confirm.dir.away_team} ${eData.confirm.dir.pick}@${eData.confirm.dir.odds}` : " · 未附带方案D方向腿"}
                {eData.confirm.parlay_odds_max != null ? ` · 最高单注串关赔率 ${eData.confirm.parlay_odds_max}` : ""}
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
