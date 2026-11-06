"""Versioned live research policy, independent of runtime and database code."""

from datetime import timedelta
from decimal import Decimal
from hashlib import sha256

SCORE_VERSION = "experimental-v3"
LABEL_VERSION = "alpha-shadow-v2"
SNAPSHOT_WINDOWS = (30, 60, 180, 300)
TRACKING_HORIZON = timedelta(hours=1)
COHORT_SECONDS = 60
HIGH_SCORE = 70


def policy_id(minimum_quote: Decimal) -> str:
    if not minimum_quote.is_finite() or minimum_quote <= 0:
        raise ValueError("minimum quote must be finite and positive")
    return sha256(f"{LABEL_VERSION}:{minimum_quote.normalize()}:vwap-30s".encode()).hexdigest()
