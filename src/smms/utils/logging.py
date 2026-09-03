import json
import logging
from typing import Any

from smms.config.settings import Settings

_SECRET_FIELDS = {"trading_wallet_private_key", "telegram_bot_token", "market_api_key", "dex_api_key"}


def _sanitize(value: Any, key: str | None = None) -> Any:
    if key and key.lower() in _SECRET_FIELDS:
        return "[REDACTED]"
    if isinstance(value, dict):
        return {k: _sanitize(v, k) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize(item) for item in value]
    return value


class StructuredFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "level": record.levelname,
            "message": record.getMessage(),
            "context": _sanitize(getattr(record, "context", {})),
        }
        return json.dumps(payload, ensure_ascii=True)


def configure_logging(settings: Settings) -> logging.Logger:
    logger = logging.getLogger("smms")
    logger.setLevel(settings.log_level.upper())
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(StructuredFormatter())
        logger.addHandler(handler)
    logger.propagate = False
    return logger

