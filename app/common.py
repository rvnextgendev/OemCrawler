import hashlib
import json
import os
import time
import uuid
import jwt
from cryptography.fernet import Fernet
from fastapi import Header, HTTPException
from redis.asyncio import Redis

r = Redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
SECRET = os.environ["JWT_SECRET"]
INTERNAL = os.environ["INTERNAL_SECRET"]
API_KEY = os.environ["API_KEY"]
TENANT = "demo"
fernet = Fernet(os.environ["SESSION_KEY"].encode())

def fingerprint(value):
    return hashlib.sha256(value.encode()).hexdigest()

async def internal(x_internal_secret: str = Header(default="")):
    import hmac
    if not hmac.compare_digest(x_internal_secret, INTERNAL):
        raise HTTPException(403, "Internal caller required")

async def principal(authorization: str = Header(default=""),
                    x_tenant: str = Header(default="")):
    try:
        token = authorization.removeprefix("Bearer ")
        claims = jwt.decode(token, SECRET, algorithms=["HS256"],
                            audience="oem-crawler", issuer="orchestrator",
                            options={"require": ["exp", "sub", "jti", "tenant", "role"]})
        if claims["tenant"] != x_tenant or not await r.exists("jwt:" + claims["jti"]):
            raise ValueError()
        return claims
    except (jwt.PyJWTError, ValueError):
        raise HTTPException(401, "Invalid or expired token")

async def audit(event, **fields):
    # Do not include credentials, tokens, query strings or page content.
    await r.xadd("audit", {"event": event, "time": str(time.time()),
                           "fields": json.dumps(fields)}, maxlen=10000)

# Atomic shared pacing slot; host-wide across accounts and workers.
PACE = """
local next = tonumber(redis.call('GET', KEYS[1]) or '0')
local now = tonumber(ARGV[1])
local slot = math.max(now, next)
redis.call('SET', KEYS[1], slot + tonumber(ARGV[2]), 'PX', 120000)
return tostring(slot - now)
"""
async def pace(host):
    import asyncio
    delay = float(await r.eval(PACE, 1, "pace:" + host,
                               time.time(), float(os.getenv("PACE_SECONDS", "2"))))
    await asyncio.sleep(delay)

async def init_key():
    await r.set("key:" + fingerprint(API_KEY), TENANT)
