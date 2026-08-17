import { useState, useEffect } from "react";
import { useParams, Link } from "react-router-dom";
import { getMatchDetail, getOddsHistory, getH2H, getPrediction, getMatchTeamComparison } from "../api/client";
import OddsTrendChart from "../components/OddsTrendChart";
import ErrorState from "../components/ErrorState";

/* ======== SVG 雷达图组件 ======== */
const RADAR_LABELS = ["进攻", "防守", "控球", "xG", "状态", "阵容"];
const RADAR_KEYS = ["attack", "defense", "possession", "xG", "form", "squad"];

function RadarChart({ homeData, awayData, homeName, awayName }: {
  homeData: Record<string, number> | null;
  awayData: Record<string, number> | null;
  homeName: string;
  awayName: string;
}) {
  const cx = 110, cy = 110, r = 82;
  const levels = 5;
  const sides = RADAR_KEYS.length;
  const angleStep = (2 * Math.PI) / sides;

  const getPoint = (i: number, value: number) => {
    const angle = angleStep * i - Math.PI / 2;
    const dist = (value / 100) * r;
    return {
      x: cx + dist * Math.cos(angle),
      y: cy + dist * Math.sin(angle),
    };
  };

  const makePolygon = (data: Record<string, number>) =>
    RADAR_KEYS.map((k, i) => {
      const p = getPoint(i, data[k] || 50);
      return `${p.x},${p.y}`;
    }).join(" ");

  const truncName = (name: string, maxLen = 6) =>
    name.length > maxLen ? name.slice(0, maxLen) + "…" : name;

  const homeColor = "#3b82f6";  // 蓝色 - 主队
  const awayColor = "#ef4444";  // 红色 - 客队

  return (
    <svg viewBox="0 0 220 260" className="w-full h-full max-w-[300px] mx-auto">
      {/* 背景网格 */}
      {Array.from({ length: levels }, (_, level) => {
        const scale = (level + 1) / levels;
        const pts = RADAR_KEYS.map((_, i) => {
          const p = getPoint(i, scale * 100);
          return `${p.x},${p.y}`;
        }).join(" ");
        return (
          <polygon
            key={level}
            points={pts}
            fill="none"
            stroke="#e2d9c8"
            strokeWidth="0.5"
          />
        );
      })}
      {/* 轴线 */}
      {RADAR_KEYS.map((_, i) => {
        const p = getPoint(i, 100);
        return (
          <line key={i} x1={cx} y1={cy} x2={p.x} y2={p.y} stroke="#e2d9c8" strokeWidth="0.5" />
        );
      })}
      {/* 客队（红色）—— 先画客队让主队在上层 */}
      {awayData && (
        <polygon
          points={makePolygon(awayData)}
          fill="rgba(239,68,68,0.12)"
          stroke={awayColor}
          strokeWidth="1.8"
        />
      )}
      {/* 主队（蓝色） */}
      {homeData && (
        <polygon
          points={makePolygon(homeData)}
          fill="rgba(59,130,246,0.12)"
          stroke={homeColor}
          strokeWidth="1.8"
        />
      )}
      {/* 标签 */}
      {RADAR_LABELS.map((label, i) => {
        const p = getPoint(i, 108);
        return (
          <text
            key={i}
            x={p.x}
            y={p.y}
            textAnchor="middle"
            dominantBaseline="middle"
            fill="#6b5e4a"
            fontSize="11"
            fontFamily="system-ui"
          >
            {label}
          </text>
        );
      })}
      {/* 图例 —— 分行显示避免重叠 */}
      <rect x={20} y={234} width={12} height={12} fill={homeColor} rx={2} />
      <text x={37} y={244} fill="#3d3628" fontSize="12" fontFamily="system-ui">{truncName(homeName)}</text>
      <rect x={125} y={234} width={12} height={12} fill={awayColor} rx={2} />
      <text x={142} y={244} fill="#3d3628" fontSize="12" fontFamily="system-ui">{truncName(awayName)}</text>
    </svg>
  );
}

/* ======== 近期状态卡片 ======== */
function RecentFormCard({ data, teamName }: { data: any; teamName: string }) {
  const matches = data?.recent_matches || [];
  const stats = data?.stats || {};
  const wins = stats?.wins || 0;
  const draws = stats?.draws || 0;
  const losses = stats?.losses || 0;
  const total = wins + draws + losses;
  const homeStats = stats?.home || {};
  const awayStats = stats?.away || {};

  const colorMap: Record<string, string> = { W: "#2d5a3b", D: "#8b7e6a", L: "#c44b3c" };

  const renderScore = (m: any) => {
    if (m.score == null || m.score === "") return "?:?";
    if (m.is_home == null) return m.score;
    const parts = m.score.split(":");
    const homeScore = m.is_home ? parts[0] : parts[1];
    const awayScore = m.is_home ? parts[1] : parts[0];
    return `${homeScore}:${awayScore}`;
  };

  const renderMatchupEl = (m: any) => {
    if (m.is_home == null) return <span>vs {m.opponent || "?"}</span>;
    const homeName = m.is_home ? teamName : (m.opponent || "?");
    const awayName = m.is_home ? (m.opponent || "?") : teamName;

    const resultStyle =
      m.result === "W" ? "text-red-600 font-bold" :
      m.result === "D" ? "text-ink font-bold" :
      m.result === "L" ? "text-blue-600 font-bold" : "";

    // 只高亮当前查询的球队
    return (
      <>
        <span className={m.is_home ? resultStyle : ""}>{homeName}</span>
        <span className="text-ink-muted"> VS </span>
        <span className={m.is_home ? "" : resultStyle}>{awayName}</span>
      </>
    );
  };

  const formatDate = (dateStr: string) => {
    if (!dateStr || dateStr.length < 10) return dateStr?.slice(5) || "";
    return dateStr.slice(2, 10); // "YY-MM-DD"
  };

  const hasHomeAwayData = matches.some((m: any) => m.is_home != null);

  return (
    <div>
      <div className="text-[11px] font-semibold text-ink mb-1.5 truncate">{teamName}</div>
      <div className="text-xs text-ink-muted mb-1">
        近{total}战 <span className="text-moss font-semibold">{wins}胜</span>{" "}
        <span className="text-ink-light">{draws}平</span>{" "}
        <span className="text-rust">{losses}负</span>
      </div>
      {hasHomeAwayData && (
        <div className="text-[11px] text-ink-muted mb-2 flex gap-4">
          <span>
            主场{homeStats.played || 0}场{" "}
            <span className="text-moss font-semibold">{homeStats.wins || 0}胜</span>{" "}
            <span className="text-ink-light">{homeStats.draws || 0}平</span>{" "}
            <span className="text-rust">{homeStats.losses || 0}负</span>
          </span>
          <span>
            客场{awayStats.played || 0}场{" "}
            <span className="text-moss font-semibold">{awayStats.wins || 0}胜</span>{" "}
            <span className="text-ink-light">{awayStats.draws || 0}平</span>{" "}
            <span className="text-rust">{awayStats.losses || 0}负</span>
          </span>
        </div>
      )}
      {matches.length > 0 ? (
        <div className="font-body text-xs space-y-0.5">
          {matches.map((m: any, i: number) => (
            <div key={i} className="flex items-center gap-1.5 py-0.5 border-b border-highlight last:border-0 hover:bg-highlight transition-colors">
              <span
                className="w-4 h-4 rounded-full flex items-center justify-center text-[11px] font-bold text-white shrink-0"
                style={{ backgroundColor: colorMap[m.result] || "#d4c9b5" }}
              >
                {m.result}
              </span>
              <span className="text-ink-light w-16 shrink-0 font-mono text-[11px]">{formatDate(m.date)}</span>
              <span className="text-ink-muted truncate flex-1">{renderMatchupEl(m)}</span>
              <span className="font-semibold shrink-0">{renderScore(m)}</span>
            </div>
          ))}
        </div>
      ) : (
        <div className="font-body text-xs text-ink-light">暂无数据</div>
      )}
    </div>
  );
}

/* ======== 模型推理逻辑面板 ======== */
function ModelReasoningPanel({ modelA, modelB, jointAnalysis, actualScore, resultSpf, resultHcp, resultGoals }: {
  modelA: any;
  modelB: any;
  jointAnalysis: string;
  actualScore?: string;
  resultSpf?: number;
  resultHcp?: number;
  resultGoals?: number;
}) {
  return (
    <div className="mt-3 bg-white rounded-md border border-border p-4">
      <div className="flex items-center gap-2 mb-3">
        <span className="text-sm font-bold">模型推理逻辑</span>
        <span className="font-body text-[11px] text-ink-light">逻辑支点 & 佐证因素</span>
        {actualScore && (
          <span className="ml-auto font-body text-xs">
            实际比分 <span className="font-bold text-moss">{actualScore}</span>
            {" "}
            {resultSpf === 1 && <span className="text-moss font-bold ml-1">胜平负命中</span>}
            {resultSpf === -1 && <span className="text-rust font-bold ml-1">胜平负未命中</span>}
            {resultHcp === 1 && <span className="text-moss font-bold ml-1">让球命中</span>}
            {resultGoals === 1 && <span className="text-moss font-bold ml-1">进球命中</span>}
          </span>
        )}
      </div>

      {/* 联合分析 */}
      <div className="p-2.5 bg-parchment-light rounded border border-border mb-3 font-body text-xs leading-relaxed">
        <span className="font-bold text-ink">A+B 联合分析：</span>
        <span className="text-ink-muted">{jointAnalysis}</span>
      </div>

      <div className="flex gap-3">
        {/* 模型A 板块 */}
        <div className="flex-1 border border-highlight rounded p-3">
          <div className="flex items-center gap-2 mb-2">
            <span className="bg-moss text-white text-[10px] px-1.5 py-0.5 rounded-sm font-body">模型 A</span>
            <span className="text-xs font-bold">{modelA.method}</span>
          </div>

          {/* 预测方向 */}
          <div className="flex items-center gap-2 mb-2.5">
            <span className="text-[11px] text-ink-muted">预测方向：</span>
            <span className={`text-sm font-bold ${
              modelA.pred_direction === "主胜" ? "text-moss" :
              modelA.pred_direction === "客胜" ? "text-rust" : "text-sand"
            }`}>
              {modelA.pred_direction} {modelA.pred_prob}%
            </span>
          </div>

          {/* 推理文本 */}
          <div className="text-[11px] text-ink-muted leading-relaxed mb-2.5 bg-parchment-light rounded p-2">
            {modelA.reasoning}
          </div>

          {/* 核心支撑因素 */}
          {modelA.top_features?.length > 0 && (
            <div className="mb-2">
              <div className="text-[10px] font-semibold text-ink-muted uppercase mb-1">核心支撑因素</div>
              <div className="space-y-0.5">
                {modelA.top_features.map((f: any, i: number) => (
                  <div key={i} className="flex items-center gap-1.5 text-[11px]">
                    <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${
                      f.impact.includes("利好") ? "bg-moss" : "bg-sand"
                    }`} />
                    <span className="text-ink truncate">{f.feature}</span>
                    <span className="text-ink-light font-mono">{f.value}</span>
                    <span className={`text-[10px] ${
                      f.impact.includes("利好") ? "text-moss" : "text-ink-muted"
                    }`}>{f.impact}</span>
                    {f.importance && (
                      <span className="text-ink-light text-[10px] ml-auto">{f.importance}%</span>
                    )}
                  </div>
                ))}
              </div>

          </div>
          )}

          {/* 其他参考因素 */}
          {modelA.other_features?.length > 0 && (
            <div>
              <div className="text-[10px] font-semibold text-ink-muted uppercase mb-1">其他参考因素</div>
              <div className="flex flex-wrap gap-1">
                {modelA.other_features.map((f: any, i: number) => (
                  <span key={i} className="text-[10px] bg-parchment-light px-1.5 py-0.5 rounded text-ink-muted">
                    {f.feature}: {f.value}
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* 模型B 板块 */}
        <div className="flex-1 border border-highlight rounded p-3">
          <div className="flex items-center gap-2 mb-2">
            <span className="bg-rust text-white text-[10px] px-1.5 py-0.5 rounded-sm font-body">模型 B</span>
            <span className="text-xs font-bold">{modelB.method}</span>
          </div>

          {/* 预测方向 */}
          <div className="flex items-center gap-2 mb-2.5">
            <span className="text-[11px] text-ink-muted">预期总进球：</span>
            <span className="text-sm font-bold text-ink">λ={modelB.lambda} 球</span>
            <span className={`text-[11px] font-semibold ${
              modelB.over_2_5_prob > 50 ? "text-rust" : "text-blue-600"
            }`}>
              大2.5概率 {modelB.over_2_5_prob}%
            </span>
          </div>

          {/* 推理文本 */}
          <div className="text-[11px] text-ink-muted leading-relaxed mb-2.5 bg-parchment-light rounded p-2">
            {modelB.reasoning}
          </div>

          {/* 推升进球因素 */}
          {modelB.push_factors?.length > 0 && (
            <div className="mb-2">
              <div className="text-[10px] font-semibold text-rust uppercase mb-1">推升进球因素 ↑</div>
              <div className="space-y-0.5">
                {modelB.push_factors.map((f: any, i: number) => (
                  <div key={i} className="flex items-center gap-1.5 text-[11px]">
                    <span className="w-1.5 h-1.5 rounded-full bg-rust shrink-0" />
                    <span className="text-ink truncate">{f.feature}</span>
                    <span className="text-ink-light font-mono">{f.value}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* 压制进球因素 */}
          {modelB.pull_factors?.length > 0 && (
            <div>
              <div className="text-[10px] font-semibold text-blue-600 uppercase mb-1">压制进球因素 ↓</div>
              <div className="space-y-0.5">
                {modelB.pull_factors.map((f: any, i: number) => (
                  <div key={i} className="flex items-center gap-1.5 text-[11px]">
                    <span className="w-1.5 h-1.5 rounded-full bg-blue-400 shrink-0" />
                    <span className="text-ink truncate">{f.feature}</span>
                    <span className="text-ink-light font-mono">{f.value}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* 进球分布摘要 */}
          {modelB.goal_distribution && (
            <div className="mt-2 pt-2 border-t border-highlight">
              <div className="text-[10px] font-semibold text-ink-muted mb-1">进球分布</div>
              <div className="flex text-[10px] font-mono text-ink-light">
                {modelB.goal_distribution.map((p: number, i: number) => (
                  <span key={i} className="flex-1 text-center">
                    {i}球 <span className="text-ink font-semibold">{p}%</span>
                  </span>
                ))}
                <span className="flex-1 text-center">
                  4+球 <span className="text-ink font-semibold">{modelB.goal_distribution[4] || 0}%</span>
                </span>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export default function MatchDetail() {
  const { id } = useParams<{ id: string }>();
  const [match, setMatch] = useState<any>(null);
  const [prediction, setPrediction] = useState<any>(null);
  const [oddsData, setOddsData] = useState<Record<string, any[]>>({});
  const [oddsOpening, setOddsOpening] = useState<Record<string, any>>({});
  const [matchHcpLine, setMatchHcpLine] = useState<number | null>(null);
  const [h2h, setH2h] = useState<any[]>([]);
  const [teamComp, setTeamComp] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!id) return;
    Promise.all([
      getMatchDetail(Number(id)),
      getPrediction(Number(id)),
      getOddsHistory(Number(id)),
      getH2H(Number(id)),
      getMatchTeamComparison(Number(id)),
    ]).then(([m, p, o, h, tc]) => {
      setMatch(m.data);
      setPrediction(p.data);
      setOddsData(o.data || {});
      setOddsOpening(o.opening || {});
      setMatchHcpLine(o.match_handicap_line ?? null);
      setH2h(h.data || []);
      setTeamComp(tc.data || null);
      setLoading(false);
    }).catch((err) => { setError(err?.message || "加载失败"); setLoading(false); });
  }, [id]);

  if (loading) return (
    <div className="bg-parchment">
      <div className="flex items-center gap-3 px-5 py-2.5 bg-parchment-dark border-b border-border-dark">
        <div className="w-24 h-4 bg-border rounded animate-pulse" />
      </div>
      <div className="px-6 py-6 text-center border-b border-border">
        <div className="w-48 h-3 bg-border rounded animate-pulse mx-auto mb-3" />
        <div className="flex items-center justify-center gap-8">
          <div className="flex flex-col items-center gap-2">
            <div className="w-12 h-12 bg-border rounded-full animate-pulse" />
            <div className="w-16 h-4 bg-border rounded animate-pulse" />
            <div className="w-8 h-3 bg-border rounded animate-pulse" />
          </div>
          <div className="w-8 h-6 bg-border rounded animate-pulse" />
          <div className="flex flex-col items-center gap-2">
            <div className="w-12 h-12 bg-border rounded-full animate-pulse" />
            <div className="w-16 h-4 bg-border rounded animate-pulse" />
            <div className="w-8 h-3 bg-border rounded animate-pulse" />
          </div>
        </div>
      </div>
      <div className="flex">
        <div className="flex-[1.15] p-3.5 space-y-2.5">
          {[1,2,3].map(i => <div key={i} className="bg-white rounded-md border border-border p-3.5"><div className="w-full h-40 bg-border rounded animate-pulse" /></div>)}
        </div>
        <div className="flex-[0.85] p-3.5 space-y-2.5">
          {[1,2].map(i => <div key={i} className="bg-white rounded-md border border-border p-4"><div className="w-full h-28 bg-border rounded animate-pulse" /></div>)}
        </div>
      </div>
    </div>
  );
  if (error) return <ErrorState message={error} onRetry={() => { setError(null); setLoading(true); window.location.reload(); }} />;
  if (!match) return <ErrorState message="赛事数据异常" />;

  const pred = prediction || {};
  const isCold = pred.is_cold_match;

  return (
    <div className="bg-parchment text-ink">
      {/* 顶部栏 */}
      <div className="flex items-center gap-3 px-5 py-2.5 bg-parchment-dark border-b border-border-dark font-body text-xs text-ink-muted">
        <Link to="/" className="text-sm hover:text-moss">← 返回赛事列表</Link>
        <span className="ml-auto">模型 {pred.model_version || "v0.1.0"} · 更新于 {pred.created_at?.slice(11,16) || "--"}</span>
      </div>

      {/* 对阵概要 */}
      <div className="px-6 py-4 text-center border-b border-border">
        <div className="font-body text-ink-light tracking-wide">
          <div className="text-sm font-semibold">{match.league_name} · 第{match.round || "--"}轮</div>
          <div className="text-xs mt-0.5">{match.kickoff_time?.slice(0,16)} · {match.venue || "--"} · {match.match_num || match.jc_match_id}</div>
        </div>
        <div className="flex items-center justify-center gap-6 mt-2.5">
          <div className="text-center">
            {match.home_logo ? (
              <img src={match.home_logo} alt={match.home_team} className="w-12 h-12 object-contain rounded-full mx-auto mb-2 bg-white border-2 border-border" />
            ) : (
              <div className="w-12 h-12 bg-white border-2 border-border rounded-full mx-auto mb-2 flex items-center justify-center text-sm font-bold text-ink">
                {match.home_team?.charAt(0) || "?"}
              </div>
            )}
            <div className="text-lg font-bold">{match.home_team}</div>
            <div className="font-body text-xs text-ink-light">排名 {match.home_rank || "-"}</div>
          </div>
          <div className="font-body text-3xl font-heading font-bold text-ink">VS</div>
          <div className="text-center">
            {match.away_logo ? (
              <img src={match.away_logo} alt={match.away_team} className="w-12 h-12 object-contain rounded-full mx-auto mb-2 bg-white border-2 border-border" />
            ) : (
              <div className="w-12 h-12 bg-white border-2 border-border rounded-full mx-auto mb-2 flex items-center justify-center text-sm font-bold text-ink">
                {match.away_team?.charAt(0) || "?"}
              </div>
            )}
            <div className="text-lg font-bold">{match.away_team}</div>
            <div className="font-body text-xs text-ink-light">排名 {match.away_rank || "-"}</div>
          </div>
        </div>
      </div>

      {/* 主内容：左底座 + 右预测 */}
      <div className="flex border-b border-border">
        {/* 左：预测底座 */}
        <div className="flex-[1.15] p-3.5 border-r border-border">
          <div className="text-[11px] text-ink-light font-body uppercase tracking-wide mb-2.5">预测数据底座</div>

          {/* 球队战力雷达对比 */}
          <div className="bg-white rounded-md border border-border p-3.5 mb-2.5">
            <div className="text-[13px] font-bold mb-2">球队战力雷达对比</div>
            {teamComp ? (
              <RadarChart
                homeData={teamComp.home?.radar || null}
                awayData={teamComp.away?.radar || null}
                homeName={teamComp.home?.name || match.home_team}
                awayName={teamComp.away?.name || match.away_team}
              />
            ) : (
              <div className="h-40 bg-parchment-light rounded flex flex-col items-center justify-center text-ink-light font-body text-xs gap-2">
                <span>球队数据暂未同步</span>
                <Link to="/admin" className="text-moss hover:underline text-[11px]">前往系统管理 → 更新球队数据</Link>
              </div>
            )}
          </div>

          {/* 交锋 + 状态并排 */}
          <div className="flex gap-2.5 mb-2.5">
            <div className="flex-1 bg-white rounded-md border border-border p-3">
              <div className="text-xs font-bold mb-1.5">近期交锋</div>
              <div className="font-body text-xs">
                {h2h.slice(0, 4).map((h: any, i: number) => {
                  const homeWin = h.home_score > h.away_score;
                  const awayWin = h.away_score > h.home_score;
                  const isDraw = h.home_score === h.away_score;
                  return (
                  <div key={i} className="py-1 border-b border-highlight last:border-0 hover:bg-highlight transition-colors">
                    <div className="text-ink-light mb-0.5">{h.date?.slice(0, 7)}</div>
                    <div className="flex items-center">
                      <span className={`flex-1 truncate text-right ${homeWin ? "text-red-600 font-bold" : isDraw ? "text-ink font-bold" : ""}`}>
                        {h.home_team || "主"}
                      </span>
                      <span className="font-bold text-sm mx-3 min-w-[32px] text-center">{h.home_score}:{h.away_score}</span>
                      <span className={`flex-1 truncate text-left ${awayWin ? "text-red-600 font-bold" : ""}`}>
                        {h.away_team || "客"}
                      </span>
                    </div>
                  </div>
                );
                })}
                {h2h.length === 0 && (
                  <div className="text-center py-4">
                    <div className="text-[11px] text-ink-light mb-2">暂无交锋数据</div>
                    <Link to="/admin" className="text-[11px] text-moss hover:underline">前往系统管理 → 更新球队数据</Link>
                  </div>
                )}
              </div>
            </div>
            <div className="flex-1 bg-white rounded-md border border-border p-3">
              <div className="text-xs font-bold mb-2">近期状态</div>
              <div className="space-y-3">
                <RecentFormCard
                  data={teamComp?.home}
                  teamName={teamComp?.home?.name || match.home_team || "主队"}
                />
                <div className="border-t border-highlight" />
                <RecentFormCard
                  data={teamComp?.away}
                  teamName={teamComp?.away?.name || match.away_team || "客队"}
                />
              </div>
            </div>
          </div>

          {/* 赔率趋势图 */}
          <OddsTrendChart data={oddsData} opening={oddsOpening} matchHandicapLine={matchHcpLine} hasFixture={!!match?.sportmonks_fixture_id} />
        </div>

        {/* 右：预测输出（两个板块） */}
        <div className="flex-[0.85] p-3.5">
          <div className="text-[11px] text-ink-light font-body uppercase tracking-wide mb-2.5">模型预测输出</div>

          {/* 板块一：模型A */}
          <div className="bg-white rounded-md border border-border p-3.5 mb-2.5">
            <div className="flex items-center gap-2 mb-2.5">
              <span className="bg-moss text-white text-[11px] px-1.5 py-0.5 rounded-sm font-body">模型 A</span>
              <span className="text-[13px] font-bold">胜平负 & 让胜平负</span>
              <span className="font-body text-xs text-ink-light ml-auto">LightGBM 多任务</span>
            </div>

            {/* 胜平负 */}
            <div className="mb-2.5">
              <div className="text-[11px] font-semibold text-ink-muted mb-1.5">胜平负</div>
              <div className="flex gap-1.5 font-body">
                {(["home_prob","draw_prob","away_prob"] as const).map((k, i) => {
                  const labels = ["主胜","平局","客胜"];
                  const prob = pred[k] || 0.33;
                  const probs = [pred.home_prob || 0, pred.draw_prob || 0, pred.away_prob || 0];
                  const isMax = i === probs.indexOf(Math.max(...probs));
                  return (
                    <div key={k} className={`flex-1 text-center rounded-md p-2.5 ${isMax ? "bg-highlight border-2 border-moss" : "bg-parchment-light"}`}>
                      <div className="text-2xl font-bold" style={{color: isMax ? "#2d5a3b" : "#3d3628"}}>{(prob * 100).toFixed(1)}%</div>
                      <div className="text-[11px] text-ink-muted mt-0.5">{labels[i]}</div>
                    </div>
                  );
                })}
              </div>
              {/* 概率条 */}
              <div className="flex gap-1.5 mt-1.5">
                <div className="flex-1 h-1 bg-border rounded-sm overflow-hidden"><div className="h-full bg-moss" style={{width:`${(pred.home_prob||0)*100}%`}}/></div>
                <div className="flex-1 h-1 bg-border rounded-sm overflow-hidden"><div className="h-full bg-sand" style={{width:`${(pred.draw_prob||0)*100}%`}}/></div>
                <div className="flex-1 h-1 bg-border rounded-sm overflow-hidden"><div className="h-full bg-rust" style={{width:`${(pred.away_prob||0)*100}%`}}/></div>
              </div>
            </div>

            {/* 让球胜平负 */}
            <div className="border-t border-border border-dashed pt-2">
              <div className="text-[11px] font-semibold text-ink-muted mb-1.5">
                让球胜平负 <span className="text-xs text-ink-light font-normal">(-{match.handicap_line || 1}球)</span>
              </div>
              <div className="flex gap-1.5 font-body">
                {(["handicap_home_prob","handicap_draw_prob","handicap_away_prob"] as const).map((k, i) => {
                  const labels = ["让胜","让平","让负"];
                  const prob = pred[k] || 0.33;
                  return (
                    <div key={k} className="flex-1 text-center bg-parchment-light rounded-md py-2">
                      <div className="text-lg font-semibold text-ink">{(prob * 100).toFixed(1)}%</div>
                      <div className="text-xs text-ink-muted">{labels[i]}</div>
                    </div>
                  );
                })}
              </div>
            </div>

            {/* 冷门概率修正 */}
            {pred.cold_correction && (
              <div className="border-t border-border border-dashed pt-2">
                <div className="text-[11px] font-semibold text-ink-muted mb-1.5 flex items-center gap-1">
                  <span className="text-amber">&#x2194;</span> 概率修正
                  <span className="text-[10px] text-ink-light font-normal">(市场分歧融合)</span>
                </div>
                <div className="font-body text-[10px]">
                  <div className="grid grid-cols-3 gap-1 text-center">
                    <div className="bg-parchment-light rounded p-1">
                      <div className="text-ink-light mb-0.5">AI原始</div>
                      <div className="text-moss font-semibold">{((pred.cold_correction.model_original.home) * 100).toFixed(0)}%</div>
                      <div className="text-sand">{((pred.cold_correction.model_original.draw) * 100).toFixed(0)}%</div>
                      <div className="text-rust">{((pred.cold_correction.model_original.away) * 100).toFixed(0)}%</div>
                    </div>
                    <div className="bg-parchment-light rounded p-1">
                      <div className="text-ink-light mb-0.5">市场隐含</div>
                      <div className="text-moss font-semibold">{((pred.cold_correction.market_implied.home) * 100).toFixed(0)}%</div>
                      <div className="text-sand">{((pred.cold_correction.market_implied.draw) * 100).toFixed(0)}%</div>
                      <div className="text-rust">{((pred.cold_correction.market_implied.away) * 100).toFixed(0)}%</div>
                    </div>
                    <div className="bg-amber/5 rounded p-1 border border-amber/20">
                      <div className="text-amber mb-0.5">修正结果</div>
                      <div className="text-moss font-semibold">{((pred.home_prob || 0) * 100).toFixed(0)}%</div>
                      <div className="text-sand">{((pred.draw_prob || 0) * 100).toFixed(0)}%</div>
                      <div className="text-rust">{((pred.away_prob || 0) * 100).toFixed(0)}%</div>
                    </div>
                  </div>
                  <div className="text-center mt-1 text-ink-light">
                    {((pred.cold_correction.blend_ratio.model) * 100).toFixed(0)}% AI + {((pred.cold_correction.blend_ratio.market) * 100).toFixed(0)}% 市场 加权融合
                  </div>
                </div>
              </div>
            )}
          </div>

          {/* 板块二：模型B */}
          <div className="bg-white rounded-md border border-border p-3.5">
            <div className="flex items-center gap-2 mb-2.5">
              <span className="bg-rust text-white text-[11px] px-1.5 py-0.5 rounded-sm font-body">模型 B</span>
              <span className="text-[13px] font-bold">进球数 & 比分推导</span>
              <span className="font-body text-xs text-ink-light ml-auto">Poisson 回归</span>
            </div>

            {/* 进球数分布 */}
            <div className="mb-2.5">
              <div className="text-[11px] font-semibold text-ink-muted mb-1.5">总进球数分布</div>
              <div className="font-body text-xs">
                {(pred.goal_distribution || [0.06,0.17,0.24,0.22,0.31]).map((p: number, i: number) => {
                  const labels = ["0球","1球","2球","3球","4+球"];
                  return (
                    <div key={i} className="flex items-center gap-1 mb-0.5">
                      <span className="w-7 text-ink-muted">{labels[i]}</span>
                      <div className="flex-1 h-1.5 bg-border rounded-sm overflow-hidden">
                        <div className="h-full bg-sand rounded-sm" style={{width:`${p*100}%`}}/>
                      </div>
                      <span className="w-5 text-right text-ink-light">{(p*100).toFixed(0)}%</span>
                    </div>
                  );
                })}
              </div>
              <div className="mt-1.5 font-body text-[11px] text-ink-muted text-center">
                预期总进球 <span className="font-bold text-ink text-base">{pred.expected_goals?.toFixed(1) || "2.5"}</span> 球 · 大 2.5 概率 <span className="font-semibold text-moss">{(pred.over_2_5_prob * 100 || 50).toFixed(0)}%</span>
              </div>
            </div>

            {/* 比分 Top5 */}
            <div className="border-t border-border border-dashed pt-2">
              <div className="text-[11px] font-semibold text-ink-muted mb-1.5">参考比分 Top5 <span className="text-[11px] text-ink-light font-normal">A+B联合推导</span></div>
              <div className="font-body">
                {(pred.score_top5_json || []).map((s: any, i: number) => (
                  <div key={i} className={`flex items-center justify-between py-1.5 px-2 ${i===0?"bg-highlight rounded":""}`}>
                    <span className={`font-bold ${i===0?"text-moss":"text-ink"} text-xs`}>{s.score}</span>
                    <span className={`text-xs ${i===0?"text-moss font-semibold":""}`}>{(s.prob*100).toFixed(1)}%</span>
                    <span className="text-[11px] text-ink-light">{s.result==="home"?"主胜":s.result==="draw"?"平局":"客胜"}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* 底部报告摘要 */}
      <div className="p-4 bg-parchment-light border-t border-border">
        <div className="flex items-center justify-between mb-2.5">
          <span className="text-sm font-bold">本场预测报告摘要</span>
          <span className="font-body text-xs text-ink-light">基于模型 {pred.model_version || "v0.1.0"}</span>
        </div>
        <div className="bg-white rounded-md border border-border p-4 text-[13px] leading-relaxed">
          <div className="p-2.5 bg-highlight rounded border-l-2 border-moss mb-2.5">
            <span className="font-bold text-moss">综合预测：</span>{pred.summary_text || "数据正在分析中..."}
          </div>
          {isCold && (
            <div className="p-2 bg-cold-bg rounded border border-amber/30 font-body text-[11px] flex items-center gap-1.5 text-amber">
              <span className="text-sm">⚠</span> 赔率离散度偏高，本场标记为冷门预警。建议结合临场信息判断。
            </div>
          )}
          {/* V4.12: 数据质量警告 */}
          {pred.risk_warning && Array.isArray(pred.risk_warning) && pred.risk_warning.length > 0 && (
            <div className="mt-2 p-2 bg-amber/5 rounded border border-amber/20 font-body text-[11px]">
              <div className="text-amber font-medium mb-1 flex items-center gap-1">
                <span>⚠️</span> 数据质量提示
              </div>
              <ul className="list-disc list-inside space-y-0.5 text-ink-light">
                {pred.risk_warning.map((w: string, i: number) => (
                  <li key={i}>{w}</li>
                ))}
              </ul>
            </div>
          )}
        </div>

        {/* 模型推理逻辑 */}
        {pred.key_factors?.model_a && (
          <ModelReasoningPanel
            modelA={pred.key_factors.model_a}
            modelB={pred.key_factors.model_b}
            jointAnalysis={pred.key_factors.joint_analysis}
            actualScore={pred.actual_score}
            resultSpf={pred.result_spf}
            resultHcp={pred.result_hcp}
            resultGoals={pred.result_goals}
          />
        )}

        <div className="flex justify-end gap-2 mt-2.5 font-body">
          <button className="bg-white border border-border-dark text-ink px-3.5 py-1.5 rounded text-[11px]">复制报告</button>
          <button className="bg-moss border border-moss text-white px-3.5 py-1.5 rounded text-[11px] disabled:opacity-50 disabled:cursor-not-allowed">导出本场 PDF</button>
        </div>
      </div>
    </div>
  );
}
