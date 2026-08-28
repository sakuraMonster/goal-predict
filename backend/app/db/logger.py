"""
应用日志模块：输出到控制台 + 写入 task_logs 表
"""
import logging
import sys
from datetime import datetime
from io import TextIOWrapper
from sqlalchemy import text
from app.db.database import async_session
from app.db.models import TaskLog

# 强制控制台 logger
console_logger = logging.getLogger("football_prediction")
console_logger.setLevel(logging.INFO)
console_logger.propagate = False  # 避免重复输出

# Windows/PowerShell 5 GBK 乱码强制绕开：直接用二进制buffer包 UTF-8 TextIOWrapper
_stdout_buffer = getattr(sys.stdout, "buffer", sys.stdout)
_utf8_stdout = TextIOWrapper(_stdout_buffer, encoding="utf-8", errors="backslashreplace", line_buffering=True)
handler = logging.StreamHandler(stream=_utf8_stdout)
handler.setFormatter(logging.Formatter(
    "[%(asctime)s] %(levelname)-5s | %(message)s",
    datefmt="%H:%M:%S",
))
console_logger.addHandler(handler)


async def log_to_db(task_type: str, status: str, message: str, duration_ms: int = 0):
    """写入数据库日志"""
    try:
        async with async_session() as db:
            log = TaskLog(
                task_type=task_type,
                status=status,
                start_time=datetime.utcnow(),
                end_time=datetime.utcnow(),
                duration_ms=duration_ms,
                message=message,
            )
            db.add(log)
            await db.commit()
    except Exception as e:
        console_logger.error(f"日志写入数据库失败: {e}")


class AppLogger:
    """应用日志封装，同时输出到控制台和数据库"""

    @staticmethod
    def info(task_type: str, message: str):
        console_logger.info(f"[{task_type}] {message}")

    @staticmethod
    def warning(task_type: str, message: str):
        console_logger.warning(f"[{task_type}] {message}")

    @staticmethod
    def error(task_type: str, message: str):
        console_logger.error(f"[{task_type}] {message}")

    @staticmethod
    async def log(task_type: str, status: str, message: str, duration_ms: int = 0):
        """同时输出到控制台和数据库"""
        if status == "success":
            console_logger.info(f"[{task_type}] ✓ {message}")
        elif status == "failed":
            console_logger.error(f"[{task_type}] ✗ {message}")
        else:
            console_logger.info(f"[{task_type}] {message}")
        await log_to_db(task_type, status, message, duration_ms)

    @staticmethod
    async def log_api(method: str, path: str, status_code: int, duration_ms: int):
        """记录 API 调用日志"""
        msg = f"{method} {path} → {status_code}"
        await log_to_db("api_request", "success" if status_code < 400 else "failed", msg, duration_ms)
