import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  getMarketFlowLive,
  getMarketFlowHistory,
  getMarketFlowPools,
  predictMarketFlow,
  syncMarketFlowOdds,
  syncMarketFlowSmOdds,
  type MarketFlowLiveSummary,
  type MarketFlowPredictionItem,
  type MarketFlowHistorySummary,
  type MarketFlowPoolsResponse,
} from "../api/client";
import SkeletonCard from "../components/Skeleton";
import EmptyState from "../components/EmptyState";
import ErrorState from "../components/ErrorState";
import { useToast } from "../components/Toast";

type TabKey = "live" | "history" | "pools";
type HistoryView = "by_league" | "by_date";

const TAB_LABELS: Record<TabKey, string> = {
  live: "当日预测",
  history: "历史报告",
  pools: "池化分析",
};

const OUTCOME_LABEL: Record<string, string> = {
  home: "主胜",
  draw: "平局",
  away: "客胜",
};

// 大小球方向置信度门控 —— 动态档位（对应后端 ou_direction.TIERS）
const OU_TIERS: { key: string; label: string; desc: string }[] = [
  { key: "full", label: "全量", desc: "无门控，全量判向" },
  { key: "noise", label: "去噪", desc: "跳过 P大∈(0.48,0.52) 噪声带" },
  { key: "loose", label: "宽松", desc: "P大≥0.58 判大 / ≤0.42 判小" },
  { key: "standard", label: "标准", desc: "P大≥0.62 判大 / ≤0.38 判小" },
  { key: "strict", label: "严格", desc: "P大≥0.65 判大 / ≤0.35 判小" },
];

const OU_DIRECTION_LABEL: Record<string, string> = { over: "大球", under: "小球", skip: "跳过" };

// SportMonks O/U 独立大小球盘口 —— 置信度门控（对应后端 ou_market.TIERS）
const OU_SM_TIERS: { key: string; label: string; desc: string }[] = [
  { key: "full", label: "全量", desc: "无门控，O/U 全量判向" },
  { key: "loose", label: "宽松", desc: "|Δ|≥0.08 判向" },
  { key: "standard", label: "标准", desc: "|Δ|≥0.10 判向（验证 70.8% / 覆盖30%）" },
  { key: "strict", label: "严格", desc: "|Δ|≥0.15 判向（验证 76.2% / 覆盖12.6%）" },
];

function outcomeColor(outcome: "home" | "draw" | "away" | string, hit?: boolean | null) {
  if (hit === true) return "bg-red-500/10 text-red-600 border border-red-500/40";
  if (hit === false) return "bg-parchment-dark text-ink-muted border border-parchment-dark/80";
  const base: Record<string, string> = {
    home: "bg-moss/10 text-moss border border-moss/30",
    draw: "bg-amber/15 text-amber border border-amber/35",
    away: "bg-rust/10 text-rust border border-rust/35",
  };
  return base[outcome] || "bg-parchment-dark text-ink-muted border border-parchment-dark/80";
}

export default function MarketFlowV2Page() {
  const [tab, setTab] = useState<TabKey>("live");
  return (
    <div className="p-4 max-w-[1440px] mx-auto">
      <div className="flex items-center gap-4 mb-4 flex-wrap">
        <h1 className="text-lg font-bold font-heading tracking-wide text-ink">MarketFlow V2 · 方向与比分预测</h1>
        <div className="flex gap-0.5 bg-parchment-dark rounded p-0.5 font-body text-xs">
          {(["live", "history", "pools"] as const).map((k) => (
            <span
              key={k}
              className={`px-3.5 py-1.5 rounded cursor-pointer transition-colors ${
                tab === k ? "bg-white text-ink font-semibold shadow-sm" : "text-ink-muted"
              }`}
              onClick={() => setTab(k)}
            >
              {TAB_LABELS[k]}
            </span>
          ))}
        </div>
      </div>
      {tab === "live" ? <LiveView /> : tab === "history" ? <HistoryView /> : <PoolsView />}
    </div>
  );
}

// =========== 当日预测 ===========
function LiveView() {
  const [date, setDate] = useState<string>(() => {
    const d = new Date();
    return d.toISOString().slice(0, 10);
  });
  const [rows, setRows] = useState<MarketFlowPredictionItem[]>([]);
  const [summary, setSummary] = useState<MarketFlowLiveSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [leagueFilter, setLeagueFilter] = useState<string | null>(null);
  const [predicting, setPredicting] = useState(false);
  const [syncingOdds, setSyncingOdds] = useState(false);
  const [syncingSmOdds, setSyncingSmOdds] = useState(false);
  const [ouTier, setOuTier] = useState("standard");
  const [ouSmTier, setOuSmTier] = useState("standard");
  const toast = useToast();

  const fetchData = useCallback(() => {
    setLoading(true);
    setError(null);
    getMarketFlowLive({ date, ou_tier: ouTier, ou_sm_tier: ouSmTier })
      .then((res) => {
        setRows(res.data || []);
        setSummary(res.summary || null);
      })
      .catch(() => setError("加载失败"))
      .finally(() => setLoading(false));
  }, [date, ouTier, ouSmTier]);

  useEffect(() => { fetchData(); }, [fetchData]);

  const leagues = useMemo(() => {
    const s = new Set<string>();
    rows.forEach((r) => { if (r.league_name) s.add(r.league_name); });
    return Array.from(s);
  }, [rows]);

  const filtered = useMemo(() => {
    let list = rows;
    if (leagueFilter) list = list.filter((r) => r.league_name === leagueFilter);
    if (search.trim()) {
      const q = search.toLowerCase();
      list = list.filter((r) =>
        r.home_team.toLowerCase().includes(q) ||
        r.away_team.toLowerCase().includes(q) ||
        (r.league_name || "").toLowerCase().includes(q) ||
        (r.match_num || "").toLowerCase().includes(q)
      );
    }
    return list.sort((a, b) => a.kickoff_time.localeCompare(b.kickoff_time));
  }, [rows, leagueFilter, search]);

  // 当日视图是否存在 O/U 数据（用于给缺失场次标注"无数据"，避免误以为是渲染缺失）
  const hasAnyOu = useMemo(() => rows.some((r) => !!r.ou_sm), [rows]);

  const handleSyncOdds = async () => {
    setSyncingOdds(true);
    try {
      const res: any = await syncMarketFlowOdds({ date });
      const inserted = res?.data?.inserted ?? 0;
      const windowTotal = res?.data?.window_total ?? 0;
      const msg = res?.message || `赔率拉取完成（写入 ${inserted} / ${windowTotal} 场）`;
      toast.toast(msg, inserted > 0 ? "success" : "info");
      fetchData();
    } catch (e: any) {
      toast.toast(e?.response?.data?.message || e?.message || "赔率拉取失败", "error");
    } finally {
      setSyncingOdds(false);
    }
  };

  const handleSyncSmOdds = async () => {
    setSyncingSmOdds(true);
    try {
      const res: any = await syncMarketFlowSmOdds({ date });
      const inserted = res?.data?.inserted ?? 0;
      const msg = res?.message || `SM O/U 拉取完成（写入 ${inserted} 行）`;
      toast.toast(msg, inserted > 0 ? "success" : "info");
      fetchData();
    } catch (e: any) {
      toast.toast(e?.response?.data?.message || e?.message || "SM O/U 拉取失败", "error");
    } finally {
      setSyncingSmOdds(false);
    }
  };

  const handlePredict = async () => {
    setPredicting(true);
    try {
      // overwrite=true：支持重复执行（重新预测并覆盖已有预测）
      const res: any = await predictMarketFlow({ date, overwrite: true });
      toast.toast(res?.message || "MarketFlow V2 预测完成", "success");
      fetchData();
    } catch {
      toast.toast("MarketFlow V2 预测失败", "error");
    } finally {
      setPredicting(false);
    }
  };

  if (loading) {
    return (
      <div className="space-y-4">
        <div className="h-12 bg-parchment-dark rounded-lg animate-pulse" />
        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-3">
          {Array.from({ length: 6 }).map((_, i) => <SkeletonCard key={i} />)}
        </div>
      </div>
    );
  }
  if (error) return <ErrorState message={error} onRetry={fetchData} />;

  return (
    <div className="space-y-4">
      <SummaryBar
        summary={summary} date={date} setDate={setDate}
        onRefresh={fetchData}
        onPredict={handlePredict}
        predicting={predicting}
        onSyncOdds={handleSyncOdds}
        syncingOdds={syncingOdds}
        onSyncSmOdds={handleSyncSmOdds}
        syncingSmOdds={syncingSmOdds}
        ouTier={ouTier}
        setOuTier={setOuTier}
        ouSmTier={ouSmTier}
        setOuSmTier={setOuSmTier}
      />

      <div className="flex items-center gap-3 px-3 py-2.5 bg-white rounded-md border border-border flex-wrap">
        <div className="flex items-center gap-2 px-2.5 py-1.5 bg-parchment-light border border-border rounded min-w-[220px]">
          <span className="text-xs text-ink-light">🔍</span>
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="搜索球队/联赛/场次..."
            className="bg-transparent outline-none text-xs text-ink w-full font-body"
          />
        </div>
        <span className="w-px h-5 bg-border" />
        <span
          className={`px-3 py-1 text-xs rounded-full cursor-pointer transition-colors ${!leagueFilter ? "bg-moss text-white" : "border border-border text-ink-muted hover:border-moss"}`}
          onClick={() => setLeagueFilter(null)}
        >全部联赛</span>
        {leagues.map((l) => (
          <span
            key={l}
            className={`px-3 py-1 text-xs rounded-full cursor-pointer transition-colors ${l === leagueFilter ? "bg-moss text-white" : "border border-border text-ink-muted hover:border-moss"}`}
            onClick={() => setLeagueFilter(l === leagueFilter ? null : l)}
          >{l}</span>
        ))}
        <span className="ml-auto text-xs text-ink-muted">共 <strong className="text-ink">{filtered.length}</strong> / {rows.length} 场</span>
      </div>

      {filtered.length === 0 ? (
        <EmptyState icon="📈" message="暂无 MarketFlow V2 当日预测" description="请先点击「预测」按钮执行 MarketFlow V2 跑批或切换日期" />
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-3">
          {filtered.map((r) => <PredictionCard key={r.id} row={r} ouActive={hasAnyOu} />)}
        </div>
      )}
    </div>
  );
}

function SummaryBar({
  summary, date, setDate, onRefresh, onPredict, predicting,
  onSyncOdds, syncingOdds, onSyncSmOdds, syncingSmOdds, ouTier, setOuTier, ouSmTier, setOuSmTier,
}: {
  summary: MarketFlowLiveSummary | null;
  date: string; setDate: (d: string) => void; onRefresh: () => void;
  onPredict: () => void; predicting: boolean;
  onSyncOdds?: () => void; syncingOdds?: boolean;
  onSyncSmOdds?: () => void; syncingSmOdds?: boolean;
  ouTier: string; setOuTier: (t: string) => void;
  ouSmTier: string; setOuSmTier: (t: string) => void;
}) {
  return (
    <div className="px-4 py-3 bg-parchment-dark rounded-lg border border-border-dark">
      <div className="flex items-center gap-3 flex-wrap">
        <span className="flex items-center gap-2 text-xs text-moss font-semibold">
          <span className="w-2 h-2 rounded-full bg-moss animate-pulse" />
          MarketFlow V2（C 融合版）
        </span>
        <span className="text-xs text-ink-muted">{date} · 预测 {summary?.n_predicted ?? 0} 场 · 已结算 {summary?.n_with_results ?? 0}</span>
        <span className="text-xs text-ink-muted">
          至少排除 1 <span className="text-moss font-semibold">{summary?.outcome_keep_one ?? 0}</span>
          {" · "}方向命中 <span className="text-moss font-semibold">{summary?.outcome_hit ?? 0}</span>
          {" · "}优选命中 <span className="text-moss font-semibold">{summary?.preferred_hit ?? 0}</span>
          {" · "}比分 Top2 <span className="text-moss font-semibold">{summary?.score_top2_hit ?? 0}</span>
          {" · "}比分 Top3 <span className="text-moss font-semibold">{summary?.score_top3_hit ?? 0}</span>
        </span>
        <div className="ml-auto flex items-center gap-2 flex-wrap">
          <input
            type="date"
            value={date}
            max={new Date().toISOString().slice(0, 10)}
            onChange={(e) => setDate(e.target.value)}
            className="font-mono text-xs text-ink bg-white border border-border rounded-md px-2 py-1 outline-none [color-scheme:light]"
          />
          <button
            onClick={onSyncOdds}
            disabled={syncingOdds || predicting}
            title="从竞彩网拉取当日/未来实时赔率（HAD+HHAD+TTG+CRS四玩法）"
            className="px-3 py-1.5 text-xs bg-amber/15 text-amber border border-amber/40 rounded hover:bg-amber/25 disabled:opacity-50 transition-colors font-body"
          >
            {syncingOdds ? "赔率拉取中..." : "拉取赔率"}
          </button>
          <button
            onClick={onSyncSmOdds}
            disabled={syncingSmOdds || syncingOdds || predicting}
            title="从 SportMonks 拉取赛前 O/U（大小球 over/under，含 2.5 线）盘口写入 odds_snapshots，供下方 O/U盘口判断使用"
            className="px-3 py-1.5 text-xs bg-rust/15 text-rust border border-rust/40 rounded hover:bg-rust/25 disabled:opacity-50 transition-colors font-body"
          >
            {syncingSmOdds ? "SM 拉取中..." : "拉取SM赔率"}
          </button>
          <button
            onClick={onPredict}
            disabled={predicting}
            className="px-4 py-1.5 text-xs bg-moss text-white rounded hover:opacity-90 disabled:opacity-50 transition-opacity font-body"
          >
            {predicting ? "预测中..." : "预测"}
          </button>
          <button
            onClick={onRefresh}
            className="px-3 py-1.5 text-xs text-ink-muted border border-border rounded hover:border-moss transition-colors"
          >刷新</button>
        </div>
      </div>
      {/* 大小球方向 · 置信度门控（动态档位） */}
      <div className="mt-2.5 pt-2.5 border-t border-border-dark/70 flex items-center gap-1.5 flex-wrap">
        <span className="text-[10px] text-ink-light mr-1" title="方向+置信度门控模型：直接读 TTG 总进球市场定价判大小球方向，低置信度跳过。切换档位实时重算命中。">
          大小球方向 · 置信度门控
        </span>
        {OU_TIERS.map((t) => {
          const st = summary?.ou_tiers?.[t.key];
          const active = ouTier === t.key;
          const rate = st?.settled ? Math.round((st.hit / st.settled) * 100) : null;
          return (
            <button
              key={t.key}
              onClick={() => setOuTier(t.key)}
              title={`${t.desc} · 本窗口 ${st ? `${st.bet}注(${st.skip}跳过)/${st.settled}结算` : "-"}`}
              className={`px-2 py-1 text-[10px] rounded font-semibold transition-colors ${
                active
                  ? "bg-ink text-white shadow-sm"
                  : "border border-border text-ink-muted hover:border-moss hover:text-moss"
              }`}
            >
              {t.label}
              {rate != null && (
                <span className={`ml-1 font-mono ${active ? "text-amber" : "text-ink-light"}`}>{rate}%</span>
              )}
            </button>
          );
        })}
        {summary?.ou_settled != null && summary.ou_settled > 0 && (
          <span className="ml-auto text-[10px] text-ink-light font-mono">
            当前档 {OU_TIERS.find((t) => t.key === ouTier)?.label}：命中 <span className="text-moss font-bold">{summary.ou_hit}/{summary.ou_settled}</span>
            {" "}（{Math.round(((summary.ou_hit ?? 0) / summary.ou_settled) * 100)}%）· 跳过 {summary.ou_skip} 场
          </span>
        )}
      </div>
      {/* SportMonks O/U 独立大小球盘口 · 置信度门控 */}
      <div className="mt-2 pt-2 border-t border-border-dark/50 flex items-center gap-1.5 flex-wrap">
        <span className="text-[10px] text-ink-light mr-1" title="独立大小球盘口（SportMonks O/U 2.5线，多博彩公司中位数）判大小球方向，|Δ|越高越可信。标准档验证命中 70.8% / 覆盖30%。">
          O/U盘口 · 置信度门控
        </span>
        {OU_SM_TIERS.map((t) => {
          const st = summary?.ou_sm_tiers?.[t.key];
          const active = ouSmTier === t.key;
          const rate = st?.settled ? Math.round((st.hit / st.settled) * 100) : null;
          return (
            <button
              key={t.key}
              onClick={() => setOuSmTier(t.key)}
              title={`${t.desc} · 本窗口 ${st ? `${st.bet}注(${st.skip}跳过)/${st.settled}结算` : "-"}`}
              className={`px-2 py-1 text-[10px] rounded font-semibold transition-colors ${
                active
                  ? "bg-moss text-white shadow-sm"
                  : "border border-border text-ink-muted hover:border-moss hover:text-moss"
              }`}
            >
              {t.label}
              {rate != null && (
                <span className={`ml-1 font-mono ${active ? "text-amber" : "text-ink-light"}`}>{rate}%</span>
              )}
            </button>
          );
        })}
        {summary?.ou_sm_settled != null && summary.ou_sm_settled > 0 && (
          <span className="ml-auto text-[10px] text-ink-light font-mono">
            当前档 {OU_SM_TIERS.find((t) => t.key === ouSmTier)?.label}：命中 <span className="text-moss font-bold">{summary.ou_sm_hit}/{summary.ou_sm_settled}</span>
            {" "}（{Math.round(((summary.ou_sm_hit ?? 0) / summary.ou_sm_settled) * 100)}%）· 跳过 {summary.ou_sm_skip} 场
          </span>
        )}
      </div>
    </div>
  );
}

function PredictionCard({ row, onlyPreferred = false, onlyCold = false, ouActive = false }: { row: MarketFlowPredictionItem; onlyPreferred?: boolean; onlyCold?: boolean; ouActive?: boolean }) {
  const kickoffShort = row.kickoff_time.slice(11, 16);
  const allow = row.allowed_outcomes || [];
  // 正路池只显示优选方向；冷门池只显示确定的冷门方向（强冷门=优选 / 警示冷门=参考）
  const shown = onlyCold && row.cold_dir
    ? [row.cold_dir]
    : onlyPreferred && row.preferred_outcome
      ? allow.filter((o: any) => o === row.preferred_outcome)
      : allow;
  return (
    <div className="bg-white rounded-lg border border-border p-4 shadow-sm">
      <div className="flex items-center gap-2 mb-3 flex-wrap">
        {row.match_num && (
          <span className="text-[10px] font-bold text-ink-muted bg-parchment-dark px-1.5 py-0.5 rounded">{row.match_num}</span>
        )}
        {row.league_name && (
          <span className="text-[10px] text-ink-light bg-parchment-light px-2 py-0.5 rounded tracking-wide flex-1 truncate">{row.league_name}</span>
        )}
        <span className="text-[11px] text-ink-muted">{kickoffShort}</span>
        <span
          className="text-[10px] px-1.5 py-0.5 rounded bg-highlight text-moss border border-moss/30 font-semibold"
          title={row.model_version}
        >MV</span>
        {row.had_pref && (
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-white border border-border-dark font-semibold text-ink-muted" title="盘口偏好（强排保护）">
            PREF:{OUTCOME_LABEL[row.had_pref] || row.had_pref}
          </span>
        )}
        {row.pool === "upset" && !row.cold_signal && (
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-rust text-white font-semibold" title="强冷门：让球方∉二选，优选=二选内非让球方隐含最高，方向可下注">
            强冷门
          </span>
        )}
        {row.cold_signal && (
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-rust/10 text-rust border border-rust/40 font-semibold" title="警示冷门：平局隐含≥0.28，冷门率≈60%，仅参考冷门方向不做优选">
            警示冷门
          </span>
        )}
        {row.cold_dir && row.pool === "upset" && (
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-rust/5 text-rust border border-rust/20 font-semibold" title={row.cold_signal ? "冷门参考方向（非让球方，优先平局），仅参考不做优选" : "已确定的冷门方向（二选内非让球方隐含最高）"}>
            {row.cold_signal ? "冷门参考:" : "冷门方向:"}{OUTCOME_LABEL[row.cold_dir] || row.cold_dir}
          </span>
        )}
      </div>
      <div className="flex items-center justify-between gap-1.5 mb-3">
        <span className="font-heading text-[15px] font-bold text-center flex-1 truncate leading-tight">{row.home_team}</span>
        <span className="text-[11px] text-ink-light font-semibold shrink-0">VS</span>
        <span className="font-heading text-[15px] font-bold text-center flex-1 truncate leading-tight">{row.away_team}</span>
      </div>

      {/* 方向预测：只显示选中（allowed）的方向，优选方向高亮 */}
      <div className="flex items-center gap-1.5 mb-2.5 flex-wrap">
        <span className="text-[10px] text-ink-light shrink-0 mr-1">方向</span>
        {shown.map((o: any) => {
          const isActual = row.actual_outcome === o;
          const hit = row.actual_outcome ? (allow.includes(o as any) && isActual) : null;
          const isPreferred = row.preferred_outcome === o;
          let cls;
          if (hit === true) {
            cls = "px-2 py-1 text-[11px] font-extrabold rounded border bg-red-50 text-red-600 border-red-500/50 shadow-sm";
          } else if (isPreferred) {
            cls = "px-2.5 py-1 text-[11px] font-bold rounded-md bg-ink text-white shadow-md ring-1 ring-ink/30";
          } else {
            cls = outcomeColor(o, false);
          }
          return (
            <span
              key={o}
              className={`${cls} ${isPreferred && hit !== true ? "relative" : ""}`}
              title={isPreferred ? "优选方向" : ""}
            >
              {isPreferred && <span className="mr-0.5 text-[9px] opacity-80">★</span>}
              {OUTCOME_LABEL[o]}
            </span>
          );
        })}
        {onlyCold && row.cold_signal && row.cold_dir && (
          <span className="text-[9px] px-1 py-0.5 rounded bg-rust/5 text-rust/80 border border-dashed border-rust/30">仅参考</span>
        )}
        {row.enforce_excluded?.[0] && (
          <span className="text-[10px] text-ink-light ml-auto" title="anchor 第一位强排结果">
            ENFORCE:{OUTCOME_LABEL[row.enforce_excluded[0]] || row.enforce_excluded[0]}
          </span>
        )}
      </div>

      {/* 总进球 Top2 —— cfusion MV 时 *值* 被替换为 Model C Poisson 前 2 名，但 TopN 数量固定为 2 */}
      <div className="px-3 py-2 bg-parchment-light rounded-md mb-2.5">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span className="text-[10px] text-ink-light shrink-0 mr-2">总进球 Top2</span>
            {row.is_cfusion && (
              <span className="text-[9px] px-1.5 py-0.5 rounded bg-amber/15 text-amber border border-amber/30 font-semibold">
                C融合
              </span>
            )}
          </div>
          <div className="flex items-center gap-1.5">
            {(row.total_goals_top2 || []).map((g: number, i: number) => (
              <span
                key={i}
                className={`font-heading font-bold flex items-center justify-center shadow-sm ${
                  i === 0
                    ? "w-9 h-9 rounded-md bg-moss text-white text-lg"
                    : "w-9 h-9 rounded-md border-2 border-moss text-moss text-lg"
                } ${row.actual_total_goals != null && row.actual_total_goals === g ? "ring-1 ring-red-500/60" : ""}`}
              >{g >= 6 ? "6+" : g}</span>
            ))}
            {row.actual_total_goals != null && (
              <span className="text-[11px] text-red-600 font-bold ml-2">实:{row.actual_total_goals}</span>
            )}
          </div>
        </div>
        {row.is_cfusion && row.fusion?.expected_goals_c != null && (
          <div className="mt-1.5 pt-1.5 border-t border-highlight/60 text-[10px] text-amber/90 flex items-center gap-1.5 flex-wrap">
            <span>🎯 ModelC Poisson</span>
            <span className="font-mono font-bold">λ={Number(row.fusion.expected_goals_c).toFixed(2)}</span>
            {row.fusion.snap_top2_c && (
              <span className="font-mono text-ink-muted">
                C Top2=[{row.fusion.snap_top2_c.join(",")}]
              </span>
            )}
            {row.fusion.total_goals_top3_c && (
              <span className="font-mono text-ink-muted">
                C Top3=[{row.fusion.total_goals_top3_c.join(",")}]
              </span>
            )}
            {row.total_hit_top2 === true && <span className="text-moss font-bold">✓C融合Top2</span>}
          </div>
        )}
        {/* 大小球方向 · 置信度门控模型（方向为主 + 整数补充） */}
        {row.ou_direction && (
          <div className="mt-1.5 pt-1.5 border-t border-highlight/60 flex items-center gap-1.5 flex-wrap">
            <span className="text-[10px] text-ink-light shrink-0" title="方向+置信度门控：直接读 TTG 总进球市场定价判大小球方向，低置信度跳过">
              大小球{row.ou_tier ? `(${OU_TIERS.find((t) => t.key === row.ou_tier)?.label ?? row.ou_tier})` : ""}
            </span>
            {row.ou_direction.direction === "skip" ? (
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-parchment-dark text-ink-muted border border-border font-semibold">
                跳过
              </span>
            ) : (
              <span
                className={`text-[10px] px-1.5 py-0.5 rounded font-bold border ${
                  row.ou_direction.direction === "over"
                    ? "bg-rust/10 text-rust border-rust/40"
                    : "bg-moss/10 text-moss border-moss/40"
                }`}
                title={`判定线 ${row.ou_direction.goal_line ?? 2.5}`}
              >
                {OU_DIRECTION_LABEL[row.ou_direction.direction] || row.ou_direction.direction}
                <span className="font-mono opacity-70">({row.ou_direction.direction === "over" ? ">" : "<"}{row.ou_direction.goal_line ?? 2.5})</span>
              </span>
            )}
            <span className="font-mono text-[10px] text-ink-muted">
              P大={row.ou_direction.p_big.toFixed(2)} 桶:{row.ou_direction.bucket} 盘口:{row.ou_direction.goal_line ?? 2.5}
            </span>
            {row.ou_direction.top2_ints.length >= 2 && (
              <span className="font-mono text-[10px] text-ink-muted" title="市场隐含概率最高的2个进球数（补充参考，不用于下注）">
                市场Top2=[{row.ou_direction.top2_ints.join(",")}]
              </span>
            )}
            {row.ou_hit != null && (
              <span className={`text-[10px] font-bold ${row.ou_hit ? "text-moss" : "text-rust"}`}>
                {row.ou_hit ? "✓命中" : "✗未中"}
              </span>
            )}
          </div>
        )}
        {/* SportMonks O/U 独立盘口 · 方向 + 置信度门控 */}
        {row.ou_sm && (
          <div className="mt-1.5 pt-1.5 border-t border-highlight/60 flex items-center gap-1.5 flex-wrap">
            <span className="text-[10px] text-ink-light shrink-0" title="独立大小球盘口（SportMonks O/U 2.5线，多博彩公司中位数）。标准档验证命中 70.8% / 覆盖30%。">
              O/U盘口{row.ou_sm_tier ? `(${OU_SM_TIERS.find((t) => t.key === row.ou_sm_tier)?.label ?? row.ou_sm_tier})` : ""}
            </span>
            {row.ou_sm.direction === "skip" ? (
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-parchment-dark text-ink-muted border border-border font-semibold">
                跳过
              </span>
            ) : (
              <span
                className={`text-[10px] px-1.5 py-0.5 rounded font-bold border ${
                  row.ou_sm.direction === "over"
                    ? "bg-rust/10 text-rust border-rust/40"
                    : "bg-moss/10 text-moss border-moss/40"
                }`}
                title={`判定线 ${row.ou_sm.goal_line ?? 2.5}`}
              >
                {OU_DIRECTION_LABEL[row.ou_sm.direction] || row.ou_sm.direction}
                <span className="font-mono opacity-70">({row.ou_sm.direction === "over" ? ">" : "<"}{row.ou_sm.goal_line ?? 2.5})</span>
              </span>
            )}
            <span className="font-mono text-[10px] text-ink-muted">
              P大={row.ou_sm.p_big.toFixed(2)} |Δ|={row.ou_sm.delta.toFixed(2)} 盘口:{row.ou_sm.goal_line ?? 2.5} 家数={row.ou_sm.n_books}
            </span>
            {row.ou_sm.lines && Object.keys(row.ou_sm.lines).length > 1 && (
              <div className="w-full flex items-center gap-1 flex-wrap pt-0.5">
                {Object.entries(row.ou_sm.lines)
                  .map(([gl, l]) => ({ gl: parseFloat(gl), l }))
                  .filter(({ gl, l }) => l.direction !== "skip" && Math.abs(gl - (row.ou_sm.goal_line ?? 2.5)) > 1e-9)
                  .sort((a, b) => a.gl - b.gl)
                  .map(({ gl, l }) => (
                    <span
                      key={gl}
                      className={`text-[10px] px-1 py-0.5 rounded border font-mono ${
                        l.direction === "over"
                          ? "bg-rust/10 text-rust border-rust/30"
                          : "bg-moss/10 text-moss border-moss/30"
                      }`}
                      title={`${gl}线 O/U: P大=${l.p_big.toFixed(2)} |Δ|=${l.delta.toFixed(2)} 家数=${l.n_books}`}
                    >
                      {gl}:{l.direction === "over" ? "大" : "小"}
                    </span>
                  ))}
              </div>
            )}
            {row.ou_sm_hit != null && (
              <span className={`text-[10px] font-bold ${row.ou_sm_hit ? "text-moss" : "text-rust"}`}>
                {row.ou_sm_hit ? "✓命中" : "✗未中"}
              </span>
            )}
          </div>
        )}
        {!row.ou_sm && ouActive && (
          <div className="mt-1.5 pt-1.5 border-t border-highlight/60 flex items-center gap-1.5 flex-wrap">
            <span className="text-[10px] text-ink-light shrink-0">O/U盘口</span>
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-parchment-dark text-ink-light border border-border font-semibold" title="SportMonks 未采集到该场 2.5 线 O/U 盘口，无信号">
              无数据
            </span>
          </div>
        )}
      </div>

      {/* 比分 Top3 */}
      <div className="px-3 py-2.5 bg-parchment-light rounded-md">
        <div className="text-[10px] text-ink-light mb-1.5">比分 Top3</div>
        <div className="flex items-center gap-1.5 flex-wrap">
          {(row.score_top3 || []).map((s, i) => (
            <span
              key={i}
              className={`font-heading font-bold ${
                i === 0
                  ? "px-2.5 py-1 rounded-md bg-moss text-white shadow-sm text-sm"
                  : i === 1
                    ? "px-2.5 py-1 rounded-md border border-moss text-moss text-sm"
                    : "px-2.5 py-1 rounded-md bg-white border border-border-dark text-ink text-sm"
              } ${row.actual_score === s ? "ring-1 ring-red-500/70" : ""}`}
            >{s}</span>
          ))}
          {row.actual_score && (
            <span className="text-[11px] text-red-600 font-bold ml-auto">实:{row.actual_score}</span>
          )}
        </div>
      </div>

      {/* 底部状态条 */}
      <div className="flex items-center gap-2 mt-2.5 pt-2.5 border-t border-highlight text-[10px] text-ink-light flex-wrap">
        <span>MV: {row.model_version}</span>
        {row.snapshot_time && <span>快照: {row.snapshot_time.slice(5, 16).replace("T", " ")}</span>}
        <span className="ml-auto">
          {row.outcome_hit === true && <span className="text-moss font-semibold mr-1.5">✓方向</span>}
          {row.preferred_hit === true && <span className="text-moss font-bold mr-1.5">★优选</span>}
          {row.cold_signal && row.cold_dir && row.actual_outcome === row.cold_dir && (
            <span className="text-rust font-bold mr-1.5">✓冷门参考</span>
          )}
          {row.total_hit_top2 === true && <span className="text-moss font-semibold mr-1.5">✓总进球</span>}
          {row.score_hit_top2 === true && <span className="text-moss font-semibold mr-1.5">✓比分Top2</span>}
          {row.score_hit_top3 === true && <span className="text-moss font-semibold">✓比分Top3</span>}
          {row.outcome_hit === false && <span className="text-rust font-semibold mr-1.5">✗方向</span>}
          {row.preferred_hit === false && <span className="text-rust font-semibold mr-1.5">★优选</span>}
          {row.cold_signal && row.cold_dir && row.actual_outcome != null && row.actual_outcome !== row.cold_dir && (
            <span className="text-ink-light font-semibold mr-1.5">✗冷门参考</span>
          )}
          {row.score_hit_top3 === false && <span className="text-rust font-semibold">✗比分Top3</span>}
        </span>
      </div>
    </div>
  );
}

// =========== 历史报告 ===========
function HistoryView() {
  const [start, setStart] = useState<string>(() => {
    const d = new Date(); d.setDate(d.getDate() - 6);
    return d.toISOString().slice(0, 10);
  });
  const [end, setEnd] = useState<string>(() => {
    const d = new Date();
    return d.toISOString().slice(0, 10);
  });
  const [rows, setRows] = useState<MarketFlowPredictionItem[]>([]);
  const [summary, setSummary] = useState<MarketFlowHistorySummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [view, setView] = useState<HistoryView>("by_date");
  const [ouTier, setOuTier] = useState("standard");
  const [ouSmTier, setOuSmTier] = useState("standard");
  // 按日期统计表格分页：每页 5 天
  const [page, setPage] = useState(1);
  const PAGE_SIZE = 5;
  // 请求序号守卫：丢弃过期响应，防快速切换档位/日期时的竞态
  const fetchSeq = useRef(0);

  // 用户点 by_date 时，当前日期快速筛选：近3/近7/近14/近30/自定义
  const [quickRange, setQuickRange] = useState<"3d" | "7d" | "14d" | "30d" | "custom">("7d");

  // 选中的行（点击某日期行/某联赛行后过滤底部明细）：null 表示全部
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const [selectedLeague, setSelectedLeague] = useState<string | null>(null);

  const fetchData = useCallback(() => {
    setLoading(true);
    setError(null);
    getMarketFlowHistory({ start_date: start, end_date: end, ou_tier: ouTier, ou_sm_tier: ouSmTier })
      .then((res) => {
        setRows(res.data || []);
        setSummary(res.summary || null);
      })
      .catch(() => setError("加载失败"))
      .finally(() => setLoading(false));
  }, [start, end, ouTier, ouSmTier]);

  useEffect(() => { fetchData(); }, [fetchData]);

  // 快速日期范围切换（改变 start/end 后自动触发 fetchData）
  useEffect(() => {
    if (quickRange === "custom") return;
    const days = quickRange === "3d" ? 3 : quickRange === "7d" ? 7 : quickRange === "14d" ? 14 : 30;
    const today = new Date();
    const d = new Date(); d.setDate(d.getDate() - (days - 1));
    const newStart = d.toISOString().slice(0, 10);
    const newEnd = today.toISOString().slice(0, 10);
    if (newStart !== start) setStart(newStart);
    if (newEnd !== end) setEnd(newEnd);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [quickRange]);

  // 切换 view 时清空选中行
  useEffect(() => {
    setSelectedDate(null);
    setSelectedLeague(null);
  }, [view]);

  const hitRate = (n: number, total: number) =>
    total > 0 ? ((n / total) * 100).toFixed(1) : "0.0";

  // 与后端 _matchday_date 同步：把一场预测映射到比赛日 D
  const matchdayOf = (r: MarketFlowPredictionItem): string => {
    const iso = r.kickoff_time || "";
    if (iso.length < 13) return iso.slice(0, 10);
    const y = parseInt(iso.slice(0, 4), 10);
    const m = parseInt(iso.slice(5, 7), 10);
    const d = parseInt(iso.slice(8, 10), 10);
    const h = parseInt(iso.slice(11, 13), 10);
    if (Number.isNaN(y) || Number.isNaN(m) || Number.isNaN(d) || Number.isNaN(h)) {
      return iso.slice(0, 10);
    }
    if (r.match_num && /^周[一二三四五六日]/.test(r.match_num)) {
      if (h < 12) {
        const prev = new Date(Date.UTC(y, m - 1, d));
        prev.setUTCDate(prev.getUTCDate() - 1);
        const py = prev.getUTCFullYear();
        const pm = String(prev.getUTCMonth() + 1).padStart(2, "0");
        const pd = String(prev.getUTCDate()).padStart(2, "0");
        return `${py}-${pm}-${pd}`;
      }
      const mm = String(m).padStart(2, "0");
      const dd = String(d).padStart(2, "0");
      return `${y}-${mm}-${dd}`;
    }
    const base = new Date(Date.UTC(y, m - 1, d, h));
    base.setUTCHours(base.getUTCHours() - 12);
    const py = base.getUTCFullYear();
    const pm = String(base.getUTCMonth() + 1).padStart(2, "0");
    const pd = String(base.getUTCDate()).padStart(2, "0");
    return `${py}-${pm}-${pd}`;
  };

  // preferred_hit / outcome_hit 的值形如 "8/14=57.14%"，提取命中率用于排序
  const pctFromValue = (v?: string): number => {
    if (!v || v === "0/0=-") return -1;
    const m = /^(\d+)\/(\d+)=([\d.]+)%$/.exec(v);
    if (!m) return -1;
    return parseFloat(m[3]);
  };

  const allByDate = summary?.by_date || [];
  const allByLeague = summary?.by_league || [];

  // 视图数据排序（与后端保持一致，前端兜底二次排序）：
  //   by_date   → date 降序（最新日期在上）
  //   by_league → preferred_hit 降序（相同则按 league_name 升序稳定排）
  const viewData: any[] = useMemo(() => {
    const raw = view === "by_league" ? allByLeague : allByDate;
    const copy = [...(raw || [])];
    if (view === "by_date") {
      copy.sort((a, b) => String(b.date || "").localeCompare(String(a.date || "")));
    } else if (view === "by_league") {
      copy.sort((a, b) => {
        const pa = pctFromValue(a.preferred_hit);
        const pb = pctFromValue(b.preferred_hit);
        if (pa !== pb) return pb - pa;
        return String(a.league_name || "").localeCompare(String(b.league_name || ""));
      });
    }
    return copy;
  }, [view, allByDate, allByLeague]);

  // 按日期统计表格分页：每页 5 天（by_league 不分页）
  const totalPages = Math.max(1, Math.ceil(viewData.length / PAGE_SIZE));
  const pageData = useMemo(() => {
    const s = (page - 1) * PAGE_SIZE;
    return viewData.slice(s, s + PAGE_SIZE);
  }, [viewData, page]);
  const tableRows = view === "by_date" ? pageData : viewData;
  // 切换视图或数据变化时回到第一页
  useEffect(() => { setPage(1); }, [view, viewData]);

  const dimLabel = view === "by_league" ? "联赛" : "日期";
  const dimKey = view === "by_league" ? "league_name" : "date";
  const title = view === "by_league" ? "按联赛统计" : "按日期统计";

  const filteredRows = useMemo(() => {
    let out = rows;
    if (view === "by_date" && selectedDate) {
      out = out.filter(r => matchdayOf(r) === selectedDate);
    } else if (view === "by_league" && selectedLeague) {
      out = out.filter(r => r.league_name === selectedLeague);
    }
    return out;
  }, [rows, view, selectedDate, selectedLeague]);

  const clearSelection = () => {
    setSelectedDate(null);
    setSelectedLeague(null);
  };

  if (loading) return <div className="h-64 bg-parchment-light rounded animate-pulse" />;
  if (error) return <ErrorState message={error} onRetry={fetchData} />;

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3 px-4 py-3 bg-parchment-dark rounded-lg border border-border-dark flex-wrap">
        <span className="flex items-center gap-2 text-xs text-moss font-semibold">
          <span className="w-2 h-2 rounded-full bg-moss" />
          历史命中率报告
        </span>
        <span className="text-xs text-ink-muted">
          {summary?.window_start?.slice(0, 10) || "-"} ~ {summary?.window_end?.slice(0, 10) || "-"}
          {" · "} 推荐 {summary?.n_total ?? 0} 场 · 已结算 {summary?.n_settled ?? 0}
        </span>
        <div className="ml-auto flex items-center gap-2 flex-wrap">
          <select
            value={quickRange}
            onChange={(e) => setQuickRange(e.target.value as any)}
            className="text-xs text-ink bg-white border border-border rounded-md px-2 py-1 outline-none"
          >
            <option value="3d">近 3 日</option>
            <option value="7d">近 7 日</option>
            <option value="14d">近 14 日</option>
            <option value="30d">近 30 日</option>
            <option value="custom">自定义范围</option>
          </select>
          <input type="date" value={start} max={end} onChange={(e) => { setStart(e.target.value); setQuickRange("custom"); }}
            className="font-mono text-xs text-ink bg-white border border-border rounded-md px-2 py-1 outline-none [color-scheme:light]" />
          <span className="text-ink-muted text-xs">至</span>
          <input type="date" value={end} onChange={(e) => { setEnd(e.target.value); setQuickRange("custom"); }}
            className="font-mono text-xs text-ink bg-white border border-border rounded-md px-2 py-1 outline-none [color-scheme:light]" />
          <button onClick={fetchData}
            className="px-3 py-1.5 text-xs text-ink-muted border border-border rounded hover:border-moss transition-colors">刷新</button>
        </div>
      </div>

      {/* 大小球方向 · 置信度门控（动态档位） */}
      <div className="flex items-center gap-1.5 px-3 py-2 bg-white rounded-md border border-border flex-wrap">
        <span className="text-[10px] text-ink-light mr-1" title="方向+置信度门控模型：直接读 TTG 总进球市场定价判大小球方向，低置信度跳过。切换档位实时重算命中。">
          大小球方向 · 置信度门控
        </span>
        {OU_TIERS.map((t) => {
          const st = summary?.ou_tiers?.[t.key];
          const active = ouTier === t.key;
          const rate = st?.settled ? Math.round((st.hit / st.settled) * 100) : null;
          return (
            <button
              key={t.key}
              onClick={() => setOuTier(t.key)}
              title={`${t.desc} · 窗口内 ${st ? `${st.bet}注(${st.skip}跳过)/${st.settled}结算` : "-"}`}
              className={`px-2 py-1 text-[10px] rounded font-semibold transition-colors ${
                active
                  ? "bg-ink text-white shadow-sm"
                  : "border border-border text-ink-muted hover:border-moss hover:text-moss"
              }`}
            >
              {t.label}
              {rate != null && (
                <span className={`ml-1 font-mono ${active ? "text-amber" : "text-ink-light"}`}>{rate}%</span>
              )}
            </button>
          );
        })}
        {summary?.ou_settled != null && summary.ou_settled > 0 && (
          <span className="ml-auto text-[10px] text-ink-light font-mono">
            当前档 {OU_TIERS.find((t) => t.key === ouTier)?.label}：命中 <span className="text-moss font-bold">{summary.ou_hit}/{summary.ou_settled}</span>
            {" "}（{Math.round(((summary.ou_hit ?? 0) / summary.ou_settled) * 100)}%）· 跳过 {summary.ou_skip} 场
          </span>
        )}
      </div>

      {/* SportMonks O/U 独立盘口 · 置信度门控 */}
      <div className="flex items-center gap-1.5 px-3 py-2 bg-white rounded-md border border-border flex-wrap">
        <span className="text-[10px] text-ink-light mr-1" title="独立大小球盘口（SportMonks O/U 2.5线）。标准档验证命中 70.8% / 覆盖30%。">
          O/U盘口 · 置信度门控
        </span>
        {OU_SM_TIERS.map((t) => {
          const st = summary?.ou_sm_tiers?.[t.key];
          const active = ouSmTier === t.key;
          const rate = st?.settled ? Math.round((st.hit / st.settled) * 100) : null;
          return (
            <button
              key={t.key}
              onClick={() => setOuSmTier(t.key)}
              title={`${t.desc} · 窗口内 ${st ? `${st.bet}注(${st.skip}跳过)/${st.settled}结算` : "-"}`}
              className={`px-2 py-1 text-[10px] rounded font-semibold transition-colors ${
                active
                  ? "bg-moss text-white shadow-sm"
                  : "border border-border text-ink-muted hover:border-moss hover:text-moss"
              }`}
            >
              {t.label}
              {rate != null && (
                <span className={`ml-1 font-mono ${active ? "text-amber" : "text-ink-light"}`}>{rate}%</span>
              )}
            </button>
          );
        })}
        {summary?.ou_sm_settled != null && summary.ou_sm_settled > 0 && (
          <span className="ml-auto text-[10px] text-ink-light font-mono">
            当前档 {OU_SM_TIERS.find((t) => t.key === ouSmTier)?.label}：命中 <span className="text-moss font-bold">{summary.ou_sm_hit}/{summary.ou_sm_settled}</span>
            {" "}（{Math.round(((summary.ou_sm_hit ?? 0) / summary.ou_sm_settled) * 100)}%）· 跳过 {summary.ou_sm_skip} 场
          </span>
        )}
      </div>

      <div className="flex items-center gap-1 bg-parchment-dark rounded p-0.5 w-fit flex-wrap">
        {([
          ["by_date", "按日期"],
          ["by_league", "按联赛"],
        ] as [HistoryView, string][]).map(([k, label]) => (
          <span
            key={k}
            onClick={() => setView(k)}
            className={`px-3.5 py-1.5 rounded text-xs cursor-pointer transition-colors ${
              view === k ? "bg-white text-ink font-semibold shadow-sm" : "text-ink-muted"
            }`}
          >{label}</span>
        ))}
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-8 gap-3">
        <StatBox label="已结算" value={`${summary?.n_settled ?? 0}`} />
        <StatBox
          label="方向命中率"
          value={`${hitRate(summary?.outcome_hit_total ?? 0, summary?.n_settled ?? 0)}%`}
          sub={`${summary?.outcome_hit_total ?? 0}/${summary?.n_settled ?? 0}`}
        />
        <StatBox
          label="方向优选命中"
          value={`${hitRate(summary?.preferred_hit_total ?? 0, summary?.n_pick_total ?? 0)}%`}
          sub={`${summary?.preferred_hit_total ?? 0}/${summary?.n_pick_total ?? 0}`}
          highlight
        />
        <StatBox
          label="进球 Top2 命中"
          value={`${hitRate(summary?.total_goals_top2_hit_total ?? 0, summary?.n_settled ?? 0)}%`}
          sub={`${summary?.total_goals_top2_hit_total ?? 0}/${summary?.n_settled ?? 0}`}
        />
        <StatBox
          label="比分 Top2 命中"
          value={`${hitRate(summary?.score_top2_hit_total ?? 0, summary?.n_settled ?? 0)}%`}
          sub={`${summary?.score_top2_hit_total ?? 0}/${summary?.n_settled ?? 0}`}
        />
        <StatBox
          label="比分 Top3 命中"
          value={`${hitRate(summary?.score_top3_hit_total ?? 0, summary?.n_settled ?? 0)}%`}
          sub={`${summary?.score_top3_hit_total ?? 0}/${summary?.n_settled ?? 0}`}
          highlight
        />
        <StatBox
          label={`大小球·${OU_TIERS.find((t) => t.key === ouTier)?.label}`}
          value={`${hitRate(summary?.ou_hit ?? 0, summary?.ou_settled ?? 0)}%`}
          sub={`${summary?.ou_hit ?? 0}/${summary?.ou_settled ?? 0}注`}
          highlight
        />
        <StatBox
          label={`O/U盘口·${OU_SM_TIERS.find((t) => t.key === ouSmTier)?.label}`}
          value={`${hitRate(summary?.ou_sm_hit ?? 0, summary?.ou_sm_settled ?? 0)}%`}
          sub={`${summary?.ou_sm_hit ?? 0}/${summary?.ou_sm_settled ?? 0}注`}
          highlight
        />
      </div>

      <div className="bg-white rounded-md border border-border overflow-hidden">
        <div className="px-4 py-3 border-b border-highlight bg-parchment-light/60 flex items-center gap-3 flex-wrap">
          <div className="text-xs font-bold text-ink-light uppercase tracking-wide">{title}</div>
          {((view === "by_date" && selectedDate) || (view === "by_league" && selectedLeague)) && (
            <span
              onClick={clearSelection}
              className="ml-auto text-[10px] px-2 py-0.5 rounded border border-border text-ink-muted hover:border-rust hover:text-rust cursor-pointer transition-colors shrink-0"
            >清除筛选 ×</span>
          )}
        </div>
        <div className="overflow-x-auto">
          <table className="min-w-full text-xs font-body">
            <thead>
              <tr className="bg-parchment-dark/60 text-ink-muted">
                <Th>{dimLabel}</Th>
                <Th>场次</Th>
                <Th>方向命中</Th>
                <Th highlight>优选命中</Th>
                <Th>进球Top2</Th>
                <Th>比分 Top2</Th>
                <Th highlight>比分 Top3</Th>
                <Th title={view === "by_date" ? "按所选大小球档位统计的方向命中" : undefined}>大小球命中</Th>
                <Th title={view === "by_date" ? "按所选O/U档位统计的方向命中" : undefined}>O/U命中</Th>
              </tr>
            </thead>
            <tbody>
              {tableRows.map((row: any, i: number) => {
                const isSelected =
                  (view === "by_date" && selectedDate === row.date) ||
                  (view === "by_league" && selectedLeague === row.league_name);
                return (
                  <tr
                    key={i}
                    onClick={() => {
                      if (view === "by_date") setSelectedDate(selectedDate === row.date ? null : row.date);
                      else setSelectedLeague(selectedLeague === row.league_name ? null : row.league_name);
                    }}
                    className={`border-t border-highlight cursor-pointer transition-colors ${
                      isSelected ? "bg-moss/10 hover:bg-moss/15" : "hover:bg-highlight/60"
                    }`}
                  >
                    {dimKey === "date" ? <TdCode>{row[dimKey]}</TdCode> : <Td>{row[dimKey]}</Td>}
                    <Td>{row.n}</Td>
                    <TdColor value={row.outcome_hit} />
                    <TdColor value={row.preferred_hit} highlight />
                    <TdColor value={row.total_goals_top2_hit} />
                    <TdColor value={row.score_top2_hit} />
                    <TdColor value={row.score_top3_hit} highlight />
                    <TdColor value={row.ou_hit} title={`大小球 · 跳过 ${row.ou_skip ?? 0} 场`} />
                    <TdColor value={row.ou_sm_hit} title={`O/U · 跳过 ${row.ou_sm_skip ?? 0} 场`} />
                  </tr>
                );
              })}
              {tableRows.length === 0 && (
                <tr><td colSpan={9} className="px-4 py-8 text-center text-ink-light">该区间暂无已结算预测</td></tr>
              )}
            </tbody>
          </table>
        </div>
        {view === "by_date" && totalPages > 1 && (
          <div className="flex items-center justify-end gap-2 px-4 py-2 border-t border-highlight">
            <button
              disabled={page <= 1}
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              className="px-2.5 py-1 text-[10px] text-ink-muted border border-border rounded hover:border-moss transition-colors disabled:opacity-40"
            >上一页</button>
            <span className="text-[10px] text-ink-muted font-mono">第 {page} / {totalPages} 页 · 共 {viewData.length} 天</span>
            <button
              disabled={page >= totalPages}
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              className="px-2.5 py-1 text-[10px] text-ink-muted border border-border rounded hover:border-moss transition-colors disabled:opacity-40"
            >下一页</button>
          </div>
        )}
      </div>

      {/* 底部场次列表 */}
      <div className="bg-white rounded-md border border-border p-4">
        <div className="flex items-center gap-2 mb-3 flex-wrap">
          <span className="text-xs font-bold text-ink-light uppercase tracking-wide">逐场明细（{filteredRows.length} 场）</span>
          <span className="text-[10px] text-ink-light">★=优选方向 · 红底加粗=方向命中</span>
          {((view === "by_date" && selectedDate) || (view === "by_league" && selectedLeague)) && (
            <span className="text-[10px] px-2 py-0.5 rounded bg-moss/10 text-moss border border-moss/30 shrink-0">
              {view === "by_date" ? `筛选: ${selectedDate}` : `筛选: ${selectedLeague}`}
            </span>
          )}
        </div>
        <div className="max-h-[520px] overflow-y-auto space-y-1.5 pr-1">
          {filteredRows.map((r) => {
            const settled = r.actual_outcome != null;
            return (
              <div key={r.id} className="flex items-center gap-2 px-3 py-2 rounded border border-highlight hover:bg-highlight/70 transition-colors flex-wrap">
                <span className="text-[11px] text-ink-muted font-mono w-[60px] shrink-0">{r.kickoff_time.slice(5, 16).replace("T", " ")}</span>
                {r.match_num && <span className="text-[10px] font-bold text-ink-muted bg-parchment-dark px-1.5 py-0.5 rounded shrink-0">{r.match_num}</span>}
                {r.league_name && <span className="text-[10px] text-ink-light bg-parchment-light px-1.5 py-0.5 rounded shrink-0 max-w-[100px] truncate">{r.league_name}</span>}
                <span className="font-semibold text-[11px] text-ink truncate flex-1 min-w-[160px]">{r.home_team} vs {r.away_team}</span>
                <span className="flex gap-1 items-center">
                  {(r.allowed_outcomes || []).map((o: any) => {
                    const hit = settled && r.actual_outcome === o;
                    const isPref = r.preferred_outcome === o;
                    const cls = hit
                      ? "px-1.5 py-0.5 text-[10px] rounded bg-red-50 text-red-600 border border-red-500/50 font-extrabold shadow-sm"
                      : isPref
                        ? "px-1.5 py-0.5 text-[10px] rounded bg-ink text-white shadow-sm font-bold"
                        : "px-1.5 py-0.5 text-[10px] rounded bg-moss/10 text-moss border border-moss/30 font-semibold";
                    return (
                      <span key={o} className={cls}>
                        {isPref && !hit && <span className="opacity-80 mr-0.5">★</span>}
                        {OUTCOME_LABEL[o]}
                      </span>
                    );
                  })}
                </span>
                {r.ou_direction && (
                  <span
                    className={`text-[10px] px-1.5 py-0.5 rounded font-bold border ${
                      r.ou_direction.direction === "over" ? "bg-rust/10 text-rust border-rust/40"
                      : r.ou_direction.direction === "under" ? "bg-moss/10 text-moss border-moss/40"
                      : "bg-parchment-dark text-ink-muted border-border font-semibold"
                    }`}
                    title={`大小球(TTG ${r.ou_tier}) · 判定线 ${r.ou_direction.goal_line ?? 2.5}`}
                  >
                    {r.ou_direction.direction === "skip" ? "大小球:跳过"
                      : r.ou_direction.direction === "over" ? `大(>${r.ou_direction.goal_line ?? 2.5})`
                      : `小(<${r.ou_direction.goal_line ?? 2.5})`}
                  </span>
                )}
                {r.ou_sm && (
                  <span
                    className={`text-[10px] px-1.5 py-0.5 rounded font-bold border ${
                      r.ou_sm.direction === "over" ? "bg-rust/10 text-rust border-rust/40"
                      : r.ou_sm.direction === "under" ? "bg-moss/10 text-moss border-moss/40"
                      : "bg-parchment-dark text-ink-muted border-border font-semibold"
                    }`}
                    title={`O/U盘口(${r.ou_sm_tier}) · 判定线 ${r.ou_sm.goal_line ?? 2.5}`}
                  >
                    {r.ou_sm.direction === "skip" ? "O/U:跳过"
                      : r.ou_sm.direction === "over" ? `O/U大(>${r.ou_sm.goal_line ?? 2.5})`
                      : `O/U小(<${r.ou_sm.goal_line ?? 2.5})`}
                  </span>
                )}
                <span className="text-[10px] font-mono text-ink-muted">S2:{r.score_top2?.join("/")}</span>
                <span className="text-[10px] font-mono text-ink-muted">S3:{r.score_top3?.join("/")}</span>
                {settled && (
                  <span className="text-[10px] font-bold text-red-600 shrink-0">实:{r.actual_score}</span>
                )}
              </div>
            );
          })}
          {filteredRows.length === 0 && (
            <div className="text-center text-ink-light py-6 text-xs">暂无比赛明细</div>
          )}
        </div>
      </div>
    </div>
  );
}

function StatBox({ label, value, sub, highlight }: { label: string; value: string; sub?: string; highlight?: boolean }) {
  return (
    <div className={`flex-1 min-w-[140px] bg-white rounded-md border p-3 text-center font-body ${highlight ? "border-moss shadow-sm bg-moss/[0.03]" : "border-border"}`}>
      <div className={`text-[10px] tracking-wide ${highlight ? "text-moss" : "text-ink-light"}`}>{label}</div>
      <div className={`text-xl font-bold font-heading mt-0.5 leading-tight ${highlight ? "text-moss" : "text-ink"}`}>{value}</div>
      {sub && <div className="text-[10px] text-ink-muted mt-0.5">{sub}</div>}
    </div>
  );
}

function Th({ children, highlight, title }: { children: React.ReactNode; highlight?: boolean; title?: string }) {
  return <th className={`text-left font-semibold px-4 py-2 whitespace-nowrap ${highlight ? "text-moss" : ""}`} title={title}>{children}</th>;
}
function Td({ children }: { children: React.ReactNode }) {
  return <td className="px-4 py-2 text-ink whitespace-nowrap">{children}</td>;
}
function TdCode({ children }: { children: React.ReactNode }) {
  return <td className="px-4 py-2 font-mono text-[11px] text-ink whitespace-nowrap">{children}</td>;
}
function TdColor({ value, color = "moss", highlight, title }: { value: string; color?: "moss" | "amber"; highlight?: boolean; title?: string }) {
  if (!value || value === "0/0=-") return <td className="px-4 py-2 text-ink-muted" title={title}>-</td>;
  const m = /^(\d+)\/(\d+)=([\d.]+)%$/.exec(value);
  const baseCls = color === "amber" ? "bg-amber/10 text-amber" : "bg-moss/10 text-moss";
  const pct = m ? parseFloat(m[3]) : 0;
  let levelCls = baseCls;
  if (color === "moss") {
    if (pct >= 80) levelCls = "bg-moss/20 text-moss font-bold";
    else if (pct >= 65) levelCls = "bg-moss/10 text-moss font-semibold";
    else if (pct < 50) levelCls = "bg-rust/10 text-rust";
  }
  return (
    <td className={`px-4 py-2 whitespace-nowrap ${highlight ? levelCls + " rounded-md" : ""}`} title={title}>
      {m ? <span className={`px-1.5 py-0.5 rounded border border-current/30 ${levelCls}`}>{m[1]}/{m[2]} <span className="opacity-70">({pct.toFixed(0)}%)</span></span> : value}
    </td>
  );
}

// =========== 池化分析（正路池 / 冷门池） ===========
function PoolsView() {
  const [date, setDate] = useState<string>(() => {
    const d = new Date();
    return d.toISOString().slice(0, 10);
  });
  const [data, setData] = useState<MarketFlowPoolsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [subTab, setSubTab] = useState<"daily" | "stats">("daily");
  const [trendDays, setTrendDays] = useState<number>(30);
  const [selDay, setSelDay] = useState<string | null>(null);
  const [dayRows, setDayRows] = useState<MarketFlowPredictionItem[]>([]);
  const [dayLoading, setDayLoading] = useState(false);
  const toast = useToast();

  const fetchData = useCallback(() => {
    setLoading(true);
    setError(null);
    getMarketFlowPools({ date, trend_days: trendDays })
      .then((res) => setData(res))
      .catch(() => setError("加载失败"))
      .finally(() => setLoading(false));
  }, [date, trendDays]);

  useEffect(() => { fetchData(); }, [fetchData]);

  const refresh = () => {
    fetchData();
    toast.toast("池化分析已刷新", "info");
  };

  // 每日统计：点击某行日期拉取当天预测明细（复用历史报告单日查询）
  const loadDay = (day: string) => {
    setSelDay(day);
    setDayLoading(true);
    getMarketFlowHistory({ start_date: day, end_date: day })
      .then((res) => setDayRows(res.data))
      .catch(() => setDayRows([]))
      .finally(() => setDayLoading(false));
  };

  // 选中日期明细按池分组（hook 必须位于任何条件早退之前）
  const dayByPool = useMemo(() => {
    const m: Record<string, MarketFlowPredictionItem[]> = { favorite: [], ambiguous: [], upset: [], unpooled: [] };
    (dayRows || []).forEach((r) => { (m[r.pool || "unpooled"] ?? m.unpooled).push(r); });
    return m;
  }, [dayRows]);

  if (loading) {
    return (
      <div className="space-y-4">
        <div className="h-12 bg-parchment-dark rounded-lg animate-pulse" />
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          {Array.from({ length: 3 }).map((_, i) => <SkeletonCard key={i} />)}
        </div>
      </div>
    );
  }
  if (error) return <ErrorState message={error} onRetry={fetchData} />;
  if (!data) return <EmptyState icon="🧊" message="暂无池化数据" description="请先执行当日 MarketFlow V2 预测或切换日期" />;

  const poolMap = new Map(data.pools.map((p) => [p.key, p]));
  const fav = poolMap.get("favorite");
  const amb = poolMap.get("ambiguous");
  const ups = poolMap.get("upset");
  const unpooled = poolMap.get("unpooled");
  const todayFav = data.today.favorite || [];
  const todayAmb = data.today.ambiguous || [];
  const todayUps = data.today.upset || [];
  const todayUn = data.today.unpooled || [];

  // 7 日滚动命中率：pick=优选命中 / cold=冷门方向命中(强优选+警参考) / both=双选命中（模糊池/不入池无优选方向）
  const rolling = (poolKey: "favorite" | "ambiguous" | "upset" | "unpooled", mode: "pick" | "cold" | "both") =>
    data.trend.map((t, i) => {
      const win = data.trend.slice(Math.max(0, i - 6), i + 1);
      let n = 0, hit = 0;
      win.forEach((w) => {
        const d = w[poolKey];
        if (d) {
          n += mode === "cold" ? (d.cold_n ?? d.n) : d.n;
          hit += mode === "both" ? d.both_hit : mode === "cold" ? d.cold_hit : d.hit;
        }
      });
      return { date: t.date, n, hit, rate: n > 0 ? hit / n : null };
    });
  const favTrend = rolling("favorite", "pick").slice(-14);
  const ambTrend = rolling("ambiguous", "both").slice(-14);
  const upsTrend = rolling("upset", "cold").slice(-14);

  const poolCard = (p: MarketFlowPoolsResponse["pools"][number], accent: string, subKey: string, mode: "pick" | "both" = "pick") => {
    const pct = mode === "both"
      ? (p.both_rate == null ? "-" : `${(p.both_rate * 100).toFixed(1)}%`)
      : (p.rate == null ? "-" : `${(p.rate * 100).toFixed(1)}%`);
    const hitColor = (mode === "both" ? p.both_rate : p.rate) != null && (mode === "both" ? p.both_rate! : p.rate!) >= 0.5 ? "text-moss" : "text-rust";
    return (
      <div className={`flex-1 min-w-[200px] bg-white rounded-md border p-4 ${accent}`}>
        <div className="flex items-center justify-between flex-wrap gap-2">
          <span className="text-xs font-bold text-ink-light tracking-wide">{p.label}</span>
          <span className="text-[10px] px-2 py-0.5 rounded bg-parchment-light text-ink-muted">今日 {p.today_n} 场</span>
        </div>
        <div className={`text-3xl font-bold font-heading mt-1 ${hitColor}`}>{pct}</div>
        <div className="text-[11px] text-ink-muted mt-1">{subKey}</div>
        <div className="text-[10px] text-ink-light mt-1.5 leading-relaxed">{p.desc}</div>
      </div>
    );
  };

  const PoolSection = ({ title, rows, tint, onlyPreferred = false, onlyCold = false }: { title: string; rows: MarketFlowPredictionItem[]; tint: string; onlyPreferred?: boolean; onlyCold?: boolean }) => (
    <div>
      <div className="flex items-center gap-2 mb-2">
        <span className={`text-xs font-bold px-2.5 py-1 rounded ${tint}`}>{title}</span>
        <span className="text-xs text-ink-muted">{rows.length} 场</span>
      </div>
      {rows.length === 0 ? (
        <div className="bg-white rounded-md border border-border px-4 py-6 text-center text-xs text-ink-light">该池暂无当日比赛</div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-3">
          {rows.map((r) => <PredictionCard key={r.id} row={r} onlyPreferred={onlyPreferred} onlyCold={onlyCold} />)}
        </div>
      )}
    </div>
  );

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3 px-4 py-3 bg-parchment-dark rounded-lg border border-border-dark flex-wrap">
        <span className="flex items-center gap-2 text-xs text-moss font-semibold">
          <span className="w-2 h-2 rounded-full bg-moss" />
          每日池化分析 · 正路池 / 模糊池 / 冷门池 / 不入池
        </span>
        <span className="text-xs text-ink-muted">
          {data.date} · 累计窗口 {data.days} 天（{data.window.start.slice(0, 10)} ~ {data.window.end.slice(0, 10)}）
        </span>
        <div className="ml-auto flex items-center gap-2 flex-wrap">
          <div className="flex gap-0.5 bg-white rounded p-0.5 border border-border text-xs">
            {([["daily", "当日明细"], ["stats", "每日统计"]] as const).map(([k, label]) => (
              <span
                key={k}
                className={`px-3 py-1.5 rounded cursor-pointer transition-colors ${
                  subTab === k ? "bg-parchment-dark text-ink font-semibold shadow-sm" : "text-ink-muted"
                }`}
                onClick={() => setSubTab(k)}
              >{label}</span>
            ))}
          </div>
          <input
            type="date"
            value={date}
            max={new Date().toISOString().slice(0, 10)}
            onChange={(e) => setDate(e.target.value)}
            className="font-mono text-xs text-ink bg-white border border-border rounded-md px-2 py-1 outline-none [color-scheme:light]"
          />
          <button onClick={refresh}
            className="px-3 py-1.5 text-xs text-ink-muted border border-border rounded hover:border-moss transition-colors">刷新</button>
        </div>
      </div>

      {subTab === "stats" ? (
        <div className="space-y-4">
          <div className="bg-white rounded-md border border-border overflow-hidden">
            <div className="px-4 py-3 border-b border-highlight bg-parchment-light/60 flex items-center gap-3 flex-wrap">
              <span className="text-xs font-bold text-ink-light uppercase tracking-wide">每日命中统计 · 近 {data.trend.length} 天</span>
              <div className="flex gap-0.5 bg-white rounded p-0.5 border border-border text-xs">
                {[3, 7, 30].map((d) => (
                  <span
                    key={d}
                    className={`px-2.5 py-1 rounded cursor-pointer transition-colors ${trendDays === d ? "bg-parchment-dark text-ink font-semibold shadow-sm" : "text-ink-muted"}`}
                    onClick={() => setTrendDays(d)}
                  >近{d}日</span>
                ))}
              </div>
              <span className="text-[10px] text-ink-light ml-auto">点击行查看当日比赛 · 正路池=正路命中 · 模糊池/不入池=双选命中 · 冷门池·强=强冷门方向优选 · 冷门池·警=警示冷门参考命中</span>
            </div>
            <div className="overflow-x-auto">
              <table className="min-w-full text-xs font-body">
                <thead>
                  <tr className="bg-parchment-dark/60 text-ink-muted">
                    <th className="text-left font-semibold px-4 py-2 whitespace-nowrap">日期</th>
                    <th className="text-left font-semibold px-4 py-2 whitespace-nowrap">正路池</th>
                    <th className="text-left font-semibold px-4 py-2 whitespace-nowrap">模糊池</th>
                    <th className="text-left font-semibold px-4 py-2 whitespace-nowrap" title="强冷门：让球方∉二选，方向优选命中">冷门池·强</th>
                    <th className="text-left font-semibold px-4 py-2 whitespace-nowrap" title="警示冷门：平局隐含≥0.28，冷门参考方向命中">冷门池·警</th>
                    <th className="text-left font-semibold px-4 py-2 whitespace-nowrap">不入池</th>
                  </tr>
                </thead>
                <tbody>
                  {[...data.trend].reverse().map((t) => (
                    <tr
                      key={t.date}
                      onClick={() => loadDay(t.date)}
                      className={`border-t border-highlight cursor-pointer transition-colors ${
                        selDay === t.date ? "bg-moss/5" : "hover:bg-highlight/60"
                      }`}
                    >
                      <td className="px-4 py-2 font-mono text-[11px] text-ink-muted whitespace-nowrap">
                        {t.date.slice(5)}
                        {selDay === t.date && <span className="ml-1.5 text-[10px] text-moss font-bold">已选</span>}
                      </td>
                      <td className="px-4 py-2"><DailyCell hit={t.favorite.hit} n={t.favorite.n} /></td>
                      <td className="px-4 py-2"><DailyCell hit={t.ambiguous.both_hit} n={t.ambiguous.n} /></td>
                      <td className="px-4 py-2"><DailyCell hit={t.upset.strong_hit} n={t.upset.strong_n} /></td>
                      <td className="px-4 py-2"><DailyCell hit={t.upset.warn_hit} n={t.upset.warn_n} /></td>
                      <td className="px-4 py-2"><DailyCell hit={t.unpooled.both_hit} n={t.unpooled.n} /></td>
                    </tr>
                  ))}
                  {data.trend.length === 0 && (
                    <tr><td colSpan={6} className="px-4 py-8 text-center text-ink-light">暂无每日数据</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>

          {/* 选中日期的当日比赛明细 */}
          {selDay && (
            <div className="space-y-4">
              <div className="flex items-center gap-3 px-4 py-3 bg-parchment-dark rounded-lg border border-border-dark flex-wrap">
                <span className="flex items-center gap-2 text-xs text-moss font-semibold">
                  <span className="w-2 h-2 rounded-full bg-moss" />
                  当日明细 · {selDay}
                </span>
                <span className="text-xs text-ink-muted">{dayLoading ? "加载中…" : `${dayRows.length} 场`}</span>
                <button onClick={() => setSelDay(null)}
                  className="ml-auto px-2.5 py-1 text-[11px] text-ink-muted border border-border rounded hover:border-rust hover:text-rust transition-colors">收起 ×</button>
              </div>
              {dayLoading ? (
                <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-3">
                  {Array.from({ length: 3 }).map((_, i) => <SkeletonCard key={i} />)}
                </div>
              ) : dayRows.length === 0 ? (
                <div className="bg-white rounded-md border border-border px-4 py-8 text-center text-xs text-ink-light">该日暂无预测数据</div>
              ) : (
                <>
                  <PoolSection title="正路池" rows={dayByPool.favorite} tint="bg-moss/10 text-moss border border-moss/30" onlyPreferred />
                  <PoolSection title="模糊池" rows={dayByPool.ambiguous} tint="bg-amber/10 text-amber border border-amber/30" />
                  <PoolSection title="冷门池 · 强冷门博方向 / 警示冷门参考" rows={dayByPool.upset} tint="bg-rust/10 text-rust border border-rust/30" onlyCold />
                  <PoolSection title="不入池" rows={dayByPool.unpooled} tint="bg-parchment-light text-ink-muted border border-border" />
                </>
              )}
            </div>
          )}
        </div>
      ) : (
        <>
      {/* 每池累计命中率卡片 */}
      <div className="flex gap-3 flex-wrap">
        {fav && poolCard(fav, "border-moss shadow-sm bg-moss/[0.03]", `正路命中 ${fav.hit}/${fav.n} · 双选 ${fav.both_hit}/${fav.n}`)}
        {amb && poolCard(amb, "border-amber shadow-sm bg-amber/[0.03]", `双选命中 ${amb.both_hit}/${amb.n} · 无优选`, "both")}
        {ups && poolCard(ups, "border-rust shadow-sm bg-rust/[0.03]", `强冷门 ${ups.strong_hit}/${ups.strong_n} · 警示冷门 ${ups.warn_n}场/参考命中${ups.warn_hit} · 双选 ${ups.both_hit}/${ups.n}`)}
        {unpooled && poolCard(unpooled, "border-border", `双选命中 ${unpooled.both_hit}/${unpooled.n} · 不选`, "both")}
      </div>

      {/* 当日分池明细 */}
      <PoolSection title="正路池 · 高置信跟正路" rows={todayFav} tint="bg-moss/10 text-moss border border-moss/30" onlyPreferred />
      <PoolSection title="模糊池 · 仅双选展示" rows={todayAmb} tint="bg-amber/10 text-amber border border-amber/30" />
      <PoolSection title="冷门池 · 强冷门博方向 / 警示冷门参考" rows={todayUps} tint="bg-rust/10 text-rust border border-rust/30" onlyCold />
      <PoolSection title="不入池 · 信号不明" rows={todayUn} tint="bg-parchment-light text-ink-muted border border-border" />

      {/* 近14天 7日滚动命中率 */}
      <div className="bg-white rounded-md border border-border overflow-hidden">
        <div className="px-4 py-3 border-b border-highlight bg-parchment-light/60 flex items-center gap-3 flex-wrap">
          <span className="text-xs font-bold text-ink-light uppercase tracking-wide">近 14 天 · 7 日滚动命中率</span>
          <span className="text-[10px] text-ink-light">正路池=正路命中 · 模糊池=双选命中 · 冷门池=冷门方向命中（强优选+警参考，绿≥50%，红{"<"}50%）</span>
        </div>
        <div className="overflow-x-auto">
          <table className="min-w-full text-xs font-body">
            <thead>
              <tr className="bg-parchment-dark/60 text-ink-muted">
                <th className="text-left font-semibold px-4 py-2 whitespace-nowrap">日期</th>
                <th className="text-left font-semibold px-4 py-2 whitespace-nowrap">正路池 · 滚动</th>
                <th className="text-left font-semibold px-4 py-2 whitespace-nowrap">模糊池 · 双选</th>
                <th className="text-left font-semibold px-4 py-2 whitespace-nowrap">冷门池 · 方向</th>
              </tr>
            </thead>
            <tbody>
              {favTrend.map((f, i) => {
                const a = ambTrend[i];
                const u = upsTrend[i];
                return (
                  <tr key={f.date} className="border-t border-highlight hover:bg-highlight/60 transition-colors">
                    <td className="px-4 py-2 font-mono text-[11px] text-ink-muted whitespace-nowrap">{f.date.slice(5)}</td>
                    <td className="px-4 py-2"><PoolBar rate={f.rate} n={f.n} /></td>
                    <td className="px-4 py-2"><PoolBar rate={a.rate} n={a.n} /></td>
                    <td className="px-4 py-2"><PoolBar rate={u.rate} n={u.n} /></td>
                  </tr>
                );
              })}
              {favTrend.length === 0 && (
                <tr><td colSpan={4} className="px-4 py-8 text-center text-ink-light">暂无趋势数据</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
        </>
      )}
    </div>
  );
}

function DailyCell({ hit, n }: { hit: number; n: number }) {
  if (n === 0) return <span className="text-[11px] text-ink-light">-</span>;
  const rate = hit / n;
  const good = rate >= 0.5;
  return (
    <span className={`font-mono text-[11px] font-semibold ${good ? "text-moss" : "text-rust"}`}>
      {hit}/{n} = {(rate * 100).toFixed(0)}%
    </span>
  );
}

function PoolBar({ rate, n }: { rate: number | null; n: number }) {
  const pct = rate == null ? 0 : Math.round(rate * 100);
  const good = rate != null && rate >= 0.5;
  return (
    <div className="flex items-center gap-2 min-w-[220px]">
      <div className="flex-1 h-2 bg-parchment-light rounded overflow-hidden">
        <div className={`h-full ${good ? "bg-moss" : "bg-rust"} transition-all`} style={{ width: `${pct}%` }} />
      </div>
      <span className="font-mono w-20 text-right text-[10px] text-ink-muted shrink-0">
        {rate == null ? "-" : `${pct}% (${n})`}
      </span>
    </div>
  );
}
