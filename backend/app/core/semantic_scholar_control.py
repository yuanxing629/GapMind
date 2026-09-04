"""Semantic Scholar 请求的共享限流和缓存。"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from typing import Any

import redis

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)
_redis_client: redis.Redis | None = None
_local_lock = threading.Lock()
_local_next_slot = 0.0


def get_redis_client() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
    return _redis_client


def wait_for_request_slot() -> None:
    """按配置的时间间隔协调 Semantic Scholar 请求。"""
    global _local_next_slot
    interval = max(0.1, settings.semantic_scholar_rate_interval)
    client = get_redis_client()
    for _ in range(200):
        try:
            if client.set(
                "gapmind:s2:request-slot",
                str(time.time()),
                nx=True,
                px=max(1000, int(interval * 1000)),
            ):
                return
            ttl_ms = int(client.pttl("gapmind:s2:request-slot"))
            wait_seconds = max(0.05, min(0.25, ttl_ms / 1000 if ttl_ms > 0 else interval))
        except redis.RedisError:
            with _local_lock:
                now = time.monotonic()
                wait_seconds = max(0.0, _local_next_slot - now)
                if wait_seconds == 0.0:
                    _local_next_slot = now + interval
                    return
        time.sleep(wait_seconds)
    raise RuntimeError("Semantic Scholar rate limiter timed out")


def search_cache_key(params: dict[str, Any]) -> str:
    encoded = json.dumps(params, sort_keys=True, separators=(",", ":"), default=str)
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    return f"gapmind:s2:search:v1:{digest}"


def read_search_cache(key: str) -> dict[str, Any] | None:
    try:
        raw = get_redis_client().get(key)
        if not raw:
            return None
        payload = json.loads(raw)
        return payload if isinstance(payload, dict) else None
    except (redis.RedisError, json.JSONDecodeError) as exc:
        logger.debug("semantic_scholar.cache_read_failed", error=str(exc))
        return None


def write_search_cache(key: str, payload: dict[str, Any]) -> None:
    try:
        get_redis_client().setex(
            key,
            max(1, settings.semantic_scholar_search_cache_ttl),
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        )
    except (redis.RedisError, TypeError) as exc:
        logger.debug("semantic_scholar.cache_write_failed", error=str(exc))
