"""Create local demo secrets once; do not commit .env."""
import secrets
from pathlib import Path
from cryptography.fernet import Fernet
path = Path(__file__).with_name(".env")
if path.exists():
    raise SystemExit(".env exists; keeping your existing configuration")
path.write_text("\n".join([
    "API_KEY=" + secrets.token_urlsafe(32),
    "JWT_SECRET=" + secrets.token_urlsafe(48),
    "INTERNAL_SECRET=" + secrets.token_urlsafe(32),
    "SESSION_KEY=" + Fernet.generate_key().decode(),
    "APP_USER=demo", "APP_PASSWORD=" + secrets.token_urlsafe(18),
    "OEM_USER=demo-oem", "OEM_PASSWORD=" + secrets.token_urlsafe(18),
    "REDIS_PASSWORD=" + secrets.token_urlsafe(32),
]) + "\n")
print("Created .env with generated local demo credentials")
