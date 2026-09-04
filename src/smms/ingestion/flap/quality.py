"""Data-quality summaries for a Flap historical backfill."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from smms.db.models import TokenLifecycleEvent, TradeEvent
from smms.launchpads.flap.verification import EvidenceLevel


@dataclass(frozen=True)
class FlapDataQualityReport:
    token_address: str
    creation_status: str
    first_trade: datetime | None
    last_trade: datetime | None
    buy_count: int
    sell_count: int
    unique_traders: int
    graduation: str
    migration: str
    missing_blocks: int
    missing_timestamps: int
    decode_errors: int
    unknown_events: int | None
    duplicate_events: int
    evidence_level: EvidenceLevel
    overall: str
    creator_address: str | None = None

    @classmethod
    def build(
        cls,
        *,
        token_address: str,
        creator_address: str | None = None,
        creation_verified: bool,
        trades: Sequence[TradeEvent],
        lifecycle: Sequence[TokenLifecycleEvent],
        decode_errors: int = 0,
        duplicate_events: int = 0,
        unknown_events: int | None = None,
    ) -> "FlapDataQualityReport":
        def side_value(event: TradeEvent) -> str:
            side = getattr(event.side, "value", event.side)
            return str(side).upper()

        buys = [event for event in trades if side_value(event) == "BUY"]
        sells = [event for event in trades if side_value(event) == "SELL"]
        timestamps = [event.timestamp for event in trades]
        blocks = [event.block_number for event in trades]
        lifecycle_types = {str(event.event_type).upper() for event in lifecycle}
        observed_unknown_events = sum(
            1 for event in trades if side_value(event) not in {"BUY", "SELL"}
        )
        observed_unknown_events += sum(
            1
            for event in lifecycle
            if str(event.event_type).upper() in {"", "UNKNOWN"}
            or str((event.payload_json or {}).get("evidence_level", "")).upper()
            == EvidenceLevel.UNKNOWN.value
        )
        if unknown_events is None:
            # A configured-topic scan cannot prove that no other event types
            # occurred. Only count unknown records that were explicitly
            # supplied; otherwise keep the measurement UNKNOWN.
            measured_unknown_events = (
                observed_unknown_events if observed_unknown_events else None
            )
        else:
            if unknown_events < 0:
                raise ValueError("unknown_events must be non-negative")
            measured_unknown_events = unknown_events
        known_timestamps = [timestamp for timestamp in timestamps if isinstance(timestamp, datetime)]
        first_trade = min(known_timestamps) if known_timestamps else None
        last_trade = max(known_timestamps) if known_timestamps else None
        missing_blocks = sum(block is None for block in blocks)
        missing_timestamps = sum(timestamp is None for timestamp in timestamps)
        # A historical token is only complete when both sides of the trade
        # lifecycle are present. A BUY-only sample is not a successful
        # backfill, even if every decoded row has a timestamp.
        core_ok = bool(creation_verified and creator_address and buys and sells) and not any(
            (
                missing_blocks,
                missing_timestamps,
                decode_errors,
                duplicate_events,
                measured_unknown_events or 0,
            )
        )
        if not core_ok:
            overall = "FAIL"
        elif "GRADUATED" not in lifecycle_types and "DEX_LISTED" not in lifecycle_types:
            overall = "PASS_WITH_WARNINGS"
        else:
            overall = "PASS"
        return cls(
            token_address=token_address,
            creation_status="VERIFIED" if creation_verified else "UNKNOWN",
            first_trade=first_trade,
            last_trade=last_trade,
            buy_count=len(buys),
            sell_count=len(sells),
            unique_traders=len(
                {
                    event.trader_address.lower()
                    for event in trades
                    if isinstance(event.trader_address, str) and event.trader_address
                }
            ),
            graduation="VERIFIED" if "GRADUATED" in lifecycle_types else "UNKNOWN",
            migration="VERIFIED" if "DEX_LISTED" in lifecycle_types else "UNKNOWN",
            missing_blocks=missing_blocks,
            missing_timestamps=missing_timestamps,
            decode_errors=decode_errors,
            unknown_events=measured_unknown_events,
            duplicate_events=duplicate_events,
            evidence_level=EvidenceLevel.DERIVED if core_ok else EvidenceLevel.UNKNOWN,
            overall=overall,
            creator_address=creator_address,
        )

    def render(self) -> str:
        def format_time(value: datetime | None) -> str:
            return value.isoformat() if value is not None else "UNKNOWN"

        def format_count(value: int | None) -> str:
            return str(value) if value is not None else "UNKNOWN"

        return "\n".join(
            (
                "DATA QUALITY",
                "",
                f"Token: {self.token_address}",
                f"Creator: {self.creator_address or 'UNKNOWN'}",
                f"Creation: {self.creation_status}",
                f"First Trade: {format_time(self.first_trade)}",
                f"Last Trade: {format_time(self.last_trade)}",
                f"BUY count: {self.buy_count}",
                f"SELL count: {self.sell_count}",
                f"Unique Traders: {self.unique_traders}",
                f"Graduation: {self.graduation}",
                f"Migration: {self.migration}",
                f"Missing Blocks: {self.missing_blocks}",
                f"Missing Timestamps: {self.missing_timestamps}",
                f"Decode Errors: {self.decode_errors}",
                f"Unknown Events: {format_count(self.unknown_events)}",
                f"Duplicate Events: {self.duplicate_events}",
                f"Evidence: {self.evidence_level.value}",
                f"Overall: {self.overall}",
            )
        )


__all__ = ["FlapDataQualityReport"]
