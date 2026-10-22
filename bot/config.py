import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    token: str
    max_file_mb: int = 50
    local_api_url: str | None = None
    allowed_users: frozenset[int] = frozenset()
    cookies_file: str | None = None
    max_concurrent: int = 3
    data_dir: str = "data"
    temp_dir: str | None = None

    @property
    def max_bytes(self) -> int:
        return self.max_file_mb * 1024 * 1024

    def is_allowed(self, user_id: int) -> bool:
        return not self.allowed_users or user_id in self.allowed_users


def load_config() -> Config:
    load_dotenv()
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("BOT_TOKEN не задан (см. .env.example)")
    allowed = frozenset(
        int(x) for x in os.getenv("ALLOWED_USERS", "").replace(" ", "").split(",") if x
    )
    return Config(
        token=token,
        max_file_mb=int(os.getenv("MAX_FILE_MB", "50")),
        local_api_url=os.getenv("LOCAL_API_URL") or None,
        allowed_users=allowed,
        cookies_file=os.getenv("COOKIES_FILE") or None,
        max_concurrent=int(os.getenv("MAX_CONCURRENT", "3")),
        data_dir=os.getenv("DATA_DIR", "data"),
        temp_dir=os.getenv("TEMP_DIR") or None,
    )
