import { useEffect, useState } from "react";
import { getValueAnalysisHistory, computeValueAnalysis, backfillValueAnalysisPrediction, ValueHistoryResponse, ValueHistoryDay, ValueHistoryMatch } from "../api/client";

const SIDE_ZH: Record<string, string> = { home: "主胜", draw: "平", away: "客胜" };

const VERDICT_BADGE: Record<string, string> = {
  有价值: "bg-emerald-100 text-emerald-800",
  中性: "bg-gray-100 text-gray-600",
  无价值: "bg-gray-100 text-gray-500",
  无基准: "bg-gray-100 text-gray-400",
};

const fmtPct = (p: number | null | undefined, digits = 1) =>
  p == null ? "—" : `${(p * 100).toFixed(digits)}%`;
const fmtPp = (pp: number | null | undefined) =>
  pp == null ? "—" : `${pp >= 0 ? "+" : ""}${(pp * 100).toFixed(1)}pp`;

function beijingToday(): string {
  const now = new Date();
  const bj = new Date(now.getTime() + (now.getTimezoneOffset() + 480) * 60000);
  return bj.toISOString().slice(0, 10);
}

function sideZh(s: string | null | undefined) {
  return (s && SIDE_ZH[s]) || s || "";
}

function HitMark({ hit }: { hit: boolean | null }) {
  if (hit === true) return <span className="text-emerald-600 font-semibold">✓ 中</span>;
  if (hit === false) return <span className="text-red-500 font-semibold">✗ 未中</span>;
  return <span className="text-gray-400">—</span>;
}

function MatchRow({ m }: { m: ValueHistoryMatch }) {
  return (
    <div className="flex items-center gap-3 text-xs py-1.5 border-b border-border/50 last:border-0">
      <span className="text-ink-muted shrink-0 w-16">{m.match_num ?? ""}</span>
      <span className="text-moss shrink-0 w-16 truncate">{m.league ?? ""}</span>
      <span className="text-ink font-medium truncate flex-1">
        {m.home} <span className="text-ink-muted">vs</span> {m.away}
      </span>
      <span className="text-ink-muted shrink-0 w-10 text-right">{m.kickoff ?? ""}</span>
      {m.verdict && (
        <span className={`shrink-0 px-1.5 py-0.5 rounded text-[11px] font-semibold ${VERDICT_BADGE[m.verdict] ?? "bg-gray-100 text-gray-500"}`}>
          {m.verdict}
        </span>
      )}
      {m.best_side ? (
        <span className="shrink-0 text-ink w-24 text-right">
          预测 {sideZh(m.best_side)}
          {m.best_ev != null && (
            <span className={m.best_ev >= 0 ? "text-emerald-600" : "text-red-500"}>
              {" "}({m.best_ev >= 0 ? "+" : ""}{(m.best_ev * 100).toFixed(1)}%)
            </span>
          )}
        </span>
      ) : (
        <span className="shrink-0 text-gray-400 w-24 text-right">无基准</span>
      )}
      {m.prediction ? (
        <span className="shrink-0 text-ink w-40 text-right whitespace-nowrap">
          提取 {m.prediction.label}
        </span>
      ) : (
        <span className="shrink-0 text-gray-400 w-40 text-right">—</span>
      )}
      <span className="shrink-0 w-12 text-right"><HitMark hit={m.prediction_hit} /></span>
      <span className="shrink-0 text-ink-muted w-16 text-right">实际 {sideZh(m.actual)}</span>
      <span className="shrink-0 w-14 text-right"><HitMark hit={m.hit} /></span>
    </div>
  );
}

function DayCard({ day }: { day: ValueHistoryDay }) {
  const [open, setOpen] = useState(false);
  const valueHitCount = day.value_n > 0;
  return (
    <div className="bg-white border border-border rounded-lg overflow-hidden">
      <button
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center justify-between gap-3 px-4 py-2.5 text-left hover:bg-highlight/30 transition-colors"
      >
        <div className="flex items-center gap-3 flex-wrap">
          <span className="text-sm font-semibold text-ink">{day.matchday}</span>
          <span className="text-xs text-ink-muted">总 {day.total} 场</span>
          <span className="text-xs text-ink-muted">
            有价值 {day.value_n} 场 · 中 {day.value_hit}
          </span>
          {valueHitCount && (
            <span className={`text-xs font-semibold ${(day.value_rate ?? 0) >= 0.5 ? "text-emerald-600" : "text-red-500"}`}>
              价值命中率 {fmtPct(day.value_rate)}
            </span>
          )}
          <span className="text-xs text-ink-muted">全方向命中率 {fmtPct(day.all_rate)}</span>
          {day.pred_n > 0 && (
            <span className={`text-xs font-semibold ${(day.pred_rate ?? 0) >= 0.5 ? "text-emerald-600" : "text-red-500"}`}>
              预测提取 {day.pred_n}场·中{day.pred_hit}
            </span>
          )}
        </div>
        <span className="text-xs text-ink-muted shrink-0">{open ? "▴" : "▾"}</span>
      </button>
      {open && (
        <div className="px-4 pb-3 border-t border-border">
          {day.matches.length === 0 ? (
            <p className="text-xs text-ink-muted py-2">该日无比赛。</p>
          ) : (
            day.matches.map((m, i) => <MatchRow key={`${m.match_num}-${i}`} m={m} />)
          )}
        </div>
      )}
    </div>
  );
}

export default function ValueHistory() {
  const [mode, setMode] = useState<"range" | "date">("range");
  const [days, setDays] = useState(7);
  const [date, setDate] = useState<string>(beijingToday);
  const [data, setData] = useState<ValueHistoryResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [computing, setComputing] = useState(false);
  const [backfilling, setBackfilling] = useState(false);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError(null);
    const params = mode === "date" ? { date } : { days };
    getValueAnalysisHistory(params)
      .then((d) => { if (alive) setData(d); })
      .catch((e) => { if (alive) setError(e?.message ?? "加载失败"); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [mode, days, date]);

  const handleCompute = async () => {
    setComputing(true);
    setError(null);
    try {
      const params = mode === "date" ? { date } : { days };
      await computeValueAnalysis(params);
      const d = await getValueAnalysisHistory(params);
      setData(d);
    } catch (e: any) {
      setError(e?.message ?? "补算失败");
    } finally {
      setComputing(false);
    }
  };

  const handleBackfill = async () => {
    setBackfilling(true);
    setError(null);
    try {
      await backfillValueAnalysisPrediction({ only_missing: false });
      const params = mode === "date" ? { date } : { days };
      const d = await getValueAnalysisHistory(params);
      setData(d);
    } catch (e: any) {
      setError(e?.message ?? "回填预测失败");
    } finally {
      setBackfilling(false);
    }
  };

  const s = data?.summary;
  const bias = s && s.value_rate != null && s.value_avg_fp != null ? s.value_rate - s.value_avg_fp : null;

  return (
    <div>
      <div className="flex items-center justify-between mb-4 flex-wrap gap-2">
        <div className="flex items-center gap-2 text-sm flex-wrap">
          <div className="flex gap-0.5 bg-white border border-border rounded p-0.5">
            <button
              onClick={() => setMode("range")}
              className={`px-3 py-1 rounded text-xs ${mode === "range" ? "bg-moss text-white" : "text-ink hover:bg-highlight"}`}
            >
              近 N 天
            </button>
            <button
              onClick={() => setMode("date")}
              className={`px-3 py-1 rounded text-xs ${mode === "date" ? "bg-moss text-white" : "text-ink hover:bg-highlight"}`}
            >
              指定日期
            </button>
          </div>
          {mode === "range" ? (
            [7, 30, 60, 90, 180].map((d) => (
              <button
                key={d}
                onClick={() => setDays(d)}
                className={`px-3 py-1 rounded text-xs border ${days === d ? "bg-moss text-white border-moss" : "bg-white text-ink border-border hover:bg-highlight"}`}
              >
                近 {d} 天
              </button>
            ))
          ) : (
            <input
              type="date"
              value={date}
              onChange={(e) => setDate(e.target.value)}
              className="border border-border rounded px-2 py-1 text-sm text-ink bg-white"
            />
          )}
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={handleCompute}
            disabled={computing}
            className="px-3 py-1 rounded text-xs font-medium bg-moss text-white disabled:opacity-50 hover:opacity-90"
          >
            {computing ? "补算中…" : "补算"}
          </button>
          <button
            onClick={handleBackfill}
            disabled={backfilling}
            className="px-3 py-1 rounded text-xs font-medium border border-moss text-moss disabled:opacity-50 hover:bg-moss/5"
          >
            {backfilling ? "回填中…" : "回填预测"}
          </button>
          {data && (
            <span className="text-xs text-ink-muted">
              {data.range[0]}{data.range[1] !== data.range[0] ? ` ~ ${data.range[1]}` : ""}
            </span>
          )}
        </div>
      </div>

      {loading && <p className="text-sm text-ink-muted">统计中…</p>}
      {error && <p className="text-sm text-red-600">{error}</p>}

      {s && (
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mb-5">
          <div className="bg-white border border-border rounded-lg px-4 py-3">
            <div className="text-xs text-ink-muted">有价值信号命中率</div>
            <div className="text-xl font-bold text-emerald-600 mt-1">{fmtPct(s.value_rate)}</div>
            <div className="text-xs text-ink-muted mt-0.5">{s.value_n} 个信号 · 中 {s.value_hit}</div>
          </div>
          <div className="bg-white border border-border rounded-lg px-4 py-3">
            <div className="text-xs text-ink-muted">有价值平均真实概率</div>
            <div className="text-xl font-bold text-ink mt-1">{fmtPct(s.value_avg_fp)}</div>
            <div className="text-xs text-ink-muted mt-0.5">校准偏差 {fmtPp(bias)}</div>
          </div>
          <div className="bg-white border border-border rounded-lg px-4 py-3">
            <div className="text-xs text-ink-muted">全方向命中率</div>
            <div className="text-xl font-bold text-ink mt-1">{fmtPct(s.all_rate)}</div>
            <div className="text-xs text-ink-muted mt-0.5">{s.total} 场有基准 · 中 {s.all_hit}</div>
          </div>
          <div className="bg-white border border-border rounded-lg px-4 py-3">
            <div className="text-xs text-ink-muted">大小球错价方向命中率</div>
            <div className={`text-xl font-bold mt-1 ${(s.ou_rate ?? 0) < 0.5 ? "text-red-500" : "text-emerald-600"}`}>{fmtPct(s.ou_rate)}</div>
            <div className="text-xs text-ink-muted mt-0.5">{s.ou_n} 个错价信号（仅方向参考）</div>
          </div>
          <div className="bg-white border border-border rounded-lg px-4 py-3">
            <div className="text-xs text-ink-muted">预测提取命中率</div>
            <div className={`text-xl font-bold mt-1 ${(s.pred_rate ?? 0) < 0.5 ? "text-red-500" : "text-emerald-600"}`}>{fmtPct(s.pred_rate)}</div>
            <div className="text-xs text-ink-muted mt-0.5">{s.pred_n} 个预测 · 中 {s.pred_hit}</div>
          </div>
        </div>
      )}

      {data && !loading && (
        <div className="space-y-2">
          {data.daily.length === 0 && <p className="text-sm text-ink-muted">{mode === "date" ? "该日期无已结算比赛。" : "该范围无已结算比赛。"}</p>}
          {data.daily.map((d) => <DayCard key={d.matchday} day={d} />)}
        </div>
      )}
    </div>
  );
}
