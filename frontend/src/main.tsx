import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { ToastProvider } from "./components/Toast";

// ===== 兼容浏览器翻译/划词扩展对文本节点的 DOM 改写 =====
// 现象：Chrome/Edge 自动翻译或翻译扩展会把页面文本节点替换为 <font> 包装节点，
// React 仍持有改写前的旧文本节点引用，后续 commit 删除/插入时抛
// "NotFoundError: Failed to execute 'removeChild'..."，导致整树崩溃（facebook/react#11538）。
// 方案：对 DOM 增删方法做容错，外部已改写时静默跳过，React 不再中断 commit。
function patchNodeMethod(name: "removeChild" | "insertBefore" | "appendChild") {
  const proto = Node.prototype as unknown as Record<string, any>;
  const orig = proto[name];
  if (!orig || orig.__translation_patched) return;
  const wrapped = function (this: Node, ...args: unknown[]) {
    try {
      return orig.apply(this, args);
    } catch (e) {
      if (e instanceof DOMException && e.name === "NotFoundError") return;
      throw e;
    }
  };
  wrapped.__translation_patched = true;
  proto[name] = wrapped;
}
patchNodeMethod("removeChild");
patchNodeMethod("insertBefore");
patchNodeMethod("appendChild");

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ToastProvider><App /></ToastProvider>
  </StrictMode>,
)
