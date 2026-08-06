import { useState, useEffect, useCallback, createContext, useContext } from "react";

type ToastType = "success" | "error" | "warning" | "info";

interface ToastItem {
  id: number;
  message: string;
  type: ToastType;
}

interface ToastContextType {
  toast: (message: string, type?: ToastType) => void;
}

const ToastContext = createContext<ToastContextType>({ toast: () => {} });

let _nextId = 0;

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([]);

  const addToast = useCallback((message: string, type: ToastType = "info") => {
    const id = ++_nextId;
    setToasts(prev => [...prev, { id, message, type }]);
  }, []);

  const removeToast = useCallback((id: number) => {
    setToasts(prev => prev.filter(t => t.id !== id));
  }, []);

  return (
    <ToastContext.Provider value={{ toast: addToast }}>
      {children}
      <div className="fixed top-4 right-4 z-[9999] flex flex-col gap-2">
        {toasts.map(t => (
          <ToastEntry key={t.id} item={t} onDone={() => removeToast(t.id)} />
        ))}
      </div>
    </ToastContext.Provider>
  );
}

function ToastEntry({ item, onDone }: { item: ToastItem; onDone: () => void }) {
  useEffect(() => {
    const timer = setTimeout(onDone, 3000);
    return () => clearTimeout(timer);
  }, [onDone]);

  const bgMap: Record<ToastType, string> = {
    success: "bg-moss text-white",
    error: "bg-rust text-white",
    warning: "bg-amber text-white",
    info: "bg-ink text-white",
  };

  return (
    <div className={`px-4 py-2.5 rounded-md shadow-lg font-body text-xs ${bgMap[item.type]} animate-[slideIn_0.2s_ease-out] flex items-center gap-2 min-w-[240px]`}>
      <span className="text-sm">
        {item.type === "success" ? "✓" : item.type === "error" ? "✗" : item.type === "warning" ? "⚠" : "ℹ"}
      </span>
      <span className="flex-1">{item.message}</span>
      <button onClick={onDone} className="opacity-60 hover:opacity-100 text-sm leading-none">×</button>
    </div>
  );
}

export function useToast() {
  return useContext(ToastContext);
}
