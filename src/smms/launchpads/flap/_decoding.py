"""Conservative helpers for decoding configured EVM log fields."""

from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from smms.chains.base import ChainLog
from smms.ingestion import DataUnavailable


def topic_key(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DataUnavailable("Flap event topic is missing")
    return value.strip().lower()


def address_key(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DataUnavailable("Flap contract address is missing")
    return value.strip().lower()


def address_from_topic(value: str) -> str:
    """Decode an exact 32-byte ABI address topic."""

    if not isinstance(value, str) or not value.strip():
        raise DataUnavailable("Flap address topic is missing")
    body = value.strip()
    if body.startswith(("0x", "0X")):
        body = body[2:]
    if len(body) != 64:
        raise DataUnavailable("Flap address topic must be a 32-byte ABI word")
    try:
        int(body, 16)
    except ValueError as exc:
        raise DataUnavailable("Flap address topic is not hexadecimal") from exc
    return "0x" + body[-40:]


def data_words(data: str) -> list[str]:
    if not isinstance(data, str):
        raise DataUnavailable("Flap log data is invalid")
    body = data.strip()
    if body.startswith(("0x", "0X")):
        body = body[2:]
    if not body:
        return []
    if len(body) % 64 != 0:
        raise DataUnavailable("Flap log data is not ABI-word aligned")
    try:
        int(body, 16)
    except ValueError as exc:
        raise DataUnavailable("Flap log data is not hexadecimal") from exc
    return [body[offset : offset + 64] for offset in range(0, len(body), 64)]


def word_at(words: Sequence[str], index: int | None, *, field: str) -> int | None:
    if index is None:
        return None
    if index < 0 or index >= len(words):
        raise DataUnavailable(f"Flap {field} data index is out of range")
    try:
        return int(words[index], 16)
    except (TypeError, ValueError) as exc:
        raise DataUnavailable(f"Flap {field} data word is invalid") from exc


def topic_at(topics: Sequence[str], index: int | None, *, field: str) -> str | None:
    if index is None:
        return None
    if index < 0 or index >= len(topics):
        raise DataUnavailable(f"Flap {field} topic index is out of range")
    value = topics[index]
    if not isinstance(value, str) or not value.strip():
        raise DataUnavailable(f"Flap {field} topic is invalid")
    return value


def address_field(
    log: ChainLog,
    *,
    topic_index: int | None,
    data_index: int | None,
    field: str,
) -> str | None:
    if topic_index is not None and data_index is not None:
        raise DataUnavailable(f"Flap {field} has both topic and data selectors")
    topic = topic_at(log.topics, topic_index, field=field)
    if topic is not None:
        return address_from_topic(topic)
    words = data_words(log.data)
    raw = word_at(words, data_index, field=field)
    if raw is None:
        return None
    return address_from_topic(f"0x{raw:064x}")


def integer_field(
    log: ChainLog,
    *,
    topic_index: int | None,
    data_index: int | None,
    field: str,
) -> int | None:
    if topic_index is not None and data_index is not None:
        raise DataUnavailable(f"Flap {field} has both topic and data selectors")
    topic = topic_at(log.topics, topic_index, field=field)
    if topic is not None:
        body = topic[2:] if topic.lower().startswith("0x") else topic
        try:
            value = int(body, 16)
        except ValueError as exc:
            raise DataUnavailable(f"Flap {field} topic is not hexadecimal") from exc
        return value
    return word_at(data_words(log.data), data_index, field=field)


def integer_selector(
    log: ChainLog,
    *,
    topic_index: int | None,
    data_index: int | None,
    field: str,
) -> int | None:
    """Decode a numeric selector while enforcing a single configured source."""

    return integer_field(
        log,
        topic_index=topic_index,
        data_index=data_index,
        field=field,
    )


def scaled(value: int | None, decimals: int, *, field: str) -> Decimal | None:
    if value is None:
        return None
    if value < 0:
        raise DataUnavailable(f"Flap {field} cannot be negative")
    return Decimal(value) / (Decimal(10) ** decimals)


def decimal_value(value: Any, *, field: str) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise DataUnavailable(f"Flap {field} is not numeric")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise DataUnavailable(f"Flap {field} is not numeric") from exc
    if not result.is_finite() or result < 0:
        raise DataUnavailable(f"Flap {field} is invalid")
    return result


def utc_timestamp(value: datetime | None, *, field: str = "timestamp") -> datetime:
    if not isinstance(value, datetime):
        raise DataUnavailable(f"Flap {field} is unavailable")
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def log_matches(log: ChainLog, *, address: str | None, topic: str | None) -> bool:
    if address is not None and address_key(log.address) != address_key(address):
        return False
    if topic is None or not log.topics:
        return False
    return topic_key(log.topics[0]) == topic_key(topic)


def raw_log(log: ChainLog) -> dict[str, Any]:
    return {
        "address": log.address,
        "topics": list(log.topics),
        "data": log.data,
        "block_number": log.block_number,
        "transaction_hash": log.transaction_hash,
        "log_index": log.log_index,
        **(log.raw if isinstance(log.raw, dict) else {}),
    }


__all__ = [
    "address_field",
    "address_from_topic",
    "address_key",
    "data_words",
    "decimal_value",
    "integer_field",
    "integer_selector",
    "log_matches",
    "raw_log",
    "scaled",
    "topic_at",
    "topic_key",
    "utc_timestamp",
    "word_at",
]
