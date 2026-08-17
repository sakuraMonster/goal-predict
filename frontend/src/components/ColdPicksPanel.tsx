import { useState, useEffect, useCallback } from "react";
import { getColdPicks, getColdPicksHistory } from "../api/client";
import type { ColdPick, ColdPicksResponse, ColdPickHistoryResponse } from "../api/client";

/** 市场热门方向中文：0=主胜 1=平 2=客胜 */
function favDirText(dir: number): string {
  if (dir === 0) return "主胜";
  if (dir === 2) return "客胜";
  return "平";
}

/** 搏冷方向文本：非热门方向（含平局），如 [2,1] → "客胜 / 平" */
function coldDirsText(dirs?: number[]): string {
  if (!dirs || dirs.length === 0) return "-";
  return dirs.map(favDirText).join(" / ");
}

/** 搏冷方向带概率：如 "平 32% / 客胜 28%" */
function coldDirsWithProb(p: ColdPick): string {
  if (!p.cold_dirs || p.cold_dirs.length === 0) return "-";
  const ip = p.implied_probs;
  return p.cold_dirs
    .map((d) => {
      const prob = d === 0 ? ip?.home : d === 1 ? ip?.draw : ip?.away;
      return `${favDirText(d)}${prob != null ? ` ${(prob * 100).toFixed(0)}%` : ""}`;
    })
    .join(" / ");
}

/**
 * 今日冷门优选面板（主预测界面顶部）
 * 规则：市场热门方向（赛前快照收盘隐含概率 argmax）与同联赛排名优势方向相反
 * → 市场高估 → 爆冷风险高。|排名差| 越大信号越强。
 */
export function ColdPicksPanel() {
  const [collapsed, setCollapsed] = useState(false);
  const [picks, setPicks] = useState<ColdPicksResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<ColdPick | null>(null);

  const fetchPicks = useCallback(() => {
    setLoading(true);
    getColdPicks({ top_n: 3, secondary: 3 })
      .then((res) => setPicks(res))
      .catch(() => setPicks(null))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { fetchPicks(); }, [fetchPicks]);

  const items = picks?.data || [];
  const secondary = picks?.secondary || [];
  const primary = items.slice(0, 3);

  return (
    <div className="bg-white rounded-lg border border-border p-4">
      <div className="flex items-center gap-2 mb-3">
        <span
          className="flex items-center gap-1.5 cursor-pointer select-none"
          onClick={() => setCollapsed(!collapsed)}
          title={collapsed ? "展开" : "收起"}
        >
          <span className="font-heading text-sm font-bold text-ink">今日冷门优选</span>
          <span className={`text-[10px] text-ink-light transition-transform ${collapsed ? "" : "rotate-180"}`}>▾</span>
        </span>
        {picks && (
          <span className="text-[10px] text-ink-muted">
            冲突候选 {picks.total_conflicts} 场 · 含次选 {items.length + secondary.length} 场
          </span>
        )}
        <span className="ml-auto text-[10px] text-ink-light">市场热门 vs 联赛排名冲突 · 爆冷风险提示</span>
      </div>

      {!collapsed && (
        <>
          {loading ? (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
              {[1, 2, 3].map((i) => (
                <div key={i} className="h-28 bg-parchment-light rounded-md animate-pulse" />
              ))}
            </div>
          ) : primary.length === 0 && secondary.length === 0 ? (
            <div className="text-xs text-ink-light py-6 text-center">
              今日暂无冷门优选（无冲突场次，或缺赛前赔率 / 联赛排名数据）
            </div>
          ) : (
            <>
              {/* 优选卡片（前 3 名） */}
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                {primary.map((p, idx) => (
                  <ColdPickCard key={p.match_id} pick={p} rank={idx + 1} onOpen={() => setSelected(p)} />
                ))}
              </div>

              {/* 次选列表 */}
              {secondary.length > 0 && (
                <div className="mt-3 border-t border-border pt-3">
                  <div className="flex items-center gap-2 mb-2">
                    <span className="text-[10px] font-bold text-ink-muted">次选</span>
                    <span className="text-[10px] text-ink-light">
                      第 {items.length + 1}~{items.length + secondary.length} 名 · 冲突程度较低
                    </span>
                  </div>
                  <div className="space-y-1.5">
                    {secondary.map((p, idx) => (
                      <div
                        key={p.match_id}
                        className="flex items-center gap-2.5 px-3 py-2 rounded-md border border-border bg-white cursor-pointer hover:bg-parchment-light transition-colors"
                        onClick={() => setSelected(p)}
                      >
                        <span className="w-5 h-5 rounded-full bg-parchment-dark text-ink-muted text-[11px] font-bold flex items-center justify-center shrink-0">
                          {items.length + idx + 1}
                        </span>
                        {p.match_num && <span className="text-[10px] font-bold text-ink-muted bg-parchment-dark px-1.5 py-0.5 rounded shrink-0">{p.match_num}</span>}
                        <span className="text-[10px] text-ink-light truncate shrink-0 max-w-[100px]">{p.league_name}</span>
                        <span className="flex-1 text-xs font-semibold text-ink truncate">
                          {p.home_team} <span className="text-ink-light font-normal text-[10px]">vs</span> {p.away_team}
                        </span>
                        <span className="text-[11px] text-ink-muted shrink-0">{p.kickoff_time.slice(11, 16)}</span>
                        <span className="shrink-0 text-[10px] text-ink-light">
                          搏冷<strong className="text-rust font-bold">{coldDirsWithProb(p)}</strong>
                        </span>
                        <span className="shrink-0 text-xs font-bold font-heading text-rust w-12 text-right">{p.score.toFixed(1)}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </>
          )}
        </>
      )}

      {selected && <ColdPickDetailDrawer pick={selected} onClose={() => setSelected(null)} />}
    </div>
  );
}

function ColdPickCard({ pick, rank, onOpen }: { pick: ColdPick; rank: number; onOpen: () => void }) {
  return (
    <div
      className={`rounded-md border p-3 cursor-pointer transition-shadow hover:shadow-md ${rank === 1 ? "border-rust bg-parchment-light" : "border-border bg-white"}`}
      onClick={onOpen}
      title="点击查看优选逻辑"
    >
      <div className="flex items-center gap-1.5 mb-2">
        <span className="w-5 h-5 rounded-full bg-rust text-white text-[11px] font-bold flex items-center justify-center shrink-0">{rank}</span>
        {pick.match_num && <span className="text-[10px] font-bold text-ink-muted bg-parchment-dark px-1.5 py-0.5 rounded shrink-0">{pick.match_num}</span>}
        <span className="text-[10px] text-ink-light truncate flex-1">{pick.league_name}</span>
        <span className="text-[11px] text-ink-muted shrink-0">{pick.kickoff_time.slice(11, 16)}</span>
      </div>

      <div className="flex items-center justify-between gap-1.5 mb-2">
        <span className="font-heading text-sm font-bold text-center flex-1 truncate leading-tight">{pick.home_team}</span>
        <span className="text-[10px] text-ink-light shrink-0 font-semibold">VS</span>
        <span className="font-heading text-sm font-bold text-center flex-1 truncate leading-tight">{pick.away_team}</span>
      </div>

      <div className="flex items-center gap-2 mb-2">
        <div className="flex-1 rounded-md bg-parchment-dark px-2 py-1.5">
          <div className="text-[9px] text-ink-light">市场热门</div>
          <div className="text-xs font-bold text-ink leading-tight">
            {favDirText(pick.fav_dir)} {pick.fav_prob > 0 ? `${(pick.fav_prob * 100).toFixed(0)}%` : ""}
          </div>
        </div>
        <div className="flex-1 rounded-md bg-rust/10 border border-rust/30 px-2 py-1.5">
          <div className="text-[9px] text-ink-light">搏冷方向</div>
          <div className="text-xs font-bold text-rust leading-tight">{coldDirsWithProb(pick)}</div>
        </div>
        <div className="ml-auto text-right">
          <div className="text-[10px] text-ink-light">冲突强度</div>
          <div className="text-lg font-bold font-heading text-rust leading-none">{pick.score.toFixed(1)}</div>
        </div>
      </div>

      {pick.signals?.[0] && (
        <div className="text-[10px] text-ink-muted leading-relaxed">{pick.signals[0]}</div>
      )}
    </div>
  );
}

// ==================== 冷门优选逻辑详情抽屉 ====================
function ColdPickDetailDrawer({ pick, onClose }: { pick: ColdPick; onClose: () => void }) {
  const ip = pick.implied_probs || { home: 0, draw: 0, away: 0 };
  const favLabel = favDirText(pick.fav_dir);
  const favProb = pick.fav_prob > 0 ? `${(pick.fav_prob * 100).toFixed(0)}%` : "-";
  const coldLabel = coldDirsText(pick.cold_dirs);
  const coldProb = coldDirsWithProb(pick);
  const rankLeader = pick.rank_gap > 0 ? "客队" : "主队";
  const baseScore = pick.rank_gap;
  const hasBonus = pick.fav_prob >= 0.55;

  return (
    <div className="fixed inset-0 bg-ink/30 z-50 flex justify-end" onClick={onClose}>
      <div className="w-[560px] max-w-[100vw] h-full bg-white shadow-xl overflow-y-auto animate-[slideIn_0.25s_ease]" onClick={e => e.stopPropagation()}>
        {/* 头部 */}
        <div className="sticky top-0 bg-white border-b border-border px-5 py-4 flex items-center justify-between z-10">
          <span className="font-heading text-base font-bold">冷门优选 · 优选逻辑</span>
          <button onClick={onClose} className="w-9 h-9 rounded-full border border-border flex items-center justify-center text-ink-muted hover:bg-parchment transition-colors">&times;</button>
        </div>

        <div>
          {/* 对阵概要 */}
          <div className="px-5 py-4 border-b border-border">
            <div className="flex items-center gap-2 mb-2.5">
              {pick.match_num && <span className="text-[10px] font-bold text-ink-muted bg-parchment-dark px-1.5 py-0.5 rounded">{pick.match_num}</span>}
              <span className="text-[10px] text-ink-light bg-parchment-light px-2 py-0.5 rounded">{pick.league_name}</span>
              <span className="text-[11px] text-ink-muted">{pick.kickoff_time.slice(0, 16).replace("T", " ")}</span>
            </div>
            <div className="flex items-center justify-between gap-2">
              <span className="font-heading text-base font-bold flex-1 text-center">{pick.home_team}</span>
              <span className="text-[11px] text-ink-light shrink-0 font-semibold">VS</span>
              <span className="font-heading text-base font-bold flex-1 text-center">{pick.away_team}</span>
            </div>
          </div>

          {/* 概率快照 */}
          <div className="px-5 py-4 border-b border-border">
            <div className="text-xs font-bold text-ink-light uppercase tracking-wide mb-3">市场隐含概率快照</div>
            <div className="flex gap-3">
              <div className={`flex-1 text-center p-3 rounded-md ${pick.fav_dir === 0 ? "bg-rust/10 border border-rust/30" : "bg-parchment-light"}`}>
                <div className="text-[10px] text-ink-light mb-1.5">主胜</div>
                <div className={`text-xl font-bold font-heading ${pick.fav_dir === 0 ? "text-rust" : "text-ink"}`}>
                  {(ip.home * 100).toFixed(0)}%
                </div>
                {pick.fav_dir === 0 && <div className="text-[10px] text-rust font-bold mt-0.5">市场热门</div>}
              </div>
              <div className={`flex-1 text-center p-3 rounded-md ${pick.fav_dir === 1 ? "bg-rust/10 border border-rust/30" : "bg-parchment-light"}`}>
                <div className="text-[10px] text-ink-light mb-1.5">平</div>
                <div className={`text-xl font-bold font-heading ${pick.fav_dir === 1 ? "text-rust" : "text-ink"}`}>
                  {(ip.draw * 100).toFixed(0)}%
                </div>
                {pick.fav_dir === 1 && <div className="text-[10px] text-rust font-bold mt-0.5">市场热门</div>}
              </div>
              <div className={`flex-1 text-center p-3 rounded-md ${pick.fav_dir === 2 ? "bg-rust/10 border border-rust/30" : "bg-parchment-light"}`}>
                <div className="text-[10px] text-ink-light mb-1.5">客胜</div>
                <div className={`text-xl font-bold font-heading ${pick.fav_dir === 2 ? "text-rust" : "text-ink"}`}>
                  {(ip.away * 100).toFixed(0)}%
                </div>
                {pick.fav_dir === 2 && <div className="text-[10px] text-rust font-bold mt-0.5">市场热门</div>}
              </div>
            </div>
          </div>

          {/* 优选逻辑 */}
          <div className="px-5 py-4 border-b border-border">
            <div className="text-xs font-bold text-ink-light uppercase tracking-wide mb-3">优选逻辑 · 冲突信号</div>

            {/* 信号 */}
            <div className="p-2.5 bg-parchment-light rounded-md mb-3">
              <div className="flex items-center gap-2 mb-1">
                <span className="text-[10px] font-bold text-rust">排名冲突</span>
                <span className="text-xs font-semibold text-ink">
                  {rankLeader}排名领先 {pick.rank_gap} 位，市场却看好{favLabel}
                </span>
              </div>
              <div className="flex items-center gap-2">
                <div className="flex-1 h-1.5 bg-parchment rounded overflow-hidden">
                  <div className="h-full bg-rust/70 rounded" style={{ width: `${Math.min(100, pick.rank_gap * 10)}%` }} />
                </div>
                <span className="text-[10px] text-ink-light shrink-0">
                  市场热门 {favLabel} {favProb} → 搏冷方向 {coldLabel}（{coldProb}）
                </span>
              </div>
            </div>

            {/* 实证依据 */}
            <div className="text-[11px] text-ink-muted leading-relaxed mb-2">实证依据（近30天样本内验证）：</div>
            <div className="grid grid-cols-3 gap-2 mb-3">
              {[
                { label: "冲突场次冷门率", value: "77.8%", note: "p=0.001" },
                { label: "排名差 ≥4", value: "88.2%", note: "信号更强" },
                { label: "排名差 ≥5", value: "90.9%", note: "信号最强" },
              ].map((s) => (
                <div key={s.label} className="text-center p-2.5 bg-parchment-light rounded-md">
                  <div className="text-[10px] text-ink-light mb-1">{s.label}</div>
                  <div className="text-lg font-bold font-heading text-rust">{s.value}</div>
                  <div className="text-[9px] text-ink-light mt-0.5">{s.note}</div>
                </div>
              ))}
            </div>
            <div className="text-[11px] text-ink-muted leading-relaxed">
              随机标签对照（shuffle 30 次）中真实信号处于 0% 分位，且按热门概率三档控制后仍然有效，
              判定为真实可消费信号而非小样本噪声。
            </div>
          </div>

          {/* 盘口资金异动（待验证叠加特征） */}
          <div className="px-5 py-4 border-b border-border">
            <div className="text-xs font-bold text-ink-light uppercase tracking-wide mb-3">盘口资金异动 · 待验证叠加特征</div>
            <div className="flex gap-3 mb-2">
              <div className="flex-1 text-center p-3 bg-parchment-light rounded-md">
                <div className="text-[10px] text-ink-light mb-1.5">开盘→收盘最大变动</div>
                <div className="text-xl font-bold font-heading text-ink">
                  {(pick.odds_delta_max * 100).toFixed(1)}%
                </div>
              </div>
              <div className="flex-1 text-center p-3 bg-parchment-light rounded-md">
                <div className="text-[10px] text-ink-light mb-1.5">相邻快照最大单步</div>
                <div className="text-xl font-bold font-heading text-ink">
                  {(pick.odds_step_max * 100).toFixed(1)}%
                </div>
              </div>
            </div>
            <div className="text-[11px] text-ink-muted leading-relaxed">
              当前仅记录不参与评分。探针显示概率异动 &gt;p90 场次冷门率 71.4%，但样本仅 14 场、p=0.163
              未过显著；待每日推荐积累结算样本（目标 ≥30 场）验证显著性后，作为叠加条件加入评分。
            </div>
          </div>

          {/* 评分公式 */}
          <div className="px-5 py-4 border-b border-border">
            <div className="text-xs font-bold text-ink-light uppercase tracking-wide mb-3">评分公式</div>
            <div className="bg-parchment-light rounded px-3 py-2.5 font-mono text-[11px] text-ink leading-relaxed break-all">
              score = |主队排名 - 客队排名|
              {hasBonus && <><br />&nbsp;&nbsp;+ 0.5（热门概率 ≥55%，市场更"确信"的高估加成）</>}
              <br />
              <span className="text-ink-muted">
                = {baseScore}{hasBonus ? " + 0.5" : ""} = <span className="text-rust font-bold">{pick.score.toFixed(1)}</span>
              </span>
            </div>
          </div>

          {/* 命中口径 */}
          <div className="px-5 py-4">
            <div className="text-xs font-bold text-ink-light uppercase tracking-wide mb-3">命中判定口径</div>
            <div className="text-[11px] text-ink-muted leading-relaxed">
              本场实际赛果落于搏冷方向（{coldLabel}）即爆冷命中——即市场热门（{favLabel} {favProb}）未打出。
              冷门构成参考：热门直接输球约占 52%，热门打平约占 48%，故搏冷方向同时覆盖平局与反向。
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

/** 冷门优选历史命中率（爆冷命中 = 实际赛果 ≠ 推荐时市场热门方向） */
export function ColdPicksHistoryPanel() {
  const [data, setData] = useState<ColdPickHistoryResponse | null>(null);
  const [loading, setLoading] = useState(true);

  const fetchHistory = useCallback(() => {
    setLoading(true);
    getColdPicksHistory({ days: 30 })
      .then((res) => setData(res))
      .catch(() => setData(null))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { fetchHistory(); }, [fetchHistory]);

  const summary = data?.summary;
  const days = data?.data || [];

  return (
    <div className="bg-white rounded-lg border border-border p-4">
      <div className="flex items-center gap-2 mb-3">
        <span className="font-heading text-sm font-bold text-ink">冷门优选命中率</span>
        {data && (
          <span className="text-[10px] text-ink-muted">
            近{data.days}天累计推荐 {summary?.total_picks ?? 0} 场
          </span>
        )}
        <span className="ml-auto text-[10px] text-ink-light">命中口径：实际赛果 ≠ 推荐时市场热门方向</span>
      </div>

      {loading ? (
        <div className="h-24 bg-parchment-light rounded animate-pulse" />
      ) : !data || days.length === 0 ? (
        <div className="text-xs text-ink-light py-6 text-center">
          暂无冷门优选历史记录（主界面生成推荐后自动记录）
        </div>
      ) : (
        <>
          {/* 累计命中率 */}
          <div className="flex items-center gap-6 mb-3 p-3 bg-parchment-light rounded-md">
            <div className="min-w-[120px]">
              <div className="text-[10px] text-ink-light">累计爆冷命中率</div>
              <div className="text-2xl font-bold font-heading text-rust">{summary!.accuracy.toFixed(1)}%</div>
            </div>
            <div className="text-xs text-ink-muted leading-relaxed">
              推荐 {summary!.total_picks} 场 · 爆冷 {summary!.hit} · 未爆冷 {summary!.miss}
              <br />
              <span className="text-ink-light">待结算 {summary!.total_picks - summary!.settled} 场</span>
            </div>
          </div>

          {/* 按日期列表 */}
          <div className="space-y-1.5">
            {days.map((d) => (
              <div key={d.date} className="flex items-center gap-3 px-3 py-2 border border-parchment rounded">
                <span className="text-xs font-semibold text-ink w-24 shrink-0">{d.date}</span>
                <div className="flex-1 h-2 bg-parchment rounded overflow-hidden">
                  <div
                    className={`h-full rounded ${d.accuracy >= 60 ? "bg-rust" : d.accuracy >= 45 ? "bg-amber" : "bg-moss"}`}
                    style={{ width: `${Math.max(d.accuracy, 2)}%` }}
                  />
                </div>
                <span className="text-xs text-ink-muted w-20 text-right shrink-0">
                  {d.hit}/{d.settled}中
                  {d.settled < d.total ? `（${d.total - d.settled}待）` : ""}
                </span>
                <span className="text-xs font-bold text-rust w-14 text-right shrink-0">
                  {d.settled > 0 ? `${d.accuracy.toFixed(0)}%` : "-"}
                </span>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
