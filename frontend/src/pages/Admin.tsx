import { useState, useEffect } from "react";
import api from "../api/client";

interface TaskInfo {
  task_type: string;
  status: string;
  last_run: string | null;
  duration_ms: number;
  freq: string;
  last_message: string;
}

const timeAgo = (dateStr: string) => {
  const diff = Date.now() - new Date(dateStr).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "刚刚";
  if (mins < 60) return `${mins} 分钟前`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours} 小时前`;
  const days = Math.floor(hours / 24);
  return `${days} 天前`;
};

export default function Admin() {
  const [tasks, setTasks] = useState<TaskInfo[]>([]);
  const [logs, setLogs] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [acting, setActing] = useState<string | null>(null);
  const [result, setResult] = useState<{ ok: boolean; msg: string } | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(false);

  const [refreshing, setRefreshing] = useState(false);

  const fetchData = () => {
    setLoading(true);
    Promise.all([
      api.get("/admin/task-status").then((r) => r.data.data),
      api.get("/admin/logs").then((r) => r.data.data),
    ]).then(([t, l]) => {
      setTasks(t);
      setLogs(l);
    }).finally(() => setLoading(false));
  };

  useEffect(() => { fetchData(); }, []);

  const handleRefresh = async () => {
    setRefreshing(true);
    await Promise.all([
      api.get("/admin/task-status").then((r) => setTasks(r.data.data)),
      api.get("/admin/logs").then((r) => setLogs(r.data.data)),
    ]);
    setRefreshing(false);
  };

  useEffect(() => {
    if (!autoRefresh) return;
    const timer = setInterval(() => handleRefresh(), 30000);
    return () => clearInterval(timer);
  }, [autoRefresh]);

  const triggerAction = async (action: string, label: string) => {
    setActing(label);
    setResult(null);
    try {
      const r = await api.post(`/admin/${action}`);
      setResult({ ok: r.data.status === "ok", msg: r.data.message || r.data.status });
      fetchData();
    } catch (e: any) {
      setResult({ ok: false, msg: e?.response?.data?.detail || e.message || "请求失败" });
    } finally {
      setActing(null);
    }
  };

  const actions = [
    { key: "sync-matches", label: "同步赛程", desc: "拉取当日+未来3日竞彩赛事" },
    { key: "trigger-predict", label: "生成预测", desc: "对未来48h赛事执行模型预测" },
    { key: "sync-odds", label: "刷新赔率", desc: "同步欧赔/亚盘最新变动" },
    { key: "update-teams", label: "更新球队", desc: "伤病/阵容/积分排名刷新" },
    { key: "trigger-retrain", label: "重训练", desc: "使用最新数据重新训练模型" },
  ];

  return (
    <div className="bg-parchment">
      <div className="flex items-center justify-between px-5 py-3 bg-parchment-dark border-b border-border-dark">
        <span className="font-heading text-base font-bold tracking-wider">系统管理</span>
        <div className="flex items-center gap-3">
          <button
            onClick={handleRefresh}
            disabled={refreshing}
            className="font-body text-xs text-ink-muted hover:text-ink transition-colors disabled:opacity-50"
          >
            {refreshing ? "刷新中..." : "刷新"}
          </button>
          <button
            onClick={() => setAutoRefresh(!autoRefresh)}
            className={`font-body text-[11px] px-2 py-0.5 rounded border transition-colors ${autoRefresh ? "bg-moss text-white border-moss" : "text-ink-muted border-border hover:text-ink"}`}
          >
            {autoRefresh ? "自动刷新中" : "自动刷新"}
          </button>
          <span className="font-body text-xs text-ink-muted flex items-center gap-1.5">
            <span className="w-2 h-2 bg-moss rounded-full" />
            服务运行中
          </span>
        </div>
      </div>

      {/* 任务状态 */}
      <div className="px-5 py-3.5 border-b border-border">
        <div className="text-[13px] font-bold mb-2.5">定时任务状态</div>
        <div className="flex gap-3">
          {loading ? (
            <div className="flex gap-3">
              {[1,2,3,4].map(i => (
                <div key={i} className="flex-1 bg-white rounded-md border border-border p-3 animate-pulse">
                  <div className="flex items-center gap-2 mb-2">
                    <div className="w-2 h-2 bg-border rounded-full" />
                    <div className="w-16 h-3 bg-border rounded" />
                  </div>
                  <div className="w-24 h-2 bg-border rounded mb-1" />
                  <div className="w-32 h-2 bg-border rounded" />
                </div>
              ))}
            </div>
          ) : (
            tasks.map((t) => (
              <div key={t.task_type} className="flex-1 bg-white rounded-md border border-border p-3 font-body">
                <div className="flex items-center gap-2 mb-2">
                  <span className={`w-2 h-2 rounded-full ${t.status === "success" ? "bg-moss" : t.status === "failed" ? "bg-amber" : "bg-sand"}`} />
                  <span className="font-semibold text-xs text-ink">
                    {t.task_type === "sync_matches" ? "赛程同步" :
                     t.task_type === "sync_odds" ? "赔率更新" :
                     t.task_type === "update_teams" ? "球队信息" : "模型重训练"}
                  </span>
                  <span className="ml-auto text-xs text-ink-light">{t.freq}</span>
                </div>
                <div className="text-xs text-ink-muted leading-relaxed">
                  上次：{t.last_run ? <span className="text-moss">{t.last_run}</span> : "--"}
                  {t.duration_ms > 0 && <span className="ml-1">({(t.duration_ms / 1000).toFixed(1)}s)</span>}
                  {t.last_message && <div className="text-ink-light truncate mt-0.5">{t.last_message}</div>}
                </div>
              </div>
            ))
          )}
        </div>
      </div>

      {/* 手动操作 */}
      <div className="px-5 py-3.5 border-b border-border">
        <div className="text-[13px] font-bold mb-2.5">手动操作</div>
        {result && (
          <div className={`mb-3 px-3 py-2 rounded text-xs font-body flex items-center gap-2 ${result.ok ? "bg-hot-bg text-moss" : "bg-cold-bg text-amber"}`}>
            <span>{result.ok ? "✓" : "✗"}</span> {result.msg}
            <button className="ml-auto text-ink-light hover:text-ink" onClick={() => setResult(null)}>×</button>
          </div>
        )}
        <div className="flex gap-2.5 font-body">
          {actions.map((a) => (
            <button
              key={a.key}
              disabled={acting !== null}
              onClick={() => triggerAction(a.key, a.label)}
              className={`flex-1 bg-white border border-border-dark rounded-md p-3 text-center text-ink hover:border-moss hover:bg-parchment-light transition-colors disabled:opacity-50 ${acting === a.label ? "animate-pulse border-moss" : ""}`}
            >
              <div className="text-[13px] font-semibold">
                {acting === a.label ? "执行中..." : `立即${a.label}`}
              </div>
              <div className="text-xs text-ink-light mt-0.5">{a.desc}</div>
            </button>
          ))}
        </div>
      </div>

      {/* 日志 */}
      <div className="px-5 py-3.5">
        <div className="flex items-center justify-between mb-2.5">
          <span className="text-[13px] font-bold">最近日志</span>
          <span className="font-body text-xs text-ink-light">最近 48 小时</span>
        </div>
        {logs.length === 0 ? (
          <div className="bg-white rounded-md border border-border p-4 text-center text-ink-muted font-body text-xs">
            暂无日志记录
          </div>
        ) : (
          <div className="bg-white rounded-md border border-border overflow-hidden font-body text-xs">
            {logs.map((l, i) => (
              <div key={i} className="flex items-center gap-2.5 px-3.5 py-1.5 border-b border-highlight last:border-0">
                <span className={l.status === "success" ? "text-moss font-semibold" : l.status === "failed" ? "text-amber font-semibold" : "text-ink-muted"}>
                  {l.status === "success" ? "✓" : l.status === "failed" ? "✗" : "·"}
                </span>
                <span className="text-ink-light w-36" title={l.created_at}>{timeAgo(l.created_at)}</span>
                <span className="text-ink-muted">[{l.task_type}]</span>
                <span className="text-ink flex-1 truncate">{l.message}</span>
                {l.duration_ms > 0 && <span className="text-ink-light">{(l.duration_ms / 1000).toFixed(1)}s</span>}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
