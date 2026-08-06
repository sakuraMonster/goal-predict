interface EmptyStateProps {
  onAction?: () => void;
  actionLabel?: string;
  message?: string;
  description?: string;
  icon?: string;
}

export default function EmptyState({ onAction, actionLabel, message, description, icon }: EmptyStateProps) {
  return (
    <div className="flex items-center justify-center py-20">
      <div className="text-center bg-white rounded-lg p-10 border border-border max-w-md">
        <div className="text-5xl mb-3 opacity-30">{icon || "☐"}</div>
        <div className="text-base font-bold text-ink mb-2">
          {message || "暂无数据"}
        </div>
        <div className="font-body text-xs text-ink-light leading-relaxed">
          {description || "当前没有可显示的数据"}
        </div>
        {onAction && (
          <button
            onClick={onAction}
            className="mt-4 bg-white border border-border-dark text-ink px-3.5 py-1.5 rounded text-xs hover:border-moss transition-colors"
          >
            {actionLabel || "查看"}
          </button>
        )}
      </div>
    </div>
  );
}
