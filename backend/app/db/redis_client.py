"""
Redis 连接管理（分布式锁 + 缓存）
使用外部 Redis: 172.17.2.84:6379 db=2
"""
import os
import redis.asyncio as redis

REDIS_URL = os.getenv(
    "REDIS_URL",
    "redis://:hdpos4@172.17.2.84:6379/2"
)

_redis_client: redis.Redis | None = None


async def get_redis() -> redis.Redis:
    """获取 Redis 连接（单例）"""
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.from_url(REDIS_URL, decode_responses=True)
    return _redis_client


async def acquire_lock(lock_key: str, expire_seconds: int = 600) -> bool:
    """获取分布式锁，返回是否获取成功"""
    r = await get_redis()
    return await r.set(lock_key, "1", nx=True, ex=expire_seconds)


async def release_lock(lock_key: str):
    """释放分布式锁"""
    r = await get_redis()
    await r.delete(lock_key)
