"""Whitelisted, nonfinancial worker-cycle summaries for cross-process health."""

from datetime import datetime
from math import isfinite

from smms.evaluation import utc

_ERRORS = {"DB_ERROR", "IO_ERROR", "INVALID_INPUT", "INTERNAL_ERROR", "CANCELLED"}
_PROFILES = {
    "forward-worker": ({"FORWARD", "ALPHA", "COMMIT", "IDLE"},
        ("seconds", "forward_seconds", "alpha_seconds", "commit_seconds"),
        ("forward_evaluated", "alpha_scored"), 2000),
    "research-rebuild": ({"REBUILD", "COMMIT", "IDLE"},
        ("seconds", "repair_seconds", "publication_lock_seconds", "rebuild_seconds", "commit_seconds"),
        ("jobs_examined", "jobs_completed", "jobs_deferred", "jobs_unsupported", "jobs_stale", "jobs_superseded"), 256),
}


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


def cycle_summary(value, *, worker_type="forward-worker"):
    if not isinstance(value, dict) or not isinstance(worker_type, str) or worker_type not in _PROFILES:
        return None
    phases, durations, counts, maximum = _PROFILES[worker_type]
    phase, error = value.get("phase"), value.get("error_code")
    if not isinstance(phase, str) or phase not in phases or type(value.get("committed")) is not bool:
        return None
    committed = value["committed"]
    result = {"phase": value["phase"], "committed": committed,
              **{key: _number(value.get(key)) for key in durations},
              **{key: _timestamp(value.get(key)) for key in ("started_at", "finished_at", "evaluation_as_of")},
              "error_code": error if isinstance(error, str) and error in _ERRORS else None}
    for key in counts:
        number = value.get(key)
        result[key] = number if committed and type(number) is int and 0 <= number <= maximum else None
    return result


def research_worker_health(details, *, worker_type="forward-worker"):
    if not isinstance(details, dict):
        return None
    current = cycle_summary(details.get("research_cycle"), worker_type=worker_type)
    previous = cycle_summary(details.get("last_committed_research_cycle"), worker_type=worker_type)
    if previous is not None and not previous["committed"]:
        previous = None
    return {"cycle": current, "last_committed_cycle": previous} if current is not None or previous is not None else None
