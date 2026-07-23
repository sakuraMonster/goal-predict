import { BrowserRouter, Routes, Route, Link } from "react-router-dom";
import Dashboard from "./pages/Dashboard";
import MatchDetail from "./pages/MatchDetail";
import Report from "./pages/Report";
import Review from "./pages/Review";
import Mapping from "./pages/Mapping";
import Admin from "./pages/Admin";

function App() {
  return (
    <BrowserRouter>
      <div className="min-h-screen bg-parchment">
        <nav className="bg-parchment-dark border-b border-border-dark px-6 py-3 flex items-center justify-between">
          <Link to="/" className="text-base font-bold tracking-wider font-heading text-ink">
            竞彩预测系统
          </Link>
          <div className="flex gap-7 text-sm font-body text-ink-muted">
            <Link to="/" className="hover:text-moss transition-colors">赛事预测</Link>
            <Link to="/report" className="hover:text-moss transition-colors">预测报告</Link>
            <Link to="/review" className="hover:text-moss transition-colors">复盘统计</Link>
            <Link to="/admin" className="hover:text-moss transition-colors">系统管理</Link>
          </div>
          <div className="flex gap-3 items-center text-xs font-body text-ink-muted">
            <span>数据更新: --</span>
            <span className="w-2 h-2 bg-moss rounded-full" />
            <span>模型 v0.1.0</span>
          </div>
        </nav>
        <main>
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/match/:id" element={<MatchDetail />} />
            <Route path="/report" element={<Report />} />
            <Route path="/review" element={<Review />} />
            <Route path="/mapping" element={<Mapping />} />
            <Route path="/admin" element={<Admin />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  );
}

export default App;
