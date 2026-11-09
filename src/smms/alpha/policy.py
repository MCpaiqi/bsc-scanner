"""Versioned live research policy, independent of runtime and database code."""

from datetime import timedelta
from decimal import Decimal
from hashlib import sha256

from .vwap import VWAP_ARITHMETIC

SCORE_VERSION = "experimental-v4"
LABEL_VERSION = "alpha-shadow-v3"
SNAPSHOT_WINDOWS = (30, 60, 180, 300)
TRACKING_HORIZON = timedelta(hours=1)
COHORT_SECONDS = 60
HIGH_SCORE = 70


def policy_id(minimum_quote: Decimal) -> str:
    if not minimum_quote.is_finite() or minimum_quote <= 0:
        raise ValueError("minimum quote must be finite and positive")
    quote = format(minimum_quote, "f")
    quote = quote.rstrip("0").rstrip(".") if "." in quote else quote
    return sha256(f"{LABEL_VERSION}:{quote}:{VWAP_ARITHMETIC}:vwap-30s".encode()).hexdigest()
