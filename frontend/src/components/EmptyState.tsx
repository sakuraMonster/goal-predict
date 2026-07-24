interface EmptyStateProps {
  onNextDay?: () => void;
  message?: string;
}

export default function EmptyState({ onNextDay, message }: EmptyStateProps) {
  return (
    <div className="flex items-center justify-center py-20">
      <div className="text-center bg-white rounded-lg p-10 border border-border max-w-md">
        <div className="text-5xl mb-3 opacity-30">☐</div>
        <div className="text-base font-bold text-ink mb-2">
          {message || "当日无竞彩赛事"}
        </div>
        <div className="font-body text-xs text-ink-light leading-relaxed">
          暂无竞彩足球开售赛事<br />
          请选择其他日期查看，或等待赛程更新
        </div>
        {onNextDay && (
          <div className="mt-4 font-body flex gap-2 justify-center">
            <button
              onClick={onNextDay}
              className="bg-white border border-border-dark text-ink px-3.5 py-1.5 rounded text-[11px] hover:border-moss transition-colors"
            >
              查看明日
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
