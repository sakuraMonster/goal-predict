import { useMemo, useState } from "react";
import ReactECharts from "echarts-for-react";

export interface LeagueAccuracyItem {
  league_name: string;
  total: number;
  settled: number;
  hit: number;
  miss: number;
  accuracy: number;
}

interface Props {
  data: LeagueAccuracyItem[];
  loading?: boolean;
}

export default function LeagueAccuracyBarChart({ data, loading }: Props) {
  const [collapsed, setCollapsed] = useState(false);

  const option = useMemo(() => {
    // 按 accuracy 升序排列（横条从低到高）
    const sorted = [...data].sort((a, b) => a.accuracy - b.accuracy);

    const names = sorted.map((d) => d.league_name);
    const accuracies = sorted.map((d) => d.accuracy);

    return {
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "shadow" },
        backgroundColor: "#fff",
        borderColor: "#e2d9c5",
        textStyle: { color: "#3d3226", fontSize: 11, fontFamily: "system-ui" },
        formatter: (params: any) => {
          const item = params[0];
          if (!item) return "";
          const d = sorted[item.dataIndex];
          return `
            <div style="font-weight:600;margin-bottom:2px">${d.league_name}</div>
            <div>命中率 <b>${d.accuracy}%</b></div>
            <div style="color:#8b7e6a">共 ${d.settled} 场 · 命中 ${d.hit} · 未命中 ${d.miss}</div>
          `;
        },
      },
      grid: {
        left: 90,
        right: 50,
        top: 10,
        bottom: 10,
        containLabel: false,
      },
      xAxis: {
        type: "value",
        min: 0,
        max: 100,
        axisLabel: {
          fontSize: 10,
          color: "#8b7e6a",
          formatter: "{value}%",
        },
        axisLine: { show: false },
        axisTick: { show: false },
        splitLine: {
          lineStyle: { color: "#f0ebe0", type: "dashed" },
        },
      },
      yAxis: {
        type: "category",
        data: names,
        axisLabel: {
          fontSize: 11,
          color: "#3d3226",
          fontWeight: 500,
          overflow: "truncate",
          width: 80,
          fontFamily: "system-ui",
        },
        axisLine: { show: false },
        axisTick: { show: false },
      },
      series: [
        {
          type: "bar",
          data: accuracies.map((acc) => ({
            value: acc,
            itemStyle: {
              color: acc >= 60 ? "#2d5a3b" : acc >= 45 ? "#c4a44a" : "#c4553d",
              borderRadius: [0, 3, 3, 0],
            },
          })),
          barWidth: 16,
          label: {
            show: true,
            position: "right",
            fontSize: 10,
            color: "#3d3226",
            fontWeight: 600,
            formatter: "{c}%",
          },
          emphasis: {
            itemStyle: { opacity: 0.85 },
          },
        },
      ],
    };
  }, [data]);

  const header = (
    <button
      type="button"
      onClick={() => setCollapsed((c) => !c)}
      className="w-full flex items-center justify-between"
    >
      <span className="text-xs font-bold text-ink-light uppercase tracking-wide">近30天各联赛命中率</span>
      <span className="text-ink-light text-xs select-none">{collapsed ? "▸ 展开" : "▾ 收缩"}</span>
    </button>
  );

  if (loading) {
    return (
      <div className="bg-white rounded-lg border border-border p-4">
        {header}
        {!collapsed && (
          <>
            <div className="h-6 w-32 bg-parchment-light rounded mt-3 mb-3 animate-pulse" />
            <div className="h-[200px] bg-parchment-light rounded animate-pulse" />
          </>
        )}
      </div>
    );
  }

  if (data.length === 0) {
    return (
      <div className="bg-white rounded-lg border border-border p-4">
        {header}
        {!collapsed && (
          <div className="text-center text-xs text-ink-muted py-8">暂无联赛命中率数据</div>
        )}
      </div>
    );
  }

  return (
    <div className="bg-white rounded-lg border border-border p-4">
      {header}
      {!collapsed && (
        <ReactECharts
          option={option}
          style={{ height: Math.max(180, data.length * 32) }}
          opts={{ renderer: "svg" }}
        />
      )}
    </div>
  );
}
