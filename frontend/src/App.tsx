import { BrowserRouter, Routes, Route, Link, useLocation } from "react-router-dom";
import { useState } from "react";
import Dashboard from "./pages/Dashboard";
import MatchDetail from "./pages/MatchDetail";
import Report from "./pages/Report";
import Review from "./pages/Review";
import GoalsPrediction from "./pages/GoalsPrediction";
import MarketFlowV2 from "./pages/MarketFlowV2";
import Mapping from "./pages/Mapping";
import Admin from "./pages/Admin";

function App() {
  return (
    <BrowserRouter>
      <AppLayout />
    </BrowserRouter>
  );
}

function AppLayout() {
  const location = useLocation();
  const isActive = (path: string) => location.pathname === path ? "text-moss font-semibold" : "";

  return (
    <div className="min-h-screen bg-parchment">
      <nav className="bg-parchment-dark border-b border-border-dark px-6 py-3 flex items-center justify-between">
        <Link to="/" className="text-base font-bold tracking-wider font-heading text-ink">
          竞彩预测系统
        </Link>
        <div className="flex gap-7 text-sm font-body text-ink-muted">
          <Link
            to="/"
            className={`hover:text-moss transition-colors ${location.pathname === "/" || location.pathname === "/market-flow" ? "text-moss font-semibold" : ""}`}
          >
            V2方向与比分
          </Link>
          <Link to="/report" className={`hover:text-moss transition-colors ${isActive('/report')}`}>预测报告</Link>
          <Link to="/goals" className={`hover:text-moss transition-colors ${isActive('/goals')}`}>进球数预测</Link>
          <Link to="/review" className={`hover:text-moss transition-colors ${isActive('/review')}`}>复盘统计</Link>
          <SystemMenu />
        </div>
        <div className="flex gap-3 items-center text-xs font-body text-ink-muted">
          <span>数据更新: --</span>
          <span className="w-2 h-2 bg-moss rounded-full" />
          <span>模型 v0.1.0</span>
        </div>
      </nav>
      <main>
        <Routes>
          <Route path="/" element={<MarketFlowV2 />} />
          <Route path="/market-flow" element={<MarketFlowV2 />} />
          <Route path="/dashboard" element={<Dashboard />} />
          <Route path="/match/:id" element={<MatchDetail />} />
          <Route path="/report" element={<Report />} />
          <Route path="/review" element={<Review />} />
          <Route path="/goals" element={<GoalsPrediction />} />
          <Route path="/mapping" element={<Mapping />} />
          <Route path="/admin" element={<Admin />} />
        </Routes>
      </main>
    </div>
  );
}

function SystemMenu() {
  const [open, setOpen] = useState(false);
  let timer: ReturnType<typeof setTimeout> | null = null;

  const handleEnter = () => {
    if (timer) { clearTimeout(timer); timer = null; }
    setOpen(true);
  };
  const handleLeave = () => {
    timer = setTimeout(() => setOpen(false), 150);
  };

  return (
    <div
      className="relative"
      onMouseEnter={handleEnter}
      onMouseLeave={handleLeave}
    >
      <span className="cursor-pointer hover:text-moss transition-colors flex items-center gap-0.5">
        系统管理 <span className="text-xs">▾</span>
      </span>
      {open && (
        <div
          className="absolute top-full left-0 pt-1 z-50"
          onMouseEnter={handleEnter}
          onMouseLeave={handleLeave}
        >
          <div className="bg-white border border-border rounded-md shadow-lg py-1 min-w-[140px]">
            <Link
              to="/admin"
              className="block px-4 py-2 text-xs hover:bg-highlight transition-colors text-ink"
              onClick={() => setOpen(false)}
            >
              任务监控
            </Link>
            <Link
              to="/mapping"
              className="block px-4 py-2 text-xs hover:bg-highlight transition-colors text-ink"
              onClick={() => setOpen(false)}
            >
              名称映射管理
            </Link>
          </div>
        </div>
      )}
    </div>
  );
}

export default App;
