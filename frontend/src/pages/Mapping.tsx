import { useState, useEffect, useCallback, useRef } from "react";
import { getMappingStats, getLeagueMappings, getTeamMappings, confirmMapping, addAlias, batchMatch } from "../api/client";

interface AliasItem {
  name: string;
  source: string;
  is_primary: boolean;
}

interface MappingRow {
  id: number;
  name_zh: string;
  name_en: string;
  sportmonks_id: number | null;
  aliases: AliasItem[];
  status: string;
  league_name?: string;
  short_en?: string;
  review_reason?: string;
}

interface BatchResult {
  id: number;
  name_zh: string;
  name_en: string;
  status: "matched" | "unmatched" | "conflict";
  sm_id: number | null;
  sm_name: string | null;
  sm_short: string | null;
  search_term: string | null;
  note?: string;
}

export default function Mapping() {
  const [tab, setTab] = useState<"league" | "team">("team");
  const [stats, setStats] = useState({ total: 0, auto_matched: 0, pending: 0 });
  const [leagues, setLeagues] = useState<MappingRow[]>([]);
  const [teams, setTeams] = useState<MappingRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<"all" | "pending" | "confirmed">("all");
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // ── 编辑状态 ──
  const [editingRow, setEditingRow] = useState<number | null>(null);
  const [editAlias, setEditAlias] = useState("");
  const [editNameZh, setEditNameZh] = useState("");
  const [editSmId, setEditSmId] = useState("");
  const [saving, setSaving] = useState(false);

  // ── 批量匹配状态 ──
  const [batchMatching, setBatchMatching] = useState(false);
  const [batchResults, setBatchResults] = useState<BatchResult[] | null>(null);

  const fetchData = useCallback((search?: string) => {
    setLoading(true);
    const q = search ?? searchQuery;

    // 始终拉取全部数据（不带 status 筛选），在前端做筛选和计数
    Promise.all([
      getMappingStats(tab),
      tab === "league" ? getLeagueMappings(q || undefined) : getTeamMappings(undefined, q || undefined),
    ]).then(([st, d]) => {
      // 将 stats API 返回的全局计数用于顶部统计卡片
      setStats(st.data);
      if (tab === "league") setLeagues(d.data);
      else setTeams(d.data);
    }).finally(() => setLoading(false));
  }, [tab, searchQuery]);

  useEffect(() => { fetchData(); }, [fetchData, tab]);

  const handleSearchChange = (value: string) => {
    setSearchQuery(value);
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => { fetchData(value); }, 300);
  };

  const startEdit = (row: MappingRow) => {
    setEditingRow(row.id);
    setEditAlias("");
    setEditNameZh(row.name_zh || "");
    setEditSmId(row.sportmonks_id ? String(row.sportmonks_id) : "");
  };

  const cancelEdit = () => {
    setEditingRow(null);
    setEditAlias("");
    setEditNameZh("");
    setEditSmId("");
  };

  const saveEdit = async (row: MappingRow) => {
    const trimAlias = editAlias.trim();
    const trimNameZh = editNameZh.trim();
    const trimSmId = editSmId.trim();
    if (!trimAlias && !trimNameZh && !trimSmId) { cancelEdit(); return; }
    setSaving(true);
    try {
      if (trimAlias) await addAlias({ type: tab, id: row.id, alias_name: trimAlias, source: "manual" });
      const payload: Record<string, unknown> = { type: tab, id: row.id };
      if (trimNameZh) payload.name_zh = trimNameZh;
      if (trimSmId) payload.sportmonks_id = Number(trimSmId);
      await confirmMapping(payload as { type: string; id: number; name_zh?: string; sportmonks_id?: number });
      cancelEdit();
      fetchData();
    } catch (err) { console.error("保存失败:", err); alert("保存失败"); }
    finally { setSaving(false); }
  };

  const quickConfirm = async (row: MappingRow) => {
    if (!window.confirm(`确定要将「${row.name_zh || row.name_en}」标记为已确认吗？此操作将移除待确认状态。`)) return;
    setSaving(true);
    try { await confirmMapping({ type: tab, id: row.id }); fetchData(); }
    catch (err) { console.error("确认失败:", err); }
    finally { setSaving(false); }
  };

  // ── 批量匹配 ──
  const handleBatchMatch = async () => {
    setBatchMatching(true);
    setBatchResults(null);
    try {
      const res = await batchMatch();
      setBatchResults(res.data.results);
    } catch (err) { console.error("批量匹配失败:", err); alert("批量匹配失败，请检查SportMonks API"); }
    finally { setBatchMatching(false); }
  };

  const allRows: MappingRow[] = tab === "league" ? leagues : teams;

  // 前端状态筛选
  const rows = statusFilter === "all"
    ? allRows
    : allRows.filter(r => statusFilter === "pending" ? r.status === "pending" : r.status === "confirmed");

  // 从实际数据中推导筛选标签的计数（不用 stats API，确保一致）
  const dataPending = allRows.filter(r => r.status === "pending").length;
  const dataConfirmed = allRows.filter(r => r.status === "confirmed").length;
  const matchedInBatch = batchResults?.filter(r => r.status === "matched").length ?? 0;
  const conflictInBatch = batchResults?.filter(r => r.status === "conflict").length ?? 0;
  const unmatchedInBatch = batchResults?.filter(r => r.status === "unmatched").length ?? 0;

  return (
    <div className="bg-parchment">
      {/* Header */}
      <div className="flex items-center justify-between px-5 py-3 bg-parchment-dark border-b border-border-dark">
        <span className="font-heading text-base font-bold tracking-wider">名称映射管理</span>
        <div className="flex items-center gap-3">
          <span className="font-body text-xs text-ink-muted">赛季 2025-2026</span>
          {tab === "team" && dataPending > 0 && (
            <button
              onClick={handleBatchMatch}
              disabled={batchMatching}
              className="font-body text-[11px] text-white bg-amber hover:bg-amber/90 border border-amber rounded px-3 py-1 transition-colors disabled:opacity-50"
            >
              {batchMatching ? "匹配中..." : `批量匹配 (${dataPending})`}
            </button>
          )}
          <button
            onClick={() => fetchData()}
            disabled={loading}
            className="font-body text-[11px] text-ink-muted hover:text-moss border border-border rounded px-2.5 py-1 transition-colors disabled:opacity-50"
          >
            {loading ? "刷新中..." : "刷新"}
          </button>
        </div>
      </div>

      {/* Tab */}
      <div className="px-5 py-2.5 flex gap-0.5 font-body text-xs">
        <span className={`px-4 py-1.5 rounded cursor-pointer ${tab === "league" ? "bg-white shadow-sm font-semibold text-ink" : "text-ink-muted"}`}
              onClick={() => setTab("league")}>联赛映射</span>
        <span className={`px-4 py-1.5 rounded cursor-pointer ${tab === "league" ? "text-ink-muted" : "bg-white shadow-sm font-semibold text-ink"}`}
              onClick={() => setTab("team")}>球队映射</span>
      </div>

      {/* Search */}
      <div className="px-5 mb-3">
        <div className="relative">
          <input type="text" value={searchQuery} onChange={(e) => handleSearchChange(e.target.value)}
            placeholder={tab === "league" ? "搜索联赛名、英文名或别名..." : "搜索球队名、英文名、别名或所属联赛..."}
            className="w-full px-3 py-2 pl-8 font-body text-xs border border-border rounded-md bg-white focus:outline-none focus:border-amber/50 focus:ring-1 focus:ring-amber/30 transition-all placeholder:text-ink-light"
          />
          <svg className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-ink-light" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
          </svg>
          {searchQuery && <button onClick={() => handleSearchChange("")} className="absolute right-2.5 top-1/2 -translate-y-1/2 text-ink-light hover:text-ink text-xs">✕</button>}
        </div>
      </div>

      {/* Stats */}
      <div className="flex gap-3 px-5 mb-3">
        {[
          { label: "总计", value: stats.total, color: "text-ink" },
          { label: "已确认", value: stats.auto_matched, color: "text-moss" },
          { label: "待确认", value: stats.pending, color: "text-amber" },
        ].map((item, i) => (
          <div key={i} className="flex-1 bg-white rounded-md border border-border p-3 text-center font-body">
            <div className="text-[11px] text-ink-light">{item.label}</div>
            <div className={`text-xl font-bold mt-1 ${item.color}`}>{item.value}</div>
          </div>
        ))}
      </div>

      {/* Status Filter */}
      <div className="flex items-center gap-2 px-5 mb-3">
        <span className="font-body text-[11px] text-ink-muted">状态:</span>
        {[
          { key: "all", label: "全部", count: allRows.length },
          { key: "pending", label: "待确认", count: dataPending, color: "text-amber" },
          { key: "confirmed", label: "已确认", count: dataConfirmed, color: "text-moss" },
        ].map((f) => (
          <button
            key={f.key}
            onClick={() => setStatusFilter(f.key as "all" | "pending" | "confirmed")}
            className={`font-body text-[11px] px-3 py-1 rounded transition-colors ${
              statusFilter === f.key
                ? "bg-white shadow-sm font-semibold text-ink border border-border"
                : "text-ink-muted hover:text-ink"
            }`}
          >
            {f.label} <span className={f.color || "text-ink-light"}>({f.count})</span>
          </button>
        ))}
      </div>

      {/* Batch Result Modal */}
      {batchResults && (
        <div className="px-5 mb-3">
          <div className="bg-white rounded-md border border-amber/30 p-4">
            <div className="flex items-center justify-between mb-3">
              <div className="flex items-center gap-3 font-body text-xs">
                <span className="font-bold">批量匹配结果</span>
                <span className="text-moss">✓ {matchedInBatch} 匹配</span>
                {conflictInBatch > 0 && <span className="text-rust">⚠ {conflictInBatch} 冲突</span>}
                <span className="text-ink-light">✗ {unmatchedInBatch} 未匹配</span>
              </div>
              <div className="flex gap-2">
                <button onClick={() => { setBatchResults(null); fetchData(); }}
                  className="text-[11px] bg-moss text-white rounded px-3 py-1 hover:bg-moss/90">完成刷新</button>
                <button onClick={() => setBatchResults(null)}
                  className="text-[11px] text-ink-muted border rounded px-3 py-1">关闭</button>
              </div>
            </div>
            <div className="max-h-80 overflow-y-auto font-body text-[11px]">
              <table className="w-full">
                <thead>
                  <tr className="text-ink-muted border-b border-highlight">
                    <th className="text-left p-1.5 w-20">状态</th>
                    <th className="text-left p-1.5">球队</th>
                    <th className="text-left p-1.5">匹配结果</th>
                    <th className="text-left p-1.5 w-24">搜索词</th>
                  </tr>
                </thead>
                <tbody>
                  {batchResults.map((r) => (
                    <tr key={r.id} className={`border-b border-highlight ${r.status === "matched" ? "bg-hot-bg/30" : r.status === "conflict" ? "bg-cold-bg/30" : ""}`}>
                      <td className="p-1.5">
                        {r.status === "matched" && <span className="text-moss font-semibold">✓ 已匹配</span>}
                        {r.status === "conflict" && <span className="text-rust font-semibold">⚠ 冲突</span>}
                        {r.status === "unmatched" && <span className="text-ink-light">✗ 未匹配</span>}
                      </td>
                      <td className="p-1.5">
                        <span className="font-semibold">{r.name_zh || "-"}</span>
                        <span className="text-ink-light ml-1">{r.name_en}</span>
                      </td>
                      <td className="p-1.5">
                        {r.sm_id && <span>SM id={r.sm_id} <span className="text-moss">{r.sm_name}</span> {r.sm_short && <span className="text-ink-light">[{r.sm_short}]</span>}</span>}
                        {r.note && <div className="text-ink-muted text-xs">{r.note}</div>}
                      </td>
                      <td className="p-1.5 text-ink-light">{r.search_term || "-"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      {/* Table */}
      <div className="px-5 pb-5">
        {loading ? (
          <div className="bg-white rounded-md border border-border overflow-hidden">
            {[1,2,3,4,5,6].map(i => (
              <div key={i} className="flex items-center gap-3 px-2 py-3 border-b border-highlight last:border-0 animate-pulse">
                <div className="w-8 h-3 bg-border rounded" />
                <div className="w-20 h-3 bg-border rounded" />
                <div className="flex-1 h-3 bg-border rounded" />
                <div className="w-32 h-3 bg-border rounded" />
                <div className="w-12 h-3 bg-border rounded" />
                <div className="w-20 h-3 bg-border rounded" />
              </div>
            ))}
          </div>
        ) : rows.length === 0 ? (
          searchQuery || statusFilter !== "all" ? (
            <div className="bg-white rounded-md border border-border p-6 text-center text-ink-muted font-body text-xs">
              {searchQuery && <span>未找到匹配「{searchQuery}」的{tab === "league" ? "联赛" : "球队"}<br/></span>}
              {statusFilter !== "all" && <span>当前筛选：{statusFilter === "pending" ? "待确认" : "已确认"}，无匹配结果</span>}
            </div>
          ) : (
            <div className="bg-white rounded-md border border-border p-6 text-center text-ink-muted font-body text-xs">
              暂无{tab === "league" ? "联赛" : "球队"}映射数据
            </div>
          )
        ) : (
          <div className="bg-white rounded-md border border-border overflow-hidden font-body text-[11px]">
            <div className="flex bg-parchment-light border-b border-border font-semibold text-[11px] text-ink-muted">
              <span className="p-2 w-16">ID</span>
              <span className="p-2 w-28">{tab === "league" ? "中文名" : "联赛"}</span>
              <span className="p-2 flex-1">{tab === "league" ? "英文名" : "中文名 / 英文名"}</span>
              <span className="p-2 w-40">别名</span>
              <span className="p-2 w-16 text-center">状态</span>
              <span className="p-2 w-36 text-center">操作</span>
            </div>
            {rows.map((row) => {
              const isEditing = editingRow === row.id;
              return (
                <div key={row.id} className={`flex items-start border-b border-highlight last:border-0 ${row.status === "pending" ? "bg-amber/5" : ""}`}>
                  <span className="p-2 w-16 text-ink-light pt-2.5">{tab === "league" ? row.id : row.sportmonks_id || row.id}</span>
                  <span className="p-2 w-28 text-ink pt-2.5">{tab === "league" ? row.name_zh : (row.league_name || "-")}</span>
                  <span className="p-2 flex-1 pt-2.5">
                    {tab === "league" ? (
                      <span className="text-moss">{row.name_en}</span>
                    ) : (
                      <div>
                        <span><span className="font-semibold text-ink">{row.name_zh || "-"}</span><span className="text-ink-light ml-2">{row.name_en}</span></span>
                        {row.status === "pending" && row.review_reason && !isEditing && <div className="text-xs text-amber mt-0.5">{row.review_reason}</div>}
                      </div>
                    )}
                  </span>
                  <span className="p-2 w-40 pt-2.5">
                    <div className="flex flex-wrap gap-1">
                      {row.aliases.slice(0, 3).map((a, i) => (
                        <span key={i} className={`px-1.5 py-0.5 rounded-sm text-xs ${a.is_primary ? "bg-hot-bg text-moss" : "bg-highlight text-ink-muted"}`}>
                          {a.name.length > 12 ? a.name.slice(0, 12) + ".." : a.name}
                        </span>
                      ))}
                      {row.aliases.length > 3 && <span className="text-ink-light text-xs">+{row.aliases.length - 3}</span>}
                    </div>
                  </span>
                  <span className="p-2 w-16 text-center pt-2.5">
                    {row.status === "confirmed" ? (
                      <span className="bg-hot-bg text-moss px-1.5 py-0.5 rounded-sm text-xs">已确认</span>
                    ) : (
                      <span className="bg-cold-bg text-amber px-1.5 py-0.5 rounded-sm text-xs">待确认</span>
                    )}
                  </span>
                  <span className="p-2 w-36 text-center">
                    {row.status === "pending" && !isEditing && (
                      <div className="flex gap-1 justify-center">
                        <button onClick={() => startEdit(row)} className="text-[11px] text-moss hover:text-white hover:bg-moss border border-moss rounded px-2 py-0.5 transition-colors">编辑</button>
                      <button onClick={() => quickConfirm(row)} disabled={saving} className="text-[11px] text-ink-muted hover:text-ink border border-border rounded px-2 py-0.5 transition-colors disabled:opacity-50" title="直接标记为已确认">确认</button>
                      </div>
                    )}
                    {isEditing && (
                      <div className="flex flex-col gap-1.5 py-1">
                        {tab === "team" && (<>
                          <input type="text" value={editNameZh} onChange={(e) => setEditNameZh(e.target.value)} placeholder="球队中文名" className="w-full px-2.5 py-1.5 text-xs border border-border rounded focus:outline-none focus:border-amber/50" />
                          <input type="text" value={editSmId} onChange={(e) => setEditSmId(e.target.value)} placeholder="SportMonks ID" className="w-full px-2.5 py-1.5 text-xs border border-border rounded focus:outline-none focus:border-amber/50" />
                        </>)}
                        <input type="text" value={editAlias} onChange={(e) => setEditAlias(e.target.value)} placeholder="输入别名..." className="w-full px-2.5 py-1.5 text-xs border border-amber/50 rounded focus:outline-none focus:border-amber bg-amber/5" autoFocus
                          onKeyDown={(e) => { if (e.key === "Enter") saveEdit(row); if (e.key === "Escape") cancelEdit(); }} />
                        <div className="flex gap-1 justify-center">
                          <button onClick={() => saveEdit(row)} disabled={saving} className="text-xs bg-moss text-white rounded px-2 py-0.5 hover:bg-moss/90 disabled:opacity-50">{saving ? "保存中" : "保存"}</button>
                        <button onClick={cancelEdit} className="text-xs text-ink-muted border border-border rounded px-2 py-0.5 hover:text-ink">取消</button>
                        </div>
                      </div>
                    )}
                  </span>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
