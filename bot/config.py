import logging
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
    pot_provider_url: str | None = None

    @property
    def max_bytes(self) -> int:
        return self.max_file_mb * 1024 * 1024

    def is_allowed(self, user_id: int) -> bool:
        return not self.allowed_users or user_id in self.allowed_users


CLOUD_LIMIT_MB = 50  # лимит облачного Bot API на отправку файлов ботом
LOCAL_LIMIT_MB = 2000  # лимит локального Bot API сервера


def _file_limit(requested: int, local: bool) -> int:
    cap = LOCAL_LIMIT_MB if local else CLOUD_LIMIT_MB
    if requested > cap:
        hint = "" if local else " — для больших файлов нужен локальный Bot API (LOCAL_API_URL, см. README)"
        logging.getLogger(__name__).warning("MAX_FILE_MB=%d больше лимита %d МБ, использую %d%s",
                                            requested, cap, cap, hint)
        return cap
    return requested


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
        max_file_mb=_file_limit(int(os.getenv("MAX_FILE_MB", "50")), bool(os.getenv("LOCAL_API_URL"))),
        local_api_url=os.getenv("LOCAL_API_URL") or None,
        allowed_users=allowed,
        cookies_file=os.getenv("COOKIES_FILE") or None,
        max_concurrent=int(os.getenv("MAX_CONCURRENT", "3")),
        data_dir=os.getenv("DATA_DIR", "data"),
        temp_dir=os.getenv("TEMP_DIR") or None,
        pot_provider_url=os.getenv("POT_PROVIDER_URL") or None,
    )
