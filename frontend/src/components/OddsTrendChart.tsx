import { useState, useMemo } from "react";
import ReactECharts from "echarts-for-react";

interface OddsPoint {
  time: string;
  home_win: number | null;
  draw: number | null;
  away_win: number | null;
  handicap_line: number | null;
  handicap_home: number | null;
  handicap_away: number | null;
}

interface Props {
  data: Record<string, OddsPoint[]>;
  opening: Record<string, OddsPoint>;
  matchHandicapLine: number | null;
  hasFixture?: boolean;
}

type ViewMode = "1x2" | "handicap" | "line";

// ── 初盘信息条 ──
function OpeningBar({ mode, opening, latest, hcpLine }: {
  mode: ViewMode; opening: OddsPoint; latest?: OddsPoint; hcpLine: number | null;
}) {
  const fmtDelta = (cur: number | null | undefined, open: number | null | undefined) => {
    if (cur == null || open == null) return null;
    const d = cur - open;
    // ↑ 赔率升高→不被看好  ↓ 赔率降低→更被看好  无变化不显示
    const sign = d > 0 ? "↑" : d < 0 ? "↓" : "";
    const color = d > 0 ? "text-rust" : d < 0 ? "text-moss" : "";
    return { sign, color, val: Math.abs(d).toFixed(2) };
  };

  if (mode === "1x2") {
    const items = [
      { label: "主", color: "#2d5a3b", cur: latest?.home_win, open: opening.home_win },
      { label: "平", color: "#c4a44a", cur: latest?.draw, open: opening.draw },
      { label: "客", color: "#c4553d", cur: latest?.away_win, open: opening.away_win },
    ];
    return (
      <div className="flex items-center gap-3 mb-2 text-xs" title="↓赔率降低更被看好  ↑赔率升高不被看好">
        <span className="text-ink-muted">初盘</span>
        {items.map((it) => {
          const d = fmtDelta(it.cur, it.open);
          return (
            <span key={it.label} className="flex items-center gap-1">
              <span className="w-1.5 h-1.5 rounded-full inline-block" style={{ background: it.color }} />
              {it.label} {it.open?.toFixed(2)}{d && d.sign && <span className={d.color}> {d.sign}{d.val}</span>}
            </span>
          );
        })}
      </div>
    );
  }

  // handicap
  const dHome = fmtDelta(latest?.handicap_home, opening.handicap_home);
  const dAway = fmtDelta(latest?.handicap_away, opening.handicap_away);
  const homeLabel = hcpLine != null ? (hcpLine > 0 ? `主+${hcpLine}` : `主${hcpLine}`) : "主";
  const awayLabel = hcpLine != null ? (hcpLine > 0 ? `客-${hcpLine}` : `客+${-hcpLine}`) : "客";
  const hcpHomeVal = opening.handicap_home ?? latest?.handicap_home;
  const hcpAwayVal = opening.handicap_away ?? latest?.handicap_away;
  return (
    <div className="flex items-center gap-3 mb-2 text-xs" title="↓赔率降低更被看好  ↑赔率升高不被看好">
      <span className="text-ink-muted">初盘</span>
      <span className="flex items-center gap-1">
        <span className="w-1.5 h-1.5 rounded-full inline-block" style={{ background: "#2d5a3b" }} />
        {homeLabel} {hcpHomeVal?.toFixed(2)}{dHome && dHome.sign && <span className={dHome.color}> {dHome.sign}{dHome.val}</span>}
      </span>
      <span className="flex items-center gap-1">
        <span className="w-1.5 h-1.5 rounded-full inline-block" style={{ background: "#c4553d" }} />
        {awayLabel} {hcpAwayVal?.toFixed(2)}{dAway && dAway.sign && <span className={dAway.color}> {dAway.sign}{dAway.val}</span>}
      </span>
    </div>
  );
}

export default function OddsTrendChart({ data, opening, matchHandicapLine, hasFixture }: Props) {
  const bookmakers = useMemo(() => Object.keys(data), [data]);
  const [selectedBm, setSelectedBm] = useState(bookmakers[0] || "");
  const [viewMode, setViewMode] = useState<ViewMode>("1x2");

  const points = data[selectedBm] || [];
  const openingPoint = opening[selectedBm];

  // 1X2 去重
  const spfPoints = useMemo(() => {
    const seen = new Set<string>();
    return points.filter((p) => {
      const k = p.time;
      if (seen.has(k)) return false;
      seen.add(k);
      return p.home_win != null;
    });
  }, [points]);

  // 亚盘：取最接近竞彩网让球线的盘口（SM 数据为 0.5 步进，与竞彩网整数不完全一致）
  const hcpTargetLine = matchHandicapLine;
  const { hcpMatchedLine, hcpPoints } = useMemo(() => {
    if (hcpTargetLine == null) return { hcpMatchedLine: null, hcpPoints: [] as OddsPoint[] };
    // 收集所有可用盘口线
    const availableLines = new Set<number>();
    points.forEach((p) => {
      if (p.handicap_line != null && p.handicap_home != null && p.handicap_away != null) {
        availableLines.add(p.handicap_line);
      }
    });
    if (availableLines.size === 0) return { hcpMatchedLine: null, hcpPoints: [] as OddsPoint[] };
    // 找到最接近目标值的盘口线
    const sorted = Array.from(availableLines).sort((a, b) => Math.abs(a - hcpTargetLine) - Math.abs(b - hcpTargetLine));
    const closest = sorted[0];
    return {
      hcpMatchedLine: closest,
      hcpPoints: points.filter(
        (p) => p.handicap_line === closest && p.handicap_home != null
      ),
    };
  }, [points, hcpTargetLine]);

  // 盘口变化：按时间去重，每个时间点取最接近竞彩网让球线的盘口
  const linePoints = useMemo(() => {
    if (hcpTargetLine != null) {
      const timeMap = new Map<string, OddsPoint>();
      points.forEach((p) => {
        if (p.handicap_line == null || p.handicap_home == null || p.handicap_away == null) return;
        const existing = timeMap.get(p.time);
        if (!existing || Math.abs(p.handicap_line - hcpTargetLine) < Math.abs(existing.handicap_line - hcpTargetLine)) {
          timeMap.set(p.time, p);
        }
      });
      return Array.from(timeMap.values()).sort((a, b) => a.time.localeCompare(b.time));
    }
    // 无目标线时，取每种盘口线的首次出现
    const seen = new Set<number>();
    return points
      .filter((p) => {
        if (p.handicap_line == null) return false;
        if (seen.has(p.handicap_line)) return false;
        seen.add(p.handicap_line);
        return p.handicap_home != null && p.handicap_away != null;
      })
      .sort((a, b) => a.time.localeCompare(b.time));
  }, [points, hcpTargetLine]);

  // ====== 时间轴智能间隔 ======
  const timeAxis = useMemo(() => {
    const getConfig = (pts: OddsPoint[]) => {
      if (pts.length === 0) return { times: [] as string[], axisLabel: {} };
      const fullTimes = pts.map(p => p.time);
      const first = new Date(fullTimes[0]).getTime();
      const last = new Date(fullTimes[fullTimes.length - 1]).getTime();
      const spanHours = (last - first) / 3600000;

      const times = fullTimes.map(t => t.slice(11, 16));
      if (spanHours <= 1.5 || fullTimes.length <= 8) {
        return { times, axisLabel: { fontSize: 10, color: "#8b7e6a" } };
      }

      let intervalMin = 30;
      if (spanHours > 6) intervalMin = 60;
      if (spanHours > 12) intervalMin = 120;
      if (spanHours > 24) intervalMin = 240;

      const showIndices = new Set<number>();
      let lastBucket = -1;
      fullTimes.forEach((t, i) => {
        const d = new Date(t);
        const bucket = Math.floor((d.getHours() * 60 + d.getMinutes()) / intervalMin);
        if (bucket !== lastBucket) { showIndices.add(i); lastBucket = bucket; }
      });
      showIndices.add(0);
      showIndices.add(fullTimes.length - 1);

      return {
        times,
        axisLabel: {
          fontSize: 10, color: "#8b7e6a",
          interval: (index: number) => showIndices.has(index),
        },
      };
    };
    return {
      spf: getConfig(spfPoints),
      hcp: getConfig(hcpPoints),
      line: getConfig(linePoints),
    };
  }, [spfPoints, hcpPoints, linePoints]);

  // ====== Chart Options ======

  const chartOption = useMemo(() => {
    if (!selectedBm) return {};

    const baseXAxis = (ta: ReturnType<typeof timeAxis.spf>) => ({
      type: "category" as const,
      data: ta.times,
      axisLabel: ta.axisLabel,
      axisLine: { lineStyle: { color: "#e0d6c2" } },
      axisTick: { show: false },
    });

    // --- 欧赔 ---
    if (viewMode === "1x2") {
      return {
        tooltip: { trigger: "axis" },
        legend: { show: true, bottom: 0, itemWidth: 14, itemHeight: 2, textStyle: { fontSize: 10, color: "#8b7e6a" } },
        grid: { top: 10, right: 16, bottom: 44, left: 44 },
        xAxis: baseXAxis(timeAxis.spf),
        yAxis: { type: "value", inverse: true, min: (v: any) => Math.floor(v.min * 20) / 20 - 0.1, max: (v: any) => Math.ceil(v.max * 20) / 20 + 0.1, axisLabel: { fontSize: 10, color: "#8b7e6a" }, splitLine: { lineStyle: { color: "#f0e8d8", type: "dashed" } } },
        series: [
          { name: "主胜", type: "line", data: spfPoints.map(p => p.home_win), smooth: 0.5, symbol: "circle", symbolSize: 6, lineStyle: { color: "#2d5a3b", width: 2 }, itemStyle: { color: "#2d5a3b" }, areaStyle: { color: { type: "linear", x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: "rgba(45,90,59,0.12)" }, { offset: 1, color: "rgba(45,90,59,0.0)" }] } } },
          { name: "平局", type: "line", data: spfPoints.map(p => p.draw), smooth: 0.5, symbol: "circle", symbolSize: 6, lineStyle: { color: "#b89a2e", width: 2, type: "dashed" }, itemStyle: { color: "#b89a2e" }, areaStyle: { color: { type: "linear", x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: "rgba(184,154,46,0.10)" }, { offset: 1, color: "rgba(184,154,46,0.0)" }] } } },
          { name: "客胜", type: "line", data: spfPoints.map(p => p.away_win), smooth: 0.5, symbol: "circle", symbolSize: 6, lineStyle: { color: "#c4553d", width: 2 }, itemStyle: { color: "#c4553d" }, areaStyle: { color: { type: "linear", x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: "rgba(196,85,61,0.10)" }, { offset: 1, color: "rgba(196,85,61,0.0)" }] } } },
        ],
      };
    }

    // --- 亚盘（最接近竞彩网让球线的盘口） ---
    if (viewMode === "handicap") {
      const displayLine = hcpMatchedLine ?? hcpTargetLine;
      const homeLabel = displayLine != null ? (displayLine > 0 ? `主+${displayLine}` : `主${displayLine}`) : "主";
      const awayLabel = displayLine != null ? (displayLine > 0 ? `客-${displayLine}` : `客+${-displayLine}`) : "客";

      return {
        tooltip: { trigger: "axis" },
        legend: { show: true, bottom: 0, itemWidth: 14, itemHeight: 2, textStyle: { fontSize: 10, color: "#8b7e6a" } },
        grid: { top: 10, right: 16, bottom: 44, left: 44 },
        xAxis: baseXAxis(timeAxis.hcp),
        yAxis: { type: "value", inverse: true, axisLabel: { fontSize: 10, color: "#8b7e6a" }, splitLine: { lineStyle: { color: "#f0e8d8", type: "dashed" } } },
        series: [
          { name: homeLabel, type: "line", data: hcpPoints.map(p => p.handicap_home), smooth: 0.5, symbol: "circle", symbolSize: 6, lineStyle: { color: "#2d5a3b", width: 2 }, itemStyle: { color: "#2d5a3b" }, areaStyle: { color: { type: "linear", x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: "rgba(45,90,59,0.12)" }, { offset: 1, color: "rgba(45,90,59,0.0)" }] } } },
          { name: awayLabel, type: "line", data: hcpPoints.map(p => p.handicap_away), smooth: 0.5, symbol: "circle", symbolSize: 6, lineStyle: { color: "#c4553d", width: 2 }, itemStyle: { color: "#c4553d" }, areaStyle: { color: { type: "linear", x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: "rgba(196,85,61,0.10)" }, { offset: 1, color: "rgba(196,85,61,0.0)" }] } } },
        ],
      };
    }

    // --- 盘口变化趋势 ---
    const lineTimes = linePoints.map((p) => p.time.slice(11, 16));
    const openingLine = openingPoint?.handicap_line;

    return {
      tooltip: { trigger: "axis", formatter: (params: any) => {
        const v = params[0]?.value;
        const label = v != null ? (v > 0 ? `+${v}` : `${v}`) : "-";
        return `<div class="text-xs">${params[0]?.axisValue}<br/>盘口: <b>${label}</b></div>`;
      }},
      legend: { show: true, bottom: 0, itemWidth: 14, itemHeight: 2, textStyle: { fontSize: 10, color: "#8b7e6a" } },
      grid: { top: 10, right: 16, bottom: 44, left: 44 },
      xAxis: baseXAxis(timeAxis.line),
      yAxis: { type: "value", axisLabel: { fontSize: 10, color: "#8b7e6a", formatter: (v: number) => v > 0 ? `+${v}` : `${v}` }, splitLine: { lineStyle: { color: "#f0e8d8", type: "dashed" } } },
      series: [
        {
          name: "盘口", type: "line", data: linePoints.map(p => p.handicap_line), smooth: 0.5, symbol: "circle", symbolSize: 6,
          lineStyle: { color: "#2d5a3b", width: 2 }, itemStyle: { color: "#2d5a3b" },
          areaStyle: { color: { type: "linear", x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: "rgba(45,90,59,0.12)" }, { offset: 1, color: "rgba(45,90,59,0.0)" }] } },
          markLine: openingLine != null ? {
            silent: true, symbol: "none",
            lineStyle: { type: "dashed", color: "#c4a44a", width: 1.5 },
            label: { show: true, position: "start", fontSize: 9, color: "#c4a44a", formatter: `初盘 ${openingLine > 0 ? "+" : ""}${openingLine}` },
            data: [{ yAxis: openingLine }],
          } : undefined,
        },
      ],
    };
  }, [spfPoints, hcpPoints, hcpMatchedLine, linePoints, selectedBm, viewMode, hcpTargetLine, openingPoint, timeAxis]);

  // ====== Empty State ======
  if (bookmakers.length === 0) {
    const emptyMsg = hasFixture
      ? "赔率暂未开盘，请临近比赛时刷新"
      : "暂无赔率数据（赛事未收录至 SportMonks）";
    return (
      <div className="bg-white rounded-md border border-border p-3.5">
        <div className="text-[13px] font-bold mb-2">赔率变动趋势</div>
        <div className="text-[11px] text-ink-light text-center py-6">{emptyMsg}</div>
      </div>
    );
  }

  // ====== Render ======
  return (
    <div className="bg-white rounded-md border border-border p-3.5">
      <div className="flex items-center justify-between mb-2.5">
        <div className="text-[13px] font-bold">赔率变动趋势</div>
        <div className="flex items-center gap-1.5">
          {/* 视图切换 */}
          <div className="flex rounded-sm overflow-hidden border border-border text-xs">
            {(["1x2", "handicap", "line"] as ViewMode[]).map((m) => {
              const labels: Record<ViewMode, string> = { "1x2": "欧赔", handicap: "亚盘", line: "盘口" };
              return (
                <button key={m} onClick={() => setViewMode(m)}
                  className={`px-2 py-0.5 ${viewMode === m ? "bg-moss text-white" : "bg-white text-ink-muted"}`}>
                  {labels[m]}
                </button>
              );
            })}
          </div>
          {/* 博彩公司选择 */}
          <select value={selectedBm} onChange={(e) => setSelectedBm(e.target.value)}
            className="text-xs bg-white border border-border rounded-sm px-1.5 py-0.5 text-ink outline-none">
            {bookmakers.map((bm) => (<option key={bm} value={bm}>{bm}</option>))}
          </select>
        </div>
      </div>

      {/* 初盘信息栏 */}
      {openingPoint && (viewMode === "1x2" || viewMode === "handicap") && (
        <OpeningBar
          mode={viewMode}
          opening={openingPoint}
          latest={viewMode === "1x2" ? spfPoints[spfPoints.length - 1] : hcpPoints[hcpPoints.length - 1]}
          hcpLine={hcpMatchedLine}
        />
      )}

      {/* 亚盘提示 */}
      {viewMode === "handicap" && (
        <div className="text-xs text-ink-muted mb-1.5">
          实际盘口：{hcpMatchedLine != null ? (hcpMatchedLine > 0 ? `+${hcpMatchedLine}` : hcpMatchedLine) : "—"}（竞彩网让球 {hcpTargetLine != null ? (hcpTargetLine > 0 ? `+${hcpTargetLine}` : hcpTargetLine) : "—"}，取最接近的 SM 盘口）
          {hcpPoints.length === 0 && <span className="text-amber ml-1">暂无该盘口的赔率数据</span>}
        </div>
      )}

      <ReactECharts key={`${viewMode}-${selectedBm}`} option={chartOption} style={{ height: 220 }} opts={{ renderer: "canvas" }} />
    </div>
  );
}
