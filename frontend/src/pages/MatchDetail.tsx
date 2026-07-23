import { useState, useEffect } from "react";
import { useParams, Link } from "react-router-dom";
import { getMatchDetail, getOddsHistory, getH2H, getPrediction } from "../api/client";

export default function MatchDetail() {
  const { id } = useParams<{ id: string }>();
  const [match, setMatch] = useState<any>(null);
  const [prediction, setPrediction] = useState<any>(null);
  const [odds, setOdds] = useState<any[]>([]);
  const [h2h, setH2h] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!id) return;
    Promise.all([
      getMatchDetail(Number(id)),
      getPrediction(Number(id)),
      getOddsHistory(Number(id)),
      getH2H(Number(id)),
    ]).then(([m, p, o, h]) => {
      setMatch(m.data);
      setPrediction(p.data);
      setOdds(o.data || []);
      setH2h(h.data || []);
    }).finally(() => setLoading(false));
  }, [id]);

  if (loading) return <div className="p-8 text-center text-ink-muted">加载中...</div>;
  if (!match) return <div className="p-8 text-center text-ink-muted">赛事未找到</div>;

  const pred = prediction || {};
  const isCold = pred.is_cold_match;
  const oddsLatest = odds[0] || {};

  return (
    <div className="bg-parchment text-ink">
      {/* 顶部栏 */}
      <div className="flex items-center gap-3 px-5 py-2.5 bg-parchment-dark border-b border-border-dark font-body text-xs text-ink-muted">
        <Link to="/" className="text-sm hover:text-moss">← 返回赛事列表</Link>
        <span className="ml-auto">模型 {pred.model_version || "v0.1.0"} · 更新于 {pred.created_at?.slice(11,16) || "--"}</span>
      </div>

      {/* 对阵概要 */}
      <div className="px-6 py-4 text-center border-b border-border">
        <div className="font-body text-xs text-ink-light tracking-wide">
          {match.league_name} · 第 {match.round || "--"} 轮 · {match.kickoff_time?.slice(0,16)} · {match.venue || "--"} · 竞彩编号 {match.jc_match_id}
        </div>
        <div className="flex items-center justify-center gap-6 mt-2.5">
          <div className="text-center">
            <div className="w-12 h-12 bg-border rounded-full mx-auto mb-2" />
            <div className="text-lg font-bold">{match.home_team}</div>
            <div className="font-body text-[10px] text-ink-light">排名 {match.home_rank || "-"}</div>
          </div>
          <div className="font-body text-2xl font-bold text-ink">VS</div>
          <div className="text-center">
            <div className="w-12 h-12 bg-border rounded-full mx-auto mb-2" />
            <div className="text-lg font-bold">{match.away_team}</div>
            <div className="font-body text-[10px] text-ink-light">排名 {match.away_rank || "-"}</div>
          </div>
        </div>
      </div>

      {/* 主内容：左底座 + 右预测 */}
      <div className="flex border-b border-border">
        {/* 左：预测底座 */}
        <div className="flex-[1.15] p-3.5 border-r border-border">
          <div className="text-[11px] text-ink-light font-body uppercase tracking-wide mb-2.5">预测数据底座</div>

          {/* 雷达图占位 */}
          <div className="bg-white rounded-md border border-border p-3.5 mb-2.5">
            <div className="text-[13px] font-bold mb-2">球队战力雷达对比</div>
            <div className="h-40 bg-parchment-light rounded flex items-center justify-center text-sand font-body text-[11px]">
              雷达图 · 进攻/防守/控球/xG/状态/阵容
            </div>
          </div>

          {/* 交锋 + 状态并排 */}
          <div className="flex gap-2.5 mb-2.5">
            <div className="flex-1 bg-white rounded-md border border-border p-3">
              <div className="text-xs font-bold mb-1.5">近期交锋</div>
              <div className="font-body text-[10px]">
                {h2h.slice(0, 4).map((h: any, i: number) => (
                  <div key={i} className="flex justify-between py-0.5 border-b border-highlight last:border-0">
                    <span>{h.date?.slice(0,7)}</span>
                    <span className="font-semibold">{h.home_score}:{h.away_score}</span>
                  </div>
                ))}
                {h2h.length === 0 && <span className="text-ink-light">暂无数据</span>}
              </div>
            </div>
            <div className="flex-1 bg-white rounded-md border border-border p-3">
              <div className="text-xs font-bold mb-1.5">近期状态</div>
              <div className="font-body text-[10px] text-ink-light">暂无数据</div>
            </div>
          </div>

          {/* 赔率趋势 */}
          <div className="bg-white rounded-md border border-border p-3.5">
            <div className="text-[13px] font-bold mb-2">赔率变动趋势</div>
            <div className="font-body text-[10px]">
              <div className="flex items-center gap-2 mb-1.5">
                <span className="text-ink-light w-7">欧赔</span>
                <span className="text-ink-muted w-10">主胜</span>
                <span className="text-sand">{oddsLatest.home_win || "--"} → --</span>
              </div>
              <div className="flex items-center gap-2 mb-1.5">
                <span className="w-7" />
                <span className="text-ink-muted w-10">平局</span>
                <span className="text-sand">{oddsLatest.draw || "--"} → --</span>
              </div>
              <div className="flex items-center gap-2">
                <span className="w-7" />
                <span className="text-ink-muted w-10">客胜</span>
                <span className="text-sand">{oddsLatest.away_win || "--"} → --</span>
              </div>
            </div>
          </div>
        </div>

        {/* 右：预测输出（两个板块） */}
        <div className="flex-[0.85] p-3.5">
          <div className="text-[11px] text-ink-light font-body uppercase tracking-wide mb-2.5">模型预测输出</div>

          {/* 板块一：模型A */}
          <div className="bg-white rounded-md border border-border p-3.5 mb-2.5">
            <div className="flex items-center gap-2 mb-2.5">
              <span className="bg-moss text-white text-[9px] px-1.5 py-0.5 rounded-sm font-body">模型 A</span>
              <span className="text-[13px] font-bold">胜平负 & 让胜平负</span>
              <span className="font-body text-[9px] text-ink-light ml-auto">LightGBM 多任务</span>
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
                让球胜平负 <span className="text-[10px] text-ink-light font-normal">(-{match.handicap_line || 1}球)</span>
              </div>
              <div className="flex gap-1.5 font-body">
                {(["handicap_home_prob","handicap_draw_prob","handicap_away_prob"] as const).map((k, i) => {
                  const labels = ["让胜","让平","让负"];
                  const prob = pred[k] || 0.33;
                  return (
                    <div key={k} className="flex-1 text-center bg-parchment-light rounded-md py-2">
                      <div className="text-lg font-semibold text-ink">{(prob * 100).toFixed(1)}%</div>
                      <div className="text-[10px] text-ink-muted">{labels[i]}</div>
                    </div>
                  );
                })}
              </div>
            </div>
          </div>

          {/* 板块二：模型B */}
          <div className="bg-white rounded-md border border-border p-3.5">
            <div className="flex items-center gap-2 mb-2.5">
              <span className="bg-rust text-white text-[9px] px-1.5 py-0.5 rounded-sm font-body">模型 B</span>
              <span className="text-[13px] font-bold">进球数 & 比分推导</span>
              <span className="font-body text-[9px] text-ink-light ml-auto">Poisson 回归</span>
            </div>

            {/* 进球数分布 */}
            <div className="mb-2.5">
              <div className="text-[11px] font-semibold text-ink-muted mb-1.5">总进球数分布</div>
              <div className="font-body text-[10px]">
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
              <div className="text-[11px] font-semibold text-ink-muted mb-1.5">参考比分 Top5 <span className="text-[9px] text-ink-light font-normal">A+B联合推导</span></div>
              <div className="font-body">
                {(pred.score_top5_json || []).map((s: any, i: number) => (
                  <div key={i} className={`flex items-center justify-between py-1.5 px-2 ${i===0?"bg-highlight rounded":""}`}>
                    <span className={`font-bold ${i===0?"text-moss":"text-ink"} text-xs`}>{s.score}</span>
                    <span className={`text-xs ${i===0?"text-moss font-semibold":""}`}>{(s.prob*100).toFixed(1)}%</span>
                    <span className="text-[9px] text-ink-light">{s.result==="home"?"主胜":s.result==="draw"?"平局":"客胜"}</span>
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
          <span className="font-body text-[10px] text-ink-light">基于模型 {pred.model_version || "v0.1.0"}</span>
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
        </div>
        <div className="flex justify-end gap-2 mt-2.5 font-body">
          <button className="bg-white border border-border-dark text-ink px-3.5 py-1.5 rounded text-[11px]">复制报告</button>
          <button className="bg-moss border border-moss text-white px-3.5 py-1.5 rounded text-[11px]">导出本场 PDF</button>
        </div>
      </div>
    </div>
  );
}
