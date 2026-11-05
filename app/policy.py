"""One allowlisted origin for the demo. Never accept arbitrary browser scripts."""
import ipaddress
import os
from datetime import datetime, timezone
from urllib.parse import urlsplit

MOCK = "mock_oem"
ALLOWED_HOST = os.getenv("OEM_HOST", MOCK)
ALLOWED_PORT = int(os.getenv("OEM_PORT", "8003"))
START = int(os.getenv("WINDOW_START_UTC", "0"))
END = int(os.getenv("WINDOW_END_UTC", "24"))

def allowed_url(url: str) -> bool:
    try:
        u = urlsplit(url)
        scheme = "http" if ALLOWED_HOST == MOCK else "https"
        port = u.port or (443 if u.scheme == "https" else 80)
        return (u.scheme == scheme and u.hostname == ALLOWED_HOST
                and port == ALLOWED_PORT and not u.username and not u.password
                and not u.fragment and len(url) <= 2048)
    except ValueError:
        return False

def public_addresses(addresses):
    return bool(addresses) and all(ipaddress.ip_address(a).is_global for a in addresses)

def in_window(hour=None):
    hour = datetime.now(timezone.utc).hour if hour is None else hour
    return START <= hour < END if START < END else hour >= START or hour < END

def blocked(status, text):
    return status in (401, 403, 429, 531) or any(
        marker in text.lower() for marker in ("captcha", "verify you are human", "access denied"))
