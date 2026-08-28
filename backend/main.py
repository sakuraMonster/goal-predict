from dotenv import load_dotenv
load_dotenv()

import io
import os
import sys
import time
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from app.db.logger import AppLogger

# Windows PowerShell 5 强制 UTF-8，避免中文字符 GBK 乱码
if sys.platform == "win32":
    try:
        import locale
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
            sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
        except Exception:
            _buf = getattr(sys.stdout, "buffer", sys.stdout)
            sys.stdout = io.TextIOWrapper(_buf, encoding="utf-8", errors="backslashreplace", line_buffering=True)
            _ebuf = getattr(sys.stderr, "buffer", sys.stderr)
            sys.stderr = io.TextIOWrapper(_ebuf, encoding="utf-8", errors="backslashreplace", line_buffering=True)
        os.environ.setdefault("PYTHONIOENCODING", "utf-8")
        os.environ.setdefault("PYTHONUTF8", "1")
    except Exception:
        pass

app = FastAPI(title="竞彩足球预测系统", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def enforce_json_charset_utf8(request: Request, call_next):
    """对 application/json 响应强制追加 ; charset=utf-8，规避 PowerShell 5 默认用 GBK 解码产生乱码"""
    response = await call_next(request)
    ct = response.headers.get("content-type") or ""
    if ct.lower().startswith("application/json") and "charset" not in ct.lower():
        response.headers["content-type"] = ct.strip() + "; charset=utf-8"
    return response


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """记录所有 HTTP 请求到控制台和数据库"""
    start = time.time()
    response = await call_next(request)
    duration_ms = int((time.time() - start) * 1000)

    path = request.url.path
    method = request.method
    status = response.status_code

    # 跳过静态资源和健康检查
    if not path.startswith("/api") or path == "/api/health":
        return response

    # 禁用浏览器缓存，确保统计数据等 API 数据实时更新
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"

    AppLogger.info("api", f"{method} {path} → {status} ({duration_ms}ms)")
    # 异步写数据库，不阻塞响应
    try:
        await AppLogger.log_api(method, path, status, duration_ms)
    except Exception:
        pass

    return response


@app.get("/api/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}

from app.api import matches, predictions, reports, admin, mappings, teams, market_flow

app.include_router(matches.router)
app.include_router(predictions.router)
app.include_router(reports.router)
app.include_router(admin.router)
app.include_router(mappings.router)
app.include_router(teams.router)
app.include_router(market_flow.router)


@app.on_event("startup")
async def startup_event():
    from app.scheduler import init_scheduler
    init_scheduler()
    AppLogger.info("system", "定时任务调度器已启动")


AppLogger.info("system", "竞彩足球预测系统启动完成")
