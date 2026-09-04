from datetime import datetime

from smms.db.models import TokenLifecycleEvent, TokenStatus

_STATUS_BY_EVENT = {
    "CREATED": TokenStatus.NEW.value,
    "TRADING_STARTED": TokenStatus.TRADING.value,
    "NEAR_GRADUATION": TokenStatus.NEAR_GRADUATION.value,
    "GRADUATED": TokenStatus.GRADUATED.value,
    "DEX_LISTED": TokenStatus.DEX.value,
}


def lifecycle_status(event_type: str) -> str:
    """Map known lifecycle events to a status without inventing unknown states."""

    return _STATUS_BY_EVENT.get(event_type.upper(), TokenStatus.UNKNOWN.value)


def apply_lifecycle_event(
    status: str,
    event_type: str,
    *,
    event: TokenLifecycleEvent | None = None,
    observed_at: datetime | None = None,
) -> str:
    """Apply a monotonic known transition; unknown events leave the current status intact."""

    del event, observed_at
    next_status = lifecycle_status(event_type)
    if next_status == TokenStatus.UNKNOWN.value:
        return status
    order = {
        TokenStatus.UNKNOWN.value: -1,
        TokenStatus.NEW.value: 0,
        TokenStatus.TRADING.value: 1,
        TokenStatus.NEAR_GRADUATION.value: 2,
        TokenStatus.GRADUATED.value: 3,
        TokenStatus.DEX.value: 4,
    }
    return next_status if order[next_status] >= order.get(status, -1) else status

