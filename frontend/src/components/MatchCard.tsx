import { Link } from "react-router-dom";
import type { ColdCorrection } from "../api/client";

interface MatchCardProps {
  id: number;
  league_name: string;
  kickoff_time: string;
  home_team: string;
  away_team: string;
  match_num?: string;
  home_prob: number;
  draw_prob: number;
  away_prob: number;
  expected_goals: number;
  goal_distribution?: number[];
  score_top5_json?: Array<{ score: string; prob: number; result: string }>;
  reference_score: string;
  is_cold_match: boolean;
  is_hot_match: boolean;
  confidence_level: string;
  cold_correction?: ColdCorrection | null;
  risk_warning?: string[];
}

export default function MatchCard({
  id, league_name, kickoff_time, home_team, away_team, match_num,
  home_prob, draw_prob, away_prob, expected_goals,
  goal_distribution, score_top5_json,
  reference_score, is_cold_match, is_hot_match, cold_correction, risk_warning,
}: MatchCardProps) {
  const time = new Date(kickoff_time).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
  const maxProb = Math.max(home_prob, draw_prob, away_prob);
  const hasCorrection = cold_correction && cold_correction.model_original;
  // 冷门方向判定
  const coldDirection = hasCorrection ? (() => {
    const orig = cold_correction.model_original;
    const market = cold_correction.market_implied;
    const origMax = orig.home >= orig.away ? 'home' : 'away';
    const marketMax = market.home >= market.away ? 'home' : 'away';
    if (origMax !== marketMax) return { type: 'reverse', label: origMax === 'home' ? 'AI看主→市场看客' : 'AI看客→市场看主' };
    // 同方向但概率差异大
    const origGap = Math.abs(orig.home - orig.away);
    const marketGap = Math.abs(market.home - market.away);
    if (marketGap > origGap * 1.5) return { type: 'amplify', label: origMax === 'home' ? '市场更看好主胜' : '市场更看好客胜' };
    return { type: 'mild', label: '模型与市场方向一致' };
  })() : null;

  // V4.12: 距离 λ 最近2个整数，小数<0.10向下取整、>0.90向上取整，6=6+
  const top2goals = (() => {
    if (expected_goals == null) return [];
    const SNAP_DOWN = 0.10;   // 小数低于此值向下取整（如 3.03→取[2,3]）
    const SNAP_UP   = 0.90;   // 小数高于此值向上取整（如 3.93→取[3,4]）
    const frac = expected_goals - Math.floor(expected_goals);
    let effective = expected_goals;
    if (frac < SNAP_DOWN) effective = Math.floor(expected_goals);
    else if (frac > SNAP_UP) effective = Math.ceil(expected_goals);
    const dists = [0,1,2,3,4,5,6].map(i => ({i, d: Math.abs(effective - i)})).sort((a,b) => a.d - b.d);
    return dists.slice(0, 2).map(d => d.i);
  })();

  // 最高概率的2个比分
  const topScores = (score_top5_json && score_top5_json.length > 0)
    ? score_top5_json.slice(0, 2)
    : [];

  return (
    <Link to={`/match/${id}`} className="block bg-white rounded-md border border-border p-3.5 hover:shadow-sm transition-shadow">
      <div className="text-xs text-ink-light tracking-wide font-body uppercase">
        {match_num ? <span className="text-ink-muted mr-1">{match_num}</span> : null}
        {league_name} · {time}
      </div>
      <div className="text-sm font-bold my-1 flex justify-between items-center text-ink">
        <span>{home_team}</span>
        <span className="text-xs text-ink-light font-body">vs</span>
        <span>{away_team}</span>
      </div>

      {/* 概率条（修正后用虚线样式） */}
      <div className="flex gap-1.5 my-2">
        <div className="flex-1 h-1 bg-border rounded-sm overflow-hidden">
          <div className={`h-full bg-moss rounded-sm ${hasCorrection ? "opacity-70" : ""}`}
            style={{ width: `${home_prob * 100}%` }} />
        </div>
        <div className="flex-1 h-1 bg-border rounded-sm overflow-hidden">
          <div className={`h-full bg-sand rounded-sm ${hasCorrection ? "opacity-70" : ""}`}
            style={{ width: `${draw_prob * 100}%` }} />
        </div>
        <div className="flex-1 h-1 bg-border rounded-sm overflow-hidden">
          <div className={`h-full bg-rust rounded-sm ${hasCorrection ? "opacity-70" : ""}`}
            style={{ width: `${away_prob * 100}%` }} />
        </div>
      </div>

      {/* 概率数值（修正后显示原文提示） */}
      <div className="flex justify-between font-body text-[11px] text-ink-muted">
        <span className={home_prob === maxProb ? "text-moss font-semibold" : ""}>
          主 {(home_prob * 100).toFixed(0)}%
          {hasCorrection && (
            <span className="ml-0.5 text-[10px] text-amber/70" title={`AI原始: ${(cold_correction.model_original.home * 100).toFixed(0)}% | ${cold_correction.reason}`}>
              ({(cold_correction.model_original.home * 100).toFixed(0)})
            </span>
          )}
        </span>
        <span>平 {(draw_prob * 100).toFixed(0)}%</span>
        <span>客 {(away_prob * 100).toFixed(0)}%</span>
      </div>

      {/* 标签行 */}
      <div className="mt-2 flex gap-1.5 font-body text-xs text-ink-muted flex-wrap">
        <span className="bg-parchment-light px-1.5 py-0.5 rounded-sm">
          γ = {(expected_goals ?? 0).toFixed(1)}
          {top2goals.length === 2 && (
            <span className="text-ink-muted/50 mx-1">|</span>
          )}
          {top2goals.length === 2 && (
            <span>
              {top2goals[0] >= 6 ? '6+' : top2goals[0]}球/{top2goals[1] >= 6 ? '6+' : top2goals[1]}球
            </span>
          )}
          {expected_goals != null && (expected_goals < 1.0 || expected_goals > 5.0) && (
            <span className="text-amber font-bold ml-0.5" title="进球数预期处于极端区间">⚠</span>
          )}
        </span>
        {topScores.length > 0 ? (
          <span className="bg-parchment-light px-1.5 py-0.5 rounded-sm">
            {topScores.map((s, i) => (
              <span key={s.score} className={i === 0 ? "font-semibold text-ink" : ""}>
                {i > 0 ? " " : ""}{s.score}
              </span>
            ))}
          </span>
        ) : (
          <span className="bg-parchment-light px-1.5 py-0.5 rounded-sm">{reference_score} 参考</span>
        )}
        {is_cold_match && hasCorrection && (
          <span
            className="bg-amber/8 text-amber text-[11px] px-2 py-0.5 rounded-sm cursor-help border border-amber/20"
            title={cold_correction.reason}
          >
            {coldDirection?.label}
            <span className="ml-1 text-[10px] opacity-60">
              ({(cold_correction.model_original.home * 100).toFixed(0)}→{(home_prob * 100).toFixed(0)}%)
            </span>
          </span>
        )}
        {is_cold_match && !hasCorrection && (
          <span className="bg-cold-bg text-amber px-1.5 py-0.5 rounded-sm">冷门预警</span>
        )}
        {is_hot_match && <span className="bg-hot-bg text-moss px-1.5 py-0.5 rounded-sm">热门推荐</span>}
        {risk_warning && Array.isArray(risk_warning) && risk_warning.length > 0 && (
          <span
            className="bg-amber/5 text-amber text-[10px] px-1.5 py-0.5 rounded-sm cursor-help border border-amber/15"
            title={risk_warning.join('\n')}
          >
            数据异常
          </span>
        )}
      </div>
    </Link>
  );
}
