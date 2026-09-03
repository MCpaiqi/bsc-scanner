from redis.asyncio import Redis
from redis.exceptions import RedisError

from smms.config.settings import Settings, get_settings


async def check_redis(settings: Settings | None = None) -> str:
    client: Redis | None = None
    try:
        active = settings or get_settings()
        client = Redis.from_url(active.redis_url, socket_connect_timeout=1, socket_timeout=1)
        await client.ping()
        return "ok"
    except (ModuleNotFoundError, OSError, RedisError):
        return "unavailable"
    finally:
        if client is not None:
            await client.aclose()

