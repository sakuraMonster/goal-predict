from dotenv import load_dotenv
load_dotenv()

import time
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from app.db.logger import AppLogger

app = FastAPI(title="竞彩足球预测系统", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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
