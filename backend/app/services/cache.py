import json
import redis.asyncio as redis
from typing import Dict, Any
import os

# Initialize Redis client (typically configured centrally).
redis_client = redis.Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"))

async def get_revenue_summary(property_id: str, tenant_id: str) -> Dict[str, Any]:
    """
    Fetches revenue summary, utilizing caching to improve performance.
    """
    # Property IDs are only unique *within* a tenant (properties has a composite
    # primary key of (id, tenant_id)), so the tenant must be part of the cache key.
    # Without it, two tenants that share a property id - e.g. prop-001 belongs to
    # both tenant-a and tenant-b - read each other's cached revenue.
    if not tenant_id:
        raise ValueError("tenant_id is required to build a tenant-scoped cache key")

    cache_key = f"revenue:{tenant_id}:{property_id}"

    # Try to get from cache
    cached = await redis_client.get(cache_key)
    if cached:
        payload = json.loads(cached)
        # Defence in depth: never serve a cached payload that belongs to another
        # tenant, even if a key was written by an older/buggy build of this code.
        if payload.get("tenant_id") == tenant_id:
            return payload
        await redis_client.delete(cache_key)

    # Revenue calculation is delegated to the reservation service.
    from app.services.reservations import calculate_total_revenue
    
    # Calculate revenue
    result = await calculate_total_revenue(property_id, tenant_id)
    
    # Cache the result for 5 minutes
    await redis_client.setex(cache_key, 300, json.dumps(result))
    
    return result
