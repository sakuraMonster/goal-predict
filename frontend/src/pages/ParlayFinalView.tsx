import { useCallback, useEffect, useState } from "react";
import {
  confirmMarketFlowParlayFinal,
  getMarketFlowParlayFinalDay,
  getMarketFlowParlayFinalHistory,
  type FinalChoiceSel,
  type FinalLeg,
  type FinalOption,
  type ParlayFinalDayResponse,
  type ParlayFinalHistory,
  type ParlayFinalPlanView,
} from "../api/client";
import EmptyState from "../components/EmptyState";
import ErrorState from "../components/ErrorState";
import { useToast } from "../components/Toast";

type SubTabKey = "all" | "d" | "dir" | "e" | "f" | "g";

function todayStr() {
  return new Date().toISOString().slice(0, 10);
}

const PLAN_META: Record<string, { label: string; desc: string }> = {
  A: { label: "方案A · 全方向", desc: "2进球3选 + 1方向" },
  D: { label: "方案D · 进球半全场", desc: "1进球3选 + 1半全场 + 1方向" },
  C: { label: "方案C · 方向二串一", desc: "1半全场 + 1胜平负" },
  G: { label: "方案G · 半平×方向", desc: "halfdraw(R1半平/R2半全场) + 方案D方向腿（2串1）" },
};

const KIND_TAG: Record<string, [string, string]> = {
  goals: ["进球", "bg-amber/15 text-amber"],
  hafu: ["半全场", "bg-sand/15 text-sand"],
  dir: ["方向", "bg-moss/15 text-moss"],
  halfdraw: ["半平", "bg-sand/15 text-sand"],
};

export default function ParlayFinalView({
  initialDate,
  onNavigate,
}: {
  initialDate?: string;
  onNavigate?: (tab: SubTabKey) => void;
}) {
  const toast = useToast();
  const [d, setD] = useState<string>(initialDate || todayStr());
  const [data, setData] = useState<ParlayFinalDayResponse | null>(null);
  const [history, setHistory] = useState<ParlayFinalHistory | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<string | null>(null);
  // 人工选择：plan -> leg_idx -> opt_id | opt_id[]（同场双选=同一场 默认+备选 两个 opt_id）
  const [sel, setSel] = useState<Record<string, Record<number, string | string[]>>>({});

  /** 规范化选择为列表 */
  const toList = (v: string | string[] | null | undefined): string[] => {
    if (!v) return [];
    return Array.isArray(v) ? v : [v];
  };

  /** 选项的场次身份（与后端 _final_opt_match_key 一致） */
  const optMatchKey = (o: FinalOption): string => {
    if (o.match_id) return `id:${o.match_id}`;
    if (o.match_num) return `num:${o.match_num}`;
    return o.opt_id;
  };

  const optById = (lg: FinalLeg, optId: string): FinalOption | undefined =>
    (lg.options || []).find((x) => x.opt_id === optId);

  /** 同场双选是否允许：仅方案D/C 的非进球弱腿，且该场存在 ≥2 个可选项 */
  const canDouble = (plan: string, lg: FinalLeg): boolean =>
    lg.kind !== "goals" && (plan === "D" || plan === "C") && (lg.options || []).length >= 2;

  const sameChoice = (a: string | string[] | null | undefined, b: string | string[]): boolean =>
    toList(a).sort().join(",") === toList(b).sort().join(",");

  /** 点击选项：单选切换 / 同场第二项点按组成双选 / 已双选再点取消其中一个 / 非弱单选腿再点回到默认不改 */
  const toggleOpt = (plan: string, lg: FinalLeg, o: FinalOption) => {
    const idx = lg.idx;
    if (idx == null) return;
    const cur = (sel[plan] || {})[idx];
    const curList = toList(cur);
    const sameGrp = canDouble(plan, lg);
    let next: string | string[];
    if (!cur) {
      next = o.opt_id;
    } else if (!lg.weak && typeof cur === "string" && cur === o.opt_id) {
      // 非弱单选项腿：取消人工指定 → 保持系统默认（无选择）
      const nextSel = { ...(sel[plan] || {}) };
      delete nextSel[idx];
      setSel((s) => ({ ...s, [plan]: nextSel }));
      return;
    } else if (Array.isArray(cur)) {
      if (cur.includes(o.opt_id)) {
        const rest = cur.filter((x) => x !== o.opt_id);
        next = rest.length === 1 ? rest[0] : lg.default_opt_id ?? rest[0] ?? o.opt_id;
      } else {
        const firstSame = curList.some((id) => {
          const f = optById(lg, id);
          return f ? optMatchKey(f) === optMatchKey(o) : false;
        });
        next = sameGrp && cur.length < 2 && firstSame ? [...cur, o.opt_id] : [o.opt_id];
      }
    } else {
      // 当前单选
      if (cur === o.opt_id) return;
      const curOpt = optById(lg, cur);
      const sameField = curOpt ? optMatchKey(curOpt) === optMatchKey(o) : false;
      next = sameGrp && sameField ? [cur, o.opt_id] : o.opt_id;
    }
    setSel((s) => ({ ...s, [plan]: { ...(s[plan] || {}), [idx]: next } }));
  };

  const loadHistory = useCallback(async () => {
    try {
      setHistory(await getMarketFlowParlayFinalHistory());
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
      const day = await getMarketFlowParlayFinalDay({ date: d });
      setData(day);
      const init: Record<string, Record<number, string | string[]>> = {};
      for (const plan of Object.keys(day.plans)) {
        const v = day.plans[plan];
        if (!v?.exists) continue;
        init[plan] = {};
        for (const lg of v.legs || []) {
          if (!lg.options || lg.options.length === 0) continue;
          const saved = v.confirm?.meta?.choices?.[String(lg.idx)];
          if (saved != null) {
            init[plan][lg.idx] = saved; // 已确认快照：弱腿/非弱人工指定均恢复
          } else if (lg.weak) {
            init[plan][lg.idx] = lg.default_opt_id ?? lg.options[0].opt_id;
          }
          // 非弱单选项腿：未有人工指定 → 保持默认（不预选）
        }
      }
      setSel(init);
    } catch {
      setError("终稿数据加载失败");
    } finally {
      setLoading(false);
    }
  }, [d]);

  useEffect(() => {
    load();
  }, [load]);

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

  const doConfirm = async (plan: string, view: ParlayFinalPlanView, allDefault: boolean) => {
    setConfirming(plan);
    setError(null);
    try {
      const choices: Record<string, FinalChoiceSel> = {};
      if (!allDefault) {
        const planSel = sel[plan] || {};
        for (const lg of view.legs || []) {
          if (!lg.options || lg.options.length === 0) continue;
          if (lg.weak) {
            const opt = planSel[lg.idx] ?? lg.default_opt_id ?? lg.options[0].opt_id;
            choices[String(lg.idx)] = opt;
          } else {
            // 非弱单选项腿：仅当人工选择了目标场才提交改选，否则保持默认
            const opt = planSel[lg.idx];
            if (opt != null) choices[String(lg.idx)] = opt;
          }
        }
      }
      const res = await confirmMarketFlowParlayFinal({ date: d, plan, choices });
      if ((res as { error?: string }).error) {
        toast.toast((res as { error: string }).error, "error");
      } else {
        toast.toast(`${PLAN_META[plan]?.label ?? plan} 终稿已落库`, "success");
        await Promise.all([load(), loadHistory()]);
      }
    } catch {
      toast.toast("确认失败", "error");
    } finally {
      setConfirming(null);
    }
  };

  /** 推荐分桶 + 人工指定分组（manual 其余场次与 2 个高置信推荐区分开） */
  const bucketGroups = (lg: FinalLeg): Array<{ key: string; label: string; isOther: boolean; opts: FinalOption[] }> => {
    const opts = lg.options || [];
    const groups: Array<{ key: string; label: string; isOther: boolean; opts: FinalOption[] }> = [];
    const rec1 = opts.filter((o) => o.bucket === "rec1");
    if (rec1.length) groups.push({ key: "rec1", label: "推荐① · 当前默认场（同场）", isOther: false, opts: rec1 });
    const rec2 = opts.filter((o) => o.bucket === "rec2");
    if (rec2.length) {
      const f = rec2[0];
      groups.push({
        key: "rec2",
        label: `推荐② · 高置信场次（${f.home_team ?? "?"} vs ${f.away_team ?? "?"}${f.match_num ? ` · ${f.match_num}` : ""}）`,
        isOther: true,
        opts: rec2,
      });
    }
    const manual = opts.filter((o) => o.bucket === "manual");
    const map = new Map<string, FinalOption[]>();
    for (const o of manual) {
      const k = String(o.match_id ?? o.match_num ?? o.opt_id);
      if (!map.has(k)) map.set(k, []);
      map.get(k)!.push(o);
    }
    const nManual = map.size;
    groups.push({ key: "manual__header", label: `__manual__${nManual}`, isOther: true, opts: [] });
    for (const [k, os] of map) {
      const f = os[0];
      groups.push({
        key: `manual__${k}`,
        label: `${f.home_team ?? "?"} vs ${f.away_team ?? "?"}${f.match_num ? `（${f.match_num}）` : ""}`,
        isOther: true,
        opts: os,
      });
    }
    return groups;
  };

  const legBlock = (plan: string, lg: FinalLeg, isManual = false) => {
    const [kindTag, kindCls] = KIND_TAG[lg.kind] || KIND_TAG.dir;
    const isHafu = lg.kind === "hafu" || lg.source === "hafu" || lg.source === "favorite_hafu";
    const isVirtual = lg.pick == null;   // 手工新增：尚未选定比赛的腿
    const tagTxt = isVirtual ? "待选定"
      : lg.kind === "goals" ? `${kindTag}${lg.dir === "over" ? "·判大" : lg.dir === "under" ? "·判小" : ""}`
      : lg.kind === "halfdraw" ? (String(lg.code).toUpperCase() === "R2" ? "半全场·次选R2" : "半平·首选R1")
      : isHafu ? "半全场" : kindTag;
    const planSel = sel[plan] || {};
    const chosen = lg.weak
      ? planSel[lg.idx] ?? lg.default_opt_id ?? lg.options?.[0]?.opt_id
      : planSel[lg.idx]; // 非弱单选项腿：不预选，仅当人工指定目标场后才有选择
    const groups = bucketGroups(lg);
    const recGroups = groups.filter((g) => g.key === "rec1" || g.key === "rec2");
    const manualHeader = groups.find((g) => g.key === "manual__header");
    const manualN = manualHeader ? Number(manualHeader.label.split("__").pop()) : 0;
    const manualGroups = groups.filter((g) => g.key.startsWith("manual__") && g.key !== "manual__header");
    const optRow = (o: FinalOption) => {
      const on = toList(chosen).includes(o.opt_id);
      return (
        <label key={o.opt_id} className={`flex items-center gap-2 rounded border px-2 py-1 cursor-pointer text-[11px] ${on ? "border-moss bg-moss/10" : "border-highlight bg-white"}`}>
          <input type="checkbox" className="accent-[#4a5d3a]" checked={on}
            onChange={() => toggleOpt(plan, lg, o)} />
          <span className="text-[10px] text-ink-light font-mono shrink-0">{o.match_num || "-"}</span>
          <span className={`px-1 py-0.5 rounded text-[10px] ${o.is_default ? "bg-moss/10 text-moss" : "bg-amber/15 text-amber"}`}>
            {o.dir ? (o.dir === "over" ? "判大" : "判小") : o.is_default ? "该场默认" : "备选档"}
          </span>
          <span className="font-semibold text-ink">{o.pick}</span>
          <span className="text-ink-muted">@{o.odds.toFixed(2)}</span>
          <span className="text-ink-light">p_hat {(o.p_hat * 100).toFixed(0)}%</span>
        </label>
      );
    };
    return (
      <div key={lg.idx} className={`rounded-md border p-2.5 ${lg.weak ? "border-amber/50 bg-amber/5" : "border-highlight bg-parchment-light/40"}`}>
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-[10px] text-ink-light font-mono">{lg.match_num || "-"}</span>
          <span className={`text-[10px] px-1.5 py-0.5 rounded ${kindCls}`}>{tagTxt}</span>
          {lg.league_name ? <span className="text-[10px] text-ink-light">{lg.league_name}</span> : null}
          {lg.weak ? (
            <span className="text-[10px] text-amber font-semibold ml-auto">
              低命中腿 · 历史命中 {(lg.weak_hit != null ? lg.weak_hit * 100 : 0).toFixed(0)}%
            </span>
          ) : null}
        </div>
        <div className="text-[12px] text-ink mt-1">
          {lg.home_team || "?"} <span className="text-ink-muted text-[10px]">vs</span> {lg.away_team || "?"}
        </div>
        {lg.weak && (lg.options || []).length > 0 ? (
          <div className="mt-2 space-y-2">
            {canDouble(plan, lg) && (
              <div className="text-[10px] text-moss/80">
                同场双选：同一场的「该场默认 + 备选档」可同时勾选 = 2注复式，任一命中即该腿命中（仅方案D/C）
              </div>
            )}
            {recGroups.length > 0 && (
              <div className="space-y-2">
                {recGroups.map((g) => (
                  <div key={g.key} className={`rounded border px-2 py-1.5 ${g.key === "rec1" ? "border-moss/40 bg-moss/5" : "border-highlight bg-parchment-light/40"}`}>
                    <div className="text-[10px] font-semibold text-ink mb-1">{g.label}</div>
                    <div className="space-y-1">{g.opts.map(optRow)}</div>
                  </div>
                ))}
              </div>
            )}
            {manualN > 0 && (
              <details className="rounded border border-highlight bg-parchment-light/30 px-2 py-1">
                <summary className="text-[10px] text-ink-light cursor-pointer select-none">
                  人工指定 · 其余场次（{manualN} 场）—— 与高置信推荐区分开
                </summary>
                <div className="mt-1.5 space-y-2 max-h-[220px] overflow-y-auto">
                  {manualGroups.map((g) => (
                    <div key={g.key}>
                      <div className="text-[10px] text-ink-light mb-0.5">{g.label}</div>
                      <div className="space-y-1">{g.opts.map(optRow)}</div>
                    </div>
                  ))}
                </div>
              </details>
            )}
            {toList(chosen).length === 2 && (
              <div className="text-[10px] font-semibold text-amber">
                同场双选已开启：2注复式（{toList(chosen).map((id) => optById(lg, id)?.pick ?? id).join(" / ")}）
              </div>
            )}
          </div>
        ) : !lg.weak && (lg.options || []).length > 0 ? (
          <div className="mt-2 space-y-2">
            <div className="flex items-center gap-2 flex-wrap text-[11px]">
              <span className={`font-bold ${lg.pick ? "text-ink" : "text-amber"}`}>{lg.pick ?? "待选定"}</span>
              {lg.odds != null && <span className="text-ink-muted">@{lg.odds.toFixed(2)}</span>}
              {chosen != null && (
                <span className="text-[10px] text-amber font-semibold">
                  已改指：{(optById(lg, toList(chosen)[0])?.home_team ?? lg.home_team)} vs{" "}
                  {optById(lg, toList(chosen)[0])?.away_team ?? lg.away_team}{" "}
                  {optById(lg, toList(chosen)[0])?.pick} @{optById(lg, toList(chosen)[0])?.odds?.toFixed(2)}
                </span>
              )}
            </div>
            {manualN > 0 && (isManual ? (
              <div className="rounded border border-amber/40 bg-amber/5 px-2 py-1.5 space-y-2 max-h-[280px] overflow-y-auto">
                <div className="text-[10px] font-semibold text-amber">请为本条腿选一场比赛（共 {manualN} 场可选）</div>
                {manualGroups.map((g) => (
                  <div key={g.key}>
                    <div className="text-[10px] text-ink-light mb-0.5">{g.label}</div>
                    <div className="space-y-1">{g.opts.map(optRow)}</div>
                  </div>
                ))}
              </div>
            ) : (
              <details className="rounded border border-highlight bg-parchment-light/30 px-2 py-1">
                <summary className="text-[10px] text-ink-light cursor-pointer select-none">
                  人工指定 · 改指当日其他场次（{manualN} 场）—— 不选则保持系统默认
                </summary>
                <div className="mt-1.5 space-y-2 max-h-[220px] overflow-y-auto">
                  {manualGroups.map((g) => (
                    <div key={g.key}>
                      <div className="text-[10px] text-ink-light mb-0.5">{g.label}</div>
                      <div className="space-y-1">{g.opts.map(optRow)}</div>
                    </div>
                  ))}
                </div>
              </details>
            ))}
          </div>
        ) : (
          <div className="flex items-center gap-2 mt-1.5 flex-wrap text-[11px]">
            <span className={`font-bold ${lg.pick ? "text-ink" : "text-amber"}`}>{lg.pick ?? "待选定"}</span>
            {lg.odds != null && <span className="text-ink-muted">@{lg.odds.toFixed(2)}</span>}
            {lg.weak && <span className="text-[10px] text-amber">无可用选项（当日该玩法候选不足）</span>}
            {!lg.weak && !lg.pick && <span className="text-[10px] text-rust">该腿当日无可用候选</span>}
          </div>
        )}
      </div>
    );
  };

  const planCard = (plan: string) => {
    const view = data?.plans?.[plan];
    const meta = PLAN_META[plan];
    if (!view || !meta) return null;
    if (view.error) {
      return (
        <div className="rounded-md border border-rust/40 bg-rust/5 px-3 py-2 text-[11px] text-rust">
          {meta.label} 读取失败：{view.error}
        </div>
      );
    }
    if (!view.exists) {
      return (
        <div className="rounded-md border border-highlight bg-parchment-light/50 px-3 py-3 text-xs text-ink-light text-center">
          {meta.label}：当日无组合
        </div>
      );
    }
    const hasConfirm = !!view.confirm;
    const isManual = !!view.manual;
    const changedN = view.confirm?.meta?.changed_legs?.length ?? 0;
    const planSel = sel[plan] || {};
    const pendingChanged = (view.legs || []).filter((lg) => {
      if (!lg.options || lg.options.length < 1) return false;
      if (lg.weak) return !sameChoice(planSel[lg.idx], lg.default_opt_id as string);
      return planSel[lg.idx] != null; // 非弱单选腿：一旦人工选择目标场即视为待确认改选
    }).length;
    // 手工新增：每条腿都必须选定才能确认
    const missSlots = (view.legs || []).filter((lg) => (lg.options || []).length > 0 && planSel[lg.idx] == null);
    const canManualConfirm = !isManual || missSlots.length === 0;

    return (
      <div className={`bg-white rounded-md border overflow-hidden ${hasConfirm ? "border-moss/50" : isManual ? "border-amber/50" : "border-border"}`}>
        <div className={`px-3 py-2 border-b flex items-center gap-2 flex-wrap ${hasConfirm ? "border-moss/20 bg-moss/10" : isManual ? "border-amber/30 bg-amber/5" : "border-highlight bg-parchment-light/60"}`}>
          <span className="text-xs font-bold text-ink">{meta.label}</span>
          <span className="text-[10px] text-ink-light">{meta.desc}</span>
          {isManual && !hasConfirm && (
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber/15 text-amber font-semibold">手工新增（系统当日无组合）</span>
          )}
          {view.combo_level && (
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-parchment-dark text-ink-light">{view.combo_level}</span>
          )}
          {view.parlay_odds != null && <span className="text-[10px] text-ink-light">串关赔率 {view.parlay_odds.toFixed(2)}</span>}
          {hasConfirm ? (
            <>
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-moss/10 text-moss font-bold">
                ✓ 已确认{view.confirm?.meta?.manual ? "（手工）" : ""}
              </span>
              {view.confirm?.combo_level && <span className="text-[10px] text-ink-light">终稿异联赛 {view.confirm.combo_level}</span>}
              {changedN > 0 && <span className="text-[10px] text-amber">改选 {changedN} 腿</span>}
            </>
          ) : (
            <span className="text-[10px] text-ink-muted">未确认</span>
          )}
          {isManual && missSlots.length > 0 && <span className="text-[10px] text-rust">还需选定 {missSlots.length} 条腿</span>}
          {!isManual && pendingChanged > 0 && !hasConfirm && <span className="text-[10px] text-amber">待确认改选 {pendingChanged} 腿</span>}
        </div>
        <div className="p-3 space-y-2">
          {(view.legs || []).map((lg) => legBlock(plan, lg, isManual))}
        </div>
        <div className="px-3 py-2 border-t border-highlight flex items-center gap-2 flex-wrap">
          <button onClick={() => doConfirm(plan, view, false)} disabled={confirming === plan || !canManualConfirm}
            className="px-3 py-1.5 text-xs bg-amber text-white rounded hover:bg-amber/90 disabled:opacity-50 font-semibold">
            {confirming === plan ? "确认中..." : hasConfirm ? "重新确认（覆盖）" : isManual ? "确认手工终稿" : "确认终稿"}
          </button>
          {!isManual && (
            <button onClick={() => doConfirm(plan, view, true)} disabled={confirming === plan}
              className="px-3 py-1.5 text-xs text-ink-muted border border-border rounded hover:border-moss disabled:opacity-50">
              全默认出稿
            </button>
          )}
          <span className="text-[10px] text-ink-light ml-auto">
            {isManual
              ? "系统当日无组合：请在每条腿各选一场比赛后确认（自动校验异场；手工方案不限定串关赔率区间）"
              : `注数 ${view.stake} · 弱腿推荐①/② + 人工指定（非弱腿亦可改指）；C/D 半全场可同场双选（默认+备选=2注）`}
          </span>
        </div>
      </div>
    );
  };

  const stats = history?.stats;

  return (
    <div className="space-y-4">
      <div className="px-4 py-3 bg-parchment-dark rounded-lg border border-border-dark space-y-2">
        <div className="flex items-center gap-3 min-h-8">
          <span className="flex items-center gap-2 min-w-0 flex-1 text-xs text-moss font-semibold"
            title="终稿 · 弱腿给 2 个高置信场次推荐（默认/备选档）+ 其余场次人工指定；所有腿可改指当日其他场次（大小球进球腿全量列，方向/半全场按玩法合格池）→ 与系统默认分开统计">
            <span className="w-2 h-2 rounded-full bg-moss shrink-0" />
            <span className="truncate">终稿 · 弱腿给 2 个高置信场次推荐（默认/备选档）+ 其余场次人工指定；所有腿可改指当日其他场次（大小球进球腿全量列，方向/半全场按玩法合格池）→ 与系统默认分开统计</span>
          </span>
          <div className="flex items-center gap-2 shrink-0">
            <input type="date" value={d} onChange={(e) => setD(e.target.value)}
              className="font-mono text-xs text-ink bg-white border border-border rounded-md px-2 py-1 outline-none [color-scheme:light]" />
          </div>
        </div>
        {/* 方案切换 tab：固定第二行，各子方案视图位置一致 */}
        <div className="flex gap-0.5 bg-white rounded p-0.5 border border-border text-[11px] w-fit flex-wrap">
          {pills.map(([k, label]) => (
            <span key={k} className={`px-2 py-0.5 rounded cursor-pointer ${k === "f" ? "bg-amber text-white font-semibold" : "text-ink-muted"}`}
              onClick={() => onNavigate?.(k)}>{label}</span>
          ))}
        </div>
      </div>

      {/* 命中率统计：系统默认 vs 人工终稿 */}
      <div className="bg-white rounded-md border border-border overflow-hidden">
        <div className="px-4 py-2.5 border-b border-highlight bg-parchment-light/60 text-xs font-bold text-ink-light uppercase tracking-wide">
          终稿命中率统计 · 系统默认 vs 人工终稿（按方案分组）
        </div>
        {stats && stats.confirm_n > 0 ? (
          <div className="p-3 space-y-3">
            <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
              {Object.entries(stats.by_plan || {}).map(([plan, s]) => (
                <div key={plan} className="bg-parchment-light/40 rounded-md border border-highlight p-3">
                  <div className="text-[10px] text-ink-light mb-1.5">{PLAN_META[plan]?.label ?? plan} · 已确认 {s.confirm_n} 天 / 已结算 {s.settled_n}</div>
                  <div className="flex items-center justify-between text-[11px]">
                    <span className="text-ink-muted">系统默认</span>
                    <span className="font-heading font-bold">{s.default_p_hit == null ? "-" : `${(s.default_p_hit * 100).toFixed(0)}%`}</span>
                  </div>
                  <div className="flex items-center justify-between text-[11px] mt-1">
                    <span className="text-ink-muted">人工终稿</span>
                    <span className={`font-heading font-bold ${(s.final_p_hit ?? 0) > (s.default_p_hit ?? 0) ? "text-moss" : (s.final_p_hit ?? 0) < (s.default_p_hit ?? 0) ? "text-rust" : "text-ink"}`}>
                      {s.final_p_hit == null ? "-" : `${(s.final_p_hit * 100).toFixed(0)}%`}
                    </span>
                  </div>
                  <div className="flex items-center justify-between text-[10px] text-ink-muted mt-1.5">
                    <span>改进 {s.improved_n} / 持平 {s.same_n} / 恶化 {s.worsened_n}</span>
                  </div>
                  <div className="flex items-center justify-between text-[10px] mt-1">
                    <span className="text-ink-muted">ROI 默认 vs 终稿</span>
                    <span>{s.default_roi == null ? "-" : `${(s.default_roi * 100).toFixed(0)}%`} vs {s.final_roi == null ? "-" : `${(s.final_roi * 100).toFixed(0)}%`}</span>
                  </div>
                  {(s.manual_n ?? 0) > 0 && (
                    <div className="flex items-center justify-between text-[10px] mt-1">
                      <span className="text-amber">手工新增（系统无对照）</span>
                      <span className="text-amber">
                        {s.manual_n} 天 · 已结算 {s.manual_settled_n ?? 0}
                        {(s.manual_settled_n ?? 0) > 0 ? ` · 命中 ${s.manual_p_hit == null ? "-" : `${(s.manual_p_hit * 100).toFixed(0)}%`}` : ""}
                      </span>
                    </div>
                  )}
                </div>
              ))}
            </div>
            {stats.changed_legs && stats.changed_legs.n > 0 && (
              <div className="text-[11px] text-ink-muted">
                人工改选腿级对照（共 {stats.changed_legs.n} 腿）：默认命中 {stats.changed_legs.default_p_hit == null ? "-" : `${(stats.changed_legs.default_p_hit * 100).toFixed(0)}%`}
                {" "}→ 终稿命中 {stats.changed_legs.final_p_hit == null ? "-" : `${(stats.changed_legs.final_p_hit * 100).toFixed(0)}%`}
              </div>
            )}
            <div className="divide-y divide-highlight max-h-[320px] overflow-y-auto border-t border-highlight">
              {history!.rows.map((r) => {
                const dv = r.default;
                const fv = r.final;
                const manualRow = !!r.manual || (r.default_legs || []).length === 0;
                const dTxt = manualRow ? "系统 无方案" : !dv.settled ? "未结算" : dv.hit ? "默认✓" : "默认✗";
                const fTxt = !fv.settled ? "未结算" : fv.hit ? "终稿✓" : "终稿✗";
                return (
                  <div key={`${r.pick_date}-${r.plan}`} className="px-4 py-2 text-[11px]">
                    <div className="flex items-center gap-3 flex-wrap">
                      <span className="font-mono font-semibold text-ink w-24">{r.pick_date}</span>
                      <span className="px-1.5 py-0.5 rounded text-[10px] bg-moss/10 text-moss">{PLAN_META[r.plan]?.label ?? r.plan}</span>
                      {manualRow && <span className="px-1.5 py-0.5 rounded text-[10px] bg-amber/15 text-amber font-semibold">手工新增</span>}
                      <span className={`font-semibold ${dv.settled && dv.hit ? "text-ink" : "text-ink-light"}`}>{dTxt}</span>
                      <span className={`font-semibold ${fv.settled && fv.hit ? "text-moss" : "text-ink-light"}`}>{fTxt}</span>
                      {fv.payout != null && <span className="text-ink-muted">终稿返奖 {fv.payout.toFixed(2)} / {fv.stake ?? 1}注</span>}
                    </div>
                    {r.changed_legs.length > 0 && (
                      <div className="mt-1 flex flex-col gap-0.5 pl-2 border-l-2 border-amber/40 ml-3">
                        {r.changed_legs.map((cl, i) => (
                          <span key={i} className="text-[10px] text-ink-light">
                            改选腿：{String(cl.default_pick)} → {String(cl.final_pick)}
                            <span className="ml-1">
                              {cl.default_hit == null ? "默认未结算" : cl.default_hit ? "默认✓" : "默认✗"}
                              {" / "}
                              {cl.final_hit == null ? "终稿未结算" : cl.final_hit ? "终稿✓" : "终稿✗"}
                            </span>
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        ) : (
          <div className="px-4 py-6 text-center text-xs text-ink-light">
            暂无已确认终稿：每日对各方案腿做确认后，这里会展示「系统默认 vs 人工终稿」命中对照
          </div>
        )}
      </div>

      {/* 当日 三方案 + 候选确认 */}
      {!data ? (
        <EmptyState icon="🗂" message="暂无终稿数据" />
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
          {planCard("A")}
          {planCard("D")}
          {planCard("C")}
          {planCard("G")}
        </div>
      )}
    </div>
  );
}
