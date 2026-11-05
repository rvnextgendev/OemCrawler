"""mitmproxy hooks: centralized origin, address, pacing and blocked-response policy."""
import asyncio
import socket
from mitmproxy import http
from app.common import r, pace
from app.policy import allowed_url, public_addresses, blocked, ALLOWED_HOST

class PolicyProxy:
    async def request(self, flow: http.HTTPFlow):
        if not allowed_url(flow.request.pretty_url):
            flow.response = http.Response.make(403, b"Origin denied")
            return
        if ALLOWED_HOST != "mock_oem":
            infos = await asyncio.to_thread(socket.getaddrinfo,
                    flow.request.host, flow.request.port, type=socket.SOCK_STREAM)
            if not public_addresses([info[4][0] for info in infos]):
                flow.response = http.Response.make(403, b"Address denied")
                return
        if await r.exists("blocked:" + ALLOWED_HOST):
            flow.response = http.Response.make(531, b"Domain paused")
            return
        await pace("egress:" + ALLOWED_HOST)

    async def response(self, flow: http.HTTPFlow):
        if blocked(flow.response.status_code, flow.response.get_text(strict=False)[:2000000]):
            await r.set("blocked:" + ALLOWED_HOST, "1", ex=300)
            # 531 is private to this stack, not a standard HTTP status.
            flow.response = http.Response.make(531, b"Upstream blocked; review required",
                                                {"content-type": "text/plain"})

addons = [PolicyProxy()]
