"""Create local demo secrets once; do not commit .env."""
import secrets
import sys
from pathlib import Path
from cryptography.fernet import Fernet
path = Path(__file__).with_name(".env")
if path.exists():
    if "--demo" not in sys.argv and "--secure" not in sys.argv:
        raise SystemExit(".env exists; keeping your existing configuration. Use --demo or --secure to change mode.")
    lines = [line for line in path.read_text().splitlines() if not line.startswith(("DEMO_MODE=", "ALLOW_PUBLIC_URLS="))]
    if "--demo" in sys.argv:
        for key in ("QUOTES_USER", "QUOTES_PASSWORD"):
            if not any(line.startswith(key + "=") for line in lines):
                lines.append(key + "=demo")
    lines.append("ALLOW_PUBLIC_URLS=" + ("true" if "--demo" in sys.argv else "false"))
    lines.append("DEMO_MODE=" + ("true" if "--demo" in sys.argv else "false"))
    path.write_text("\n".join(lines) + "\n")
    raise SystemExit("Updated mode only; existing secrets preserved. Recreate services to apply.")
path.write_text("\n".join([
    "ALLOW_PUBLIC_URLS=" + ("true" if "--demo" in sys.argv else "false"),
    "DEMO_MODE=" + ("true" if "--demo" in sys.argv else "false"),
    "API_KEY=" + secrets.token_urlsafe(32),
    "JWT_SECRET=" + secrets.token_urlsafe(48),
    "INTERNAL_SECRET=" + secrets.token_urlsafe(32),
    "SESSION_KEY=" + Fernet.generate_key().decode(),
    "APP_USER=demo", "APP_PASSWORD=" + secrets.token_urlsafe(18),
    "QUOTES_USER=demo", "QUOTES_PASSWORD=demo",
    "OEM_USER=demo-oem", "OEM_PASSWORD=" + secrets.token_urlsafe(18),
    "REDIS_PASSWORD=" + secrets.token_urlsafe(32),
]) + "\n")
print("Created .env with generated local demo credentials")
