"""
Redis 连接管理（分布式锁 + 缓存）
使用外部 Redis: 172.17.2.84:6379 db=2
"""
import json
import os
import redis.asyncio as redis

REDIS_URL = os.getenv(
    "REDIS_URL",
    "redis://:hdpos4@172.17.2.84:6379/2"
)

# 统计类接口缓存 TTL（秒）。写入端（预测/赔率同步）会 bump 全局版本号立即失效缓存，
# 该 TTL 主要兜底「非 API 路径的外部写入」（如定时任务回写比赛结果）。
MARKET_FLOW_CACHE_TTL = int(os.getenv("MARKET_FLOW_CACHE_TTL", "60"))
# 全局统计缓存版本号 key：写接口每次自增，缓存 key 带版本 → 写后所有统计缓存自动失效
_CACHE_VERSION_KEY = "mf:stats:version"

_redis_client: redis.Redis | None = None


async def get_redis() -> redis.Redis:
    """获取 Redis 连接（单例）"""
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.from_url(REDIS_URL, decode_responses=True, protocol=2)
    return _redis_client


async def cache_version() -> int:
    """当前统计缓存全局版本号（Redis 不可用时恒为 0，缓存 key 一致但读写都降级为空操作）"""
    try:
        r = await get_redis()
        v = await r.get(_CACHE_VERSION_KEY)
        return int(v) if v else 0
    except Exception:
        return 0


async def cache_bump_version() -> None:
    """统计数据被写入（预测/赔率同步/结果回写）后调用，使全部统计缓存立即失效"""
    try:
        r = await get_redis()
        await r.incr(_CACHE_VERSION_KEY)
    except Exception:
        pass


async def cache_get_json(key: str):
    """读缓存并反序列化；未命中 / Redis 异常 → None（调用方按未命中降级）"""
    try:
        r = await get_redis()
        raw = await r.get(key)
        return json.loads(raw) if raw else None
    except Exception:
        return None


async def cache_set_json(key: str, value, ttl: int = MARKET_FLOW_CACHE_TTL) -> None:
    """写入缓存；Redis 异常时静默降级（不阻断主流程）"""
    try:
        r = await get_redis()
        await r.set(key, json.dumps(value, ensure_ascii=False, default=str), ex=ttl)
    except Exception:
        pass


async def acquire_lock(lock_key: str, expire_seconds: int = 600) -> bool:
    """获取分布式锁，返回是否获取成功"""
    r = await get_redis()
    return await r.set(lock_key, "1", nx=True, ex=expire_seconds)


async def release_lock(lock_key: str):
    """释放分布式锁"""
    r = await get_redis()
    await r.delete(lock_key)
