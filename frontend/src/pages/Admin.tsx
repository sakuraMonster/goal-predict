export default function Admin() {
  const tasks = [
    { name: "赛程同步", freq: "每日 09:00 / 12:00", last: "--", next: "12:00", status: "idle" },
    { name: "赔率更新", freq: "09:00 / 14:00 / 18:00", last: "--", next: "14:00", status: "idle" },
    { name: "球队信息更新", freq: "每日 03:00", last: "--", next: "03:00", status: "idle" },
    { name: "模型重训练", freq: "每周一 04:00", last: "--", next: "周一04:00", status: "idle" },
  ];

  return (
    <div className="bg-parchment">
      <div className="flex items-center justify-between px-5 py-3 bg-parchment-dark border-b border-border-dark">
        <span className="font-heading text-base font-bold tracking-wider">系统管理</span>
        <span className="font-body text-xs text-ink-muted">服务运行中</span>
      </div>

      {/* 任务状态 */}
      <div className="px-5 py-3.5 border-b border-border">
        <div className="text-[13px] font-bold mb-2.5">定时任务状态</div>
        <div className="flex gap-3">
          {tasks.map((t) => (
            <div key={t.name} className="flex-1 bg-white rounded-md border border-border p-3 font-body">
              <div className="flex items-center gap-2 mb-2">
                <span className={`w-2 h-2 rounded-full ${t.status === "idle" ? "bg-sand" : "bg-moss"}`} />
                <span className="font-semibold text-xs text-ink">{t.name}</span>
                <span className="ml-auto text-[10px] text-ink-light">{t.freq}</span>
              </div>
              <div className="text-[10px] text-ink-muted leading-relaxed">
                上次执行：<span className="text-ink">{t.last}</span><br/>
                下次执行：{t.next}
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* 手动操作 */}
      <div className="px-5 py-3.5 border-b border-border">
        <div className="text-[13px] font-bold mb-2.5">手动操作</div>
        <div className="flex gap-2.5 font-body">
          {["同步赛程","刷新赔率","更新球队","重训练"].map((action) => (
            <button key={action} className="flex-1 bg-white border border-border-dark rounded-md p-3 text-center text-ink hover:border-moss transition-colors">
              <div className="text-[13px] font-semibold">立即{action}</div>
              <div className="text-[10px] text-ink-light mt-0.5">手动触发{action}任务</div>
            </button>
          ))}
        </div>
      </div>

      {/* 日志 */}
      <div className="px-5 py-3.5">
        <div className="flex items-center justify-between mb-2.5">
          <span className="text-[13px] font-bold">最近日志</span>
          <span className="font-body text-[10px] text-ink-light">最近 48 小时</span>
        </div>
        <div className="bg-white rounded-md border border-border p-4 text-center text-ink-muted font-body text-xs">
          暂无日志记录 · 系统运行后将自动记录任务执行状态
        </div>
      </div>
    </div>
  );
}
