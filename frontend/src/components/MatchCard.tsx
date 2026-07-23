import { Link } from "react-router-dom";

interface MatchCardProps {
  id: number;
  league_name: string;
  kickoff_time: string;
  home_team: string;
  away_team: string;
  home_prob: number;
  draw_prob: number;
  away_prob: number;
  expected_goals: number;
  reference_score: string;
  is_cold_match: boolean;
  is_hot_match: boolean;
  confidence_level: string;
}

export default function MatchCard({
  id, league_name, kickoff_time, home_team, away_team,
  home_prob, draw_prob, away_prob, expected_goals,
  reference_score, is_cold_match, is_hot_match,
}: MatchCardProps) {
  const time = new Date(kickoff_time).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
  const maxProb = Math.max(home_prob, draw_prob, away_prob);

  return (
    <Link to={`/match/${id}`} className="block bg-white rounded-md border border-border p-3.5 hover:shadow-sm transition-shadow">
      <div className="text-[10px] text-ink-light tracking-wide font-body uppercase">{league_name} · {time}</div>
      <div className="text-sm font-bold my-1 flex justify-between items-center text-ink">
        <span>{home_team}</span>
        <span className="text-[10px] text-ink-light font-body">vs</span>
        <span>{away_team}</span>
      </div>

      {/* 概率条 */}
      <div className="flex gap-1.5 my-2">
        <div className="flex-1 h-1 bg-border rounded-sm overflow-hidden">
          <div className="h-full bg-moss rounded-sm" style={{ width: `${home_prob * 100}%` }} />
        </div>
        <div className="flex-1 h-1 bg-border rounded-sm overflow-hidden">
          <div className="h-full bg-sand rounded-sm" style={{ width: `${draw_prob * 100}%` }} />
        </div>
        <div className="flex-1 h-1 bg-border rounded-sm overflow-hidden">
          <div className="h-full bg-rust rounded-sm" style={{ width: `${away_prob * 100}%` }} />
        </div>
      </div>

      <div className="flex justify-between font-body text-[11px] text-ink-muted">
        <span className={home_prob === maxProb ? "text-moss font-semibold" : ""}>主 {(home_prob * 100).toFixed(0)}%</span>
        <span>平 {(draw_prob * 100).toFixed(0)}%</span>
        <span>客 {(away_prob * 100).toFixed(0)}%</span>
      </div>

      {/* 标签行 */}
      <div className="mt-2 flex gap-1.5 font-body text-[10px] text-ink-muted">
        <span className="bg-parchment-light px-1.5 py-0.5 rounded-sm">进球 {expected_goals.toFixed(1)}</span>
        <span className="bg-parchment-light px-1.5 py-0.5 rounded-sm">{reference_score} 参考</span>
        {is_cold_match && <span className="bg-cold-bg text-amber px-1.5 py-0.5 rounded-sm">冷门预警</span>}
        {is_hot_match && <span className="bg-hot-bg text-moss px-1.5 py-0.5 rounded-sm">热门推荐</span>}
      </div>
    </Link>
  );
}
