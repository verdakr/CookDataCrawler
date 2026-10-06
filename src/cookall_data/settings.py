from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


load_dotenv()


@dataclass(frozen=True)
class Settings:
    mongodb_uri: str
    db_name: str
    admin_username: str
    admin_password_hash: str
    auth_secret: str
    admin_origin: str
    cookie_secure: bool
    backup_dir: Path

    @classmethod
    def from_env(cls, *, require_auth: bool = True) -> "Settings":
        required = ("MONGODB_URI", "DB_NAME")
        missing = [name for name in required if not os.getenv(name)]
        if require_auth:
            missing.extend(
                name
                for name in ("ADMIN_USERNAME", "ADMIN_PASSWORD_HASH", "AUTH_SECRET")
                if not os.getenv(name)
            )
        if missing:
            raise RuntimeError(f"Missing required environment variables: {', '.join(sorted(set(missing)))}")
        return cls(
            mongodb_uri=os.environ["MONGODB_URI"],
            db_name=os.environ["DB_NAME"],
            admin_username=os.getenv("ADMIN_USERNAME", "admin"),
            admin_password_hash=os.getenv("ADMIN_PASSWORD_HASH", ""),
            auth_secret=os.getenv("AUTH_SECRET", "development-only-change-me"),
            admin_origin=os.getenv("ADMIN_ORIGIN", "http://localhost:3000").rstrip("/"),
            cookie_secure=os.getenv("COOKIE_SECURE", "false").lower() == "true",
            backup_dir=Path(os.getenv("BACKUP_DIR", "artifacts/backups")),
        )
