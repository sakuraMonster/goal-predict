interface ErrorStateProps {
  message?: string;
  onRetry?: () => void;
}

export default function ErrorState({ message, onRetry }: ErrorStateProps) {
  return (
    <div className="flex items-center justify-center py-20">
      <div className="text-center bg-white rounded-lg p-10 border border-border max-w-md">
        <div className="text-5xl mb-3 opacity-30">!</div>
        <div className="text-base font-bold text-ink mb-2">加载失败</div>
        <div className="font-body text-xs text-ink-light leading-relaxed">
          {message || "数据加载失败，请检查网络连接后重试"}
        </div>
        {onRetry && (
          <button
            onClick={onRetry}
            className="mt-4 bg-moss text-white px-4 py-1.5 rounded text-xs hover:bg-moss/90 transition-colors"
          >
            重新加载
          </button>
        )}
      </div>
    </div>
  );
}
