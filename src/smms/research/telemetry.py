"""Whitelisted, nonfinancial worker-cycle summaries for cross-process health."""

from datetime import datetime
from math import isfinite

from smms.evaluation import utc

_PHASES = {"FORWARD", "ALPHA", "COMMIT", "IDLE"}
_ERRORS = {"DB_ERROR", "IO_ERROR", "INVALID_INPUT", "INTERNAL_ERROR", "CANCELLED"}


def _number(value):
    if type(value) not in (int, float):
        return None
    try:
        return float(value) if isfinite(value) and value >= 0 else None
    except OverflowError:
        return None


def _timestamp(value):
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        parsed = datetime.fromisoformat(value)
        return utc(parsed).isoformat() if parsed.tzinfo is not None else None
    except (ValueError, OverflowError):
        return None


def cycle_summary(value):
    if not isinstance(value, dict):
        return None
    phase, error = value.get("phase"), value.get("error_code")
    if not isinstance(phase, str) or phase not in _PHASES or type(value.get("committed")) is not bool:
        return None
    committed = value["committed"]
    result = {"phase": value["phase"], "committed": committed,
              **{key: _number(value.get(key)) for key in ("seconds", "forward_seconds", "alpha_seconds", "commit_seconds")},
              **{key: _timestamp(value.get(key)) for key in ("started_at", "finished_at", "evaluation_as_of")},
              "error_code": error if isinstance(error, str) and error in _ERRORS else None}
    for key in ("forward_evaluated", "alpha_scored"):
        number = value.get(key)
        result[key] = number if committed and type(number) is int and 0 <= number <= 2000 else None
    return result


def research_worker_health(details):
    if not isinstance(details, dict):
        return None
    current = cycle_summary(details.get("research_cycle"))
    previous = cycle_summary(details.get("last_committed_research_cycle"))
    if previous is not None and not previous["committed"]:
        previous = None
    return {"cycle": current, "last_committed_cycle": previous} if current is not None or previous is not None else None
