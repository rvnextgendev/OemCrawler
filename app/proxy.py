"""mitmproxy hooks: centralized origin, address, pacing and blocked-response policy."""
import asyncio
import json
import socket
from urllib.parse import urlsplit
from app.site_profiles import profiles, normalize, same_origin
from mitmproxy import http
from app.common import r, pace
from app.policy import allowed_proxy_url, public_addresses, blocked

class PolicyProxy:
    async def request(self, flow: http.HTTPFlow):
        host = flow.request.host
        permitted = allowed_proxy_url(flow.request.pretty_url)
        dynamic = None
        if not permitted:
            raw = await r.get("public-origin:" + host)
            dynamic = json.loads(raw) if raw else None
            permitted = bool(dynamic and (normalize(flow.request.pretty_url,dynamic) or
                (same_origin(flow.request.pretty_url,dynamic) and urlsplit(flow.request.pretty_url).path == "/robots.txt")))
        if not permitted:
            flow.response = http.Response.make(403, b"Origin denied")
            return
        if host != "mock_oem":
            infos = await asyncio.to_thread(socket.getaddrinfo,
                    flow.request.host, flow.request.port, type=socket.SOCK_STREAM)
            if not public_addresses([info[4][0] for info in infos]):
                flow.response = http.Response.make(403, b"Address denied")
                return
        if await r.exists("blocked:" + host):
            flow.response = http.Response.make(531, b"Domain paused")
            return
        floor = max([float(p.get("pace_seconds",2)) for p in profiles().values() if urlsplit(p["origin"]).hostname == host] or [10 if dynamic else 2])
        await pace("egress:" + host, minimum=floor)

    async def response(self, flow: http.HTTPFlow):
        host = flow.request.host
        if blocked(flow.response.status_code, flow.response.get_text(strict=False)[:2000000]):
            await r.set("blocked:" + host, "1", ex=300)
            # 531 is private to this stack, not a standard HTTP status.
            flow.response = http.Response.make(531, b"Upstream blocked; review required",
                                                {"content-type": "text/plain"})

addons = [PolicyProxy()]
