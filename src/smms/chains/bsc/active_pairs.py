"""Bounded active-pair lifecycle and dynamic log fan-in for BSC.

The factory stream is the only discovery authority.  Once a verified
``PairCreated`` record is decoded it is registered here and a pair-log stream
is opened without restarting the worker.  The registry intentionally keeps
runtime state separate from historical ``Pair`` rows: expiring a pair stops
live work but never deletes chain history.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from smms.chains.bsc.websocket import BlockWindowDedupe
from smms.chains.constants import BSC_CHAIN_ID, normalize_bsc_address

if TYPE_CHECKING:
    from smms.market.models import PairRecord

logger = logging.getLogger(__name__)


def _log_sort_key(log: Any) -> tuple[int, int, int, str, bool]:
    return (
        int(getattr(log, "block_number", 0)),
        int(getattr(log, "transaction_index", -1) or -1),
        int(getattr(log, "log_index", -1)),
        str(getattr(log, "transaction_hash", "")).lower(),
        bool(getattr(log, "removed", False)),
    )


def _log_identity(log: Any) -> tuple[str, int, str | None]:
    return (
        str(getattr(log, "transaction_hash", "")).lower(),
        int(getattr(log, "log_index", -1)),
        getattr(log, "block_hash", None),
    )


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


@dataclass
class ActivePair:
    pair_address: str
    token0: str
    token1: str
    target_token: str | None
    quote_token: str | None
    dex: str
    factory: str | None
    created_block: int | None
    creation_block_hash: str | None
    created_at: datetime | None
    last_activity_at: datetime | None
    subscription_state: str = "PENDING"
    priority: float = 0.0
    expires_at: datetime | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "pair_address": self.pair_address,
            "token0": self.token0,
            "token1": self.token1,
            "target_token": self.target_token,
            "quote_token": self.quote_token,
            "dex": self.dex,
            "factory": self.factory,
            "created_block": self.created_block,
            "creation_block_hash": self.creation_block_hash,
            "created_at": self.created_at,
            "last_activity_at": self.last_activity_at,
            "subscription_state": self.subscription_state,
            "priority": self.priority,
            "expires_at": self.expires_at,
        }


class ActivePairRegistry:
    """In-memory bounded live-pair registry.

    Pair history is persisted by the market pipeline.  This registry only owns
    subscriptions, so eviction is safe and deterministic.  ``created_at`` is
    never fabricated: it remains ``None`` until the chain block timestamp is
    available.
    """

    def __init__(
        self,
        *,
        max_active_pairs: int = 500,
        activity_ttl_seconds: int = 3600,
        subscription_batch_size: int = 50,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if max_active_pairs < 1 or activity_ttl_seconds < 1 or subscription_batch_size < 1:
            raise ValueError("active pair bounds must be positive")
        self.max_active_pairs = max_active_pairs
        self.activity_ttl_seconds = activity_ttl_seconds
        self.subscription_batch_size = subscription_batch_size
        self.clock = clock or (lambda: datetime.now(UTC))
        self._pairs: dict[str, ActivePair] = {}
        self._bucket_by_address: dict[str, int] = {}
        self._buckets: dict[int, list[str]] = {}
        self._next_bucket = 0

    @property
    def metrics(self) -> dict[str, int]:
        self.expire()
        return {
            "active_pairs_current": len(self._pairs),
            "active_pairs_restored": int(getattr(self, "_restored_count", 0)),
            "active_pairs_expired": int(getattr(self, "_expired_count", 0)),
            "active_pairs_evicted": int(getattr(self, "_evicted_count", 0)),
        }

    @property
    def count(self) -> int:
        self.expire()
        return len(self._pairs)

    def get(self, pair_address: str) -> ActivePair | None:
        self.expire()
        return self._pairs.get(str(pair_address).lower())

    def __contains__(self, pair_address: str) -> bool:
        return self.get(pair_address) is not None

    def add(
        self,
        pair: PairRecord | None = None,
        *,
        pair_address: str | None = None,
        token0: str | None = None,
        token1: str | None = None,
        target_token: str | None = None,
        quote_token: str | None = None,
        dex: str = "unknown",
        factory: str | None = None,
        created_block: int | None = None,
        creation_block_hash: str | None = None,
        created_at: datetime | None = None,
        priority: float = 0.0,
    ) -> ActivePair | None:
        if pair is not None:
            if pair.chain_id != BSC_CHAIN_ID:
                return None
            pair_address, token0, token1 = pair.address, pair.token0, pair.token1
            quote_token, dex, factory = pair.quote_token, pair.dex, pair.factory
            if quote_token is None:
                return None
            created_block, created_at = pair.creation_block, pair.creation_timestamp
            creation_block_hash = pair.creation_block_hash
        if not pair_address or not token0 or not token1:
            raise ValueError("pair_address, token0 and token1 are required")
        pair_address = normalize_bsc_address(pair_address)
        token0 = normalize_bsc_address(token0)
        token1 = normalize_bsc_address(token1)
        quote_token = normalize_bsc_address(quote_token) if quote_token else None
        factory = normalize_bsc_address(factory) if factory else None
        target_token = normalize_bsc_address(target_token) if target_token else None
        if target_token is None:
            target_token = token1 if quote_token == token0 else token0 if quote_token == token1 else None
        now = _utc(self.clock())
        current = self._pairs.get(pair_address)
        if current is not None:
            supplied_activity = _utc(created_at)
            if supplied_activity is not None:
                current.last_activity_at = max(
                    (current.last_activity_at or supplied_activity), supplied_activity
                )
            current.created_at = current.created_at or supplied_activity
            current.created_block = created_block if created_block is not None else current.created_block
            if creation_block_hash is not None:
                current.creation_block_hash = str(creation_block_hash).lower()
            current.priority = max(current.priority, float(priority))
            current.expires_at = self._expiry(current.last_activity_at, now)
            return current
        self.expire(now=now)
        activity = _utc(created_at)
        if len(self._pairs) >= self.max_active_pairs:
            candidate = min(self._pairs.values(), key=self._eviction_key)
            incoming_key = (
                activity.timestamp() if activity is not None else float("-inf"),
                activity.timestamp() if activity is not None else float("-inf"),
                float(priority),
                pair_address,
            )
            if incoming_key <= self._eviction_key(candidate):
                return None
            self.remove(candidate.pair_address, state="EVICTED")
        item = ActivePair(
            pair_address=pair_address,
            token0=token0,
            token1=token1,
            target_token=target_token,
            quote_token=quote_token,
            dex=dex,
            factory=factory,
            created_block=created_block,
            creation_block_hash=(str(creation_block_hash).lower() if creation_block_hash else None),
            created_at=activity,
            last_activity_at=activity,
            priority=float(priority),
            expires_at=self._expiry(activity, now),
        )
        self._pairs[pair_address] = item
        bucket_id = next((key for key in sorted(self._buckets) if len(self._buckets[key]) < self.subscription_batch_size), None)
        if bucket_id is None:
            bucket_id = self._next_bucket
            self._next_bucket += 1
            self._buckets[bucket_id] = []
        self._buckets[bucket_id].append(pair_address)
        self._bucket_by_address[pair_address] = bucket_id
        from smms.observability import metrics
        metrics.set_gauge("active_pairs_current", len(self._pairs))
        return item

    def touch(self, pair_address: str, at: datetime | None = None) -> ActivePair | None:
        item = self._pairs.get(str(pair_address).lower())
        if item is None:
            return None
        stamp = _utc(at) or _utc(self.clock())
        item.last_activity_at = max(item.last_activity_at or stamp, stamp)
        item.expires_at = self._expiry(item.last_activity_at, _utc(self.clock()))
        if item.subscription_state in {"EXPIRED", "EVICTED"}:
            item.subscription_state = "PENDING"
        return item

    def set_subscription_state(self, pair_address: str, state: str) -> None:
        item = self._pairs.get(str(pair_address).lower())
        if item is not None:
            item.subscription_state = str(state).upper()

    def remove(self, pair_address: str, *, state: str = "EVICTED") -> ActivePair | None:
        item = self._pairs.pop(str(pair_address).lower(), None)
        if item is not None:
            item.subscription_state = state
            bucket_id = self._bucket_by_address.pop(item.pair_address, None)
            if bucket_id is not None and bucket_id in self._buckets:
                self._buckets[bucket_id] = [address for address in self._buckets[bucket_id] if address != item.pair_address]
                if not self._buckets[bucket_id]:
                    self._buckets.pop(bucket_id, None)
            if state == "EVICTED":
                self._evicted_count = int(getattr(self, "_evicted_count", 0)) + 1
                from smms.observability import metrics
                metrics.increment("active_pairs_evicted")
            from smms.observability import metrics
            metrics.set_gauge("active_pairs_current", len(self._pairs))
        return item

    def expire(self, *, now: datetime | None = None) -> tuple[ActivePair, ...]:
        current = _utc(now) or _utc(self.clock())
        expired: list[ActivePair] = []
        for address, item in tuple(self._pairs.items()):
            if item.expires_at is not None and current >= item.expires_at:
                expired.append(self._pairs.pop(address))
                expired[-1].subscription_state = "EXPIRED"
                bucket_id = self._bucket_by_address.pop(address, None)
                if bucket_id is not None and bucket_id in self._buckets:
                    self._buckets[bucket_id] = [item for item in self._buckets[bucket_id] if item != address]
                    if not self._buckets[bucket_id]:
                        self._buckets.pop(bucket_id, None)
                self._expired_count = int(getattr(self, "_expired_count", 0)) + 1
                from smms.observability import metrics
                metrics.increment("active_pairs_expired")
        return tuple(expired)

    def active(self) -> tuple[ActivePair, ...]:
        self.expire()
        return tuple(
            sorted(
                self._pairs.values(),
                key=self._eviction_key,
                reverse=True,
            )
        )

    def addresses(self) -> tuple[str, ...]:
        return tuple(item.pair_address for item in self.active())

    def batches(self) -> tuple[tuple[ActivePair, ...], ...]:
        self.expire()
        return tuple(
            tuple(self._pairs[address] for address in addresses if address in self._pairs)
            for _, addresses in sorted(self._buckets.items())
            if any(address in self._pairs for address in addresses)
        )

    def bucket_for_address(self, pair_address: str) -> int | None:
        """Return the stable subscription bucket for a live pair."""
        self.expire()
        return self._bucket_by_address.get(str(pair_address).lower())

    async def bootstrap(
        self,
        session: Any,
        *,
        factory_addresses: Iterable[str] = (),
        quote_tokens: Iterable[str] = (),
        now: datetime | None = None,
        stream_name: str | None = None,
        checkpoint_provider: str = "bsc",
    ) -> dict[str, int]:
        """Restore bounded live subscriptions from canonical Pair/TradeEvent rows."""
        from sqlalchemy import func, select

        from smms.db.models import Pair, StreamCheckpoint, TradeEvent

        current = _utc(now) or _utc(self.clock())
        cutoff = current - timedelta(seconds=self.activity_ttl_seconds)
        factories = {normalize_bsc_address(value) for value in factory_addresses if value}
        quotes = {normalize_bsc_address(value) for value in quote_tokens if value}
        activity_expr = func.max(TradeEvent.timestamp)
        query = (
            select(Pair, activity_expr.label("latest_activity"))
            .outerjoin(
                TradeEvent,
                (TradeEvent.pair_address == Pair.address)
                & (TradeEvent.chain == "bsc")
                & TradeEvent.canonical.is_(True)
                & TradeEvent.removed.is_(False),
            )
            .where(
                Pair.chain_id == BSC_CHAIN_ID,
                Pair.canonical.is_(True),
                Pair.removed.is_(False),
            )
            .group_by(Pair.id)
            .having(func.coalesce(activity_expr, Pair.creation_timestamp) >= cutoff)
        )
        if factories:
            query = query.where(Pair.factory_address.in_(factories))
        if quotes:
            query = query.where(Pair.quote_token.in_(quotes))
        if stream_name is not None:
            # A resumed live epoch owns only its durable pair lanes. Importing
            # every recent historical pair would widen recovery unexpectedly.
            query = query.where(
                select(StreamCheckpoint.id).where(
                    StreamCheckpoint.chain_id == Pair.chain_id,
                    StreamCheckpoint.provider == checkpoint_provider,
                    StreamCheckpoint.stream_name == stream_name + ":pair:" + Pair.address,
                ).exists()
            )
        query = query.order_by(
            func.coalesce(activity_expr, Pair.creation_timestamp).desc(),
            Pair.creation_block.desc(),
            Pair.address.asc(),
        ).limit(self.max_active_pairs)
        rows = (await session.execute(query)).all()
        expired = restored = 0
        candidates = []
        for pair, latest_activity in rows:
            created = _utc(pair.creation_timestamp)
            activity = _utc(latest_activity) or created
            if activity is None or (current - activity).total_seconds() > self.activity_ttl_seconds:
                expired += 1
                continue
            candidates.append((activity, pair))
        for activity, pair in candidates:
            item = self.add(
                pair_address=pair.address,
                token0=pair.token0_address,
                token1=pair.token1_address,
                quote_token=pair.quote_token,
                dex=pair.dex,
                factory=pair.factory_address,
                created_block=pair.creation_block,
                creation_block_hash=pair.creation_block_hash,
                created_at=_utc(pair.creation_timestamp),
                priority=activity.timestamp(),
            )
            if item is not None:
                self.touch(item.pair_address, activity)
                item.subscription_state = "PENDING"
                restored += 1
        from smms.observability import metrics
        metrics.increment("active_pairs_restored", restored)
        metrics.set_gauge("active_pairs_current", len(self._pairs))
        self._restored_count = int(getattr(self, "_restored_count", 0)) + restored
        self._expired_count = int(getattr(self, "_expired_count", 0)) + expired
        return {**self.metrics, "active_pairs_restored": restored, "active_pairs_expired": expired}

    bootstrap_from_database = bootstrap

    def snapshot(self) -> tuple[dict[str, Any], ...]:
        return tuple(item.as_dict() for item in self.active())

    def _expiry(self, activity: datetime | None, now: datetime | None) -> datetime | None:
        # Unknown chain timestamps do not get persisted as fake event times, but
        # a local lifecycle deadline is still required to bound subscriptions.
        return (activity or now or datetime.now(UTC)) + timedelta(seconds=self.activity_ttl_seconds)

    def _evict_one(self) -> None:
        if not self._pairs:
            return
        candidate = min(
            self._pairs.values(),
            key=self._eviction_key,
        )
        self.remove(candidate.pair_address, state="EVICTED")

    @staticmethod
    def _eviction_key(item: ActivePair) -> tuple[float, float, float, str]:
        """Use activity/creation recency as policy; priority only breaks ties."""
        activity = item.last_activity_at or item.created_at
        return (
            activity.timestamp() if activity is not None else float("-inf"),
            item.created_at.timestamp() if item.created_at is not None else float("-inf"),
            float(item.priority),
            item.pair_address,
        )


class DynamicPairSubscription:
    """Fan in a verified factory stream and bounded pair subscriptions."""

    def __init__(
        self,
        factory_stream: Any,
        *,
        registry: ActivePairRegistry,
        pair_stream_factory: Callable[[tuple[str, ...], tuple[str, ...]], Any],
        pair_created_decoder: Callable[[Any], PairRecord | None],
        pair_topics: Iterable[str],
        timestamp_resolver: Callable[[int], Awaitable[datetime | None]] | None = None,
        activation_replay_provider: Any = None,
        activation_replay_max_blocks: int = 2048,
        activation_replay_max_events: int = 10_000,
        refresh_interval_seconds: float = 0.2,
        queue_max_events: int = 10_000,
        factory_addresses: Iterable[str] = (),
        quote_tokens: Iterable[str] = (),
    ) -> None:
        self.factory_stream = factory_stream
        self.registry = registry
        self.pair_stream_factory = pair_stream_factory
        self.pair_created_decoder = pair_created_decoder
        self.pair_topics = tuple(pair_topics)
        self.configured_factory_addresses = tuple(
            sorted({normalize_bsc_address(value) for value in factory_addresses if value})
        )
        self.configured_quote_tokens = frozenset(
            normalize_bsc_address(value) for value in quote_tokens if value
        )
        self.timestamp_resolver = timestamp_resolver
        self.activation_replay_provider = activation_replay_provider
        self.activation_replay_max_blocks = max(1, int(activation_replay_max_blocks))
        self.activation_replay_max_events = max(1, int(activation_replay_max_events))
        self.refresh_interval_seconds = max(0.01, refresh_interval_seconds)
        self.queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=max(1, queue_max_events))
        self._closed = False
        self._tasks: dict[tuple[str, ...], asyncio.Task[None]] = {}
        self._streams: dict[tuple[str, ...], Any] = {}
        self._factory_task: asyncio.Task[None] | None = None
        self._refresh_task: asyncio.Task[None] | None = None
        self._factory_address: Any = None
        self._factory_topics: tuple[str, ...] | None = None
        self._start_block: int | None = None
        self._durable_checkpoint: tuple[int, int, str] | None = None
        settings = getattr(factory_stream, "settings", None) or getattr(
            getattr(factory_stream, "websocket", None), "settings", None
        )
        retention = (
            int(getattr(settings, "bsc_confirmation_depth", 3))
            + int(getattr(settings, "bsc_reorg_lookback_blocks", 12)) * 2
            + self.activation_replay_max_blocks
        )
        self._activation_replayed = BlockWindowDedupe(retention)
        self._seen = BlockWindowDedupe(retention)
        self._pair_checkpoints: dict[str, tuple[int, int, str]] = {}
        self._activating: set[str] = set()
        self._activation_buffers: dict[str, list[Any]] = {}
        self._ready_events: dict[tuple[str, ...], asyncio.Event] = {}
        self._ready_errors: dict[tuple[str, ...], Exception] = {}
        self._pair_handoff_events: dict[tuple[str, ...], asyncio.Event] = {}
        self._prepared_pair_candidates: dict[tuple[str, ...], Any] = {}
        self._prepared_factory_candidate: Any = None
        self._factory_error: Exception | None = None
        self._refresh_error: Exception | None = None
        self._monitor_task: asyncio.Task[None] | None = None
        self.metrics = getattr(factory_stream, "metrics", None)
        self.settings = getattr(factory_stream, "settings", None)
        self.provider = getattr(factory_stream, "provider", "bsc_websocket")

    async def close(self) -> None:
        self._closed = True
        current = asyncio.current_task()
        tasks = [task for task in self._tasks.values() if not task.done() and task is not current]
        for task in (self._factory_task, self._refresh_task):
            if task is not None and not task.done() and task is not current:
                task.cancel()
                tasks.append(task)
        if self._monitor_task is not None and not self._monitor_task.done() and self._monitor_task is not current:
            self._monitor_task.cancel()
            tasks.append(self._monitor_task)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for stream in tuple(self._streams.values()):
            close_stream = getattr(stream, "close", None)
            if callable(close_stream):
                result = close_stream()
                if hasattr(result, "__await__"):
                    await result
        self._streams.clear()
        close = getattr(self.factory_stream, "close", None)
        if callable(close):
            result = close()
            if hasattr(result, "__await__"):
                await result

    def set_start_block(self, block_number: int | None) -> None:
        self._start_block = block_number
        setter = getattr(self.factory_stream, "set_start_block", None)
        if callable(setter):
            setter(block_number)

    def observe_checkpoint(
        self, block_number: int | None, log_index: int | None = None, transaction_hash: str | None = None
    ) -> None:
        if block_number is not None:
            candidate = (int(block_number), int(log_index or -1), str(transaction_hash or ""))
            if self._durable_checkpoint is None or candidate[:2] >= self._durable_checkpoint[:2]:
                self._durable_checkpoint = candidate
                self._start_block = int(block_number)
        observer = getattr(self.factory_stream, "observe_checkpoint", None)
        if callable(observer):
            observer(block_number, log_index, transaction_hash)

    def observe_pair_checkpoint(
        self,
        pair_address: str,
        block_number: int | None,
        log_index: int | None = None,
        transaction_hash: str | None = None,
    ) -> None:
        """Advance only the committed subscription lane that emitted a pair log."""
        canonical = str(pair_address).lower()
        key = next((addresses for addresses in self._streams if canonical in addresses), None)
        stream = self._streams.get(key) if key is not None else None
        observer = getattr(stream, "observe_checkpoint", None)
        if callable(observer):
            observer(block_number, log_index, transaction_hash)
        if block_number is not None:
            candidate = (int(block_number), int(log_index or -1), str(transaction_hash or ""))
            current = self._pair_checkpoints.get(canonical)
            if current is None or candidate[:2] >= current[:2]:
                self._pair_checkpoints[canonical] = candidate

    async def subscribe_logs(self, *, address: Any = None, topics: Iterable[str] | None = None) -> AsyncIterator[Any]:
        self._factory_address = address or self.configured_factory_addresses or None
        self._factory_topics = tuple(topics) if topics else None
        self._factory_task = asyncio.create_task(self._pump_factory())
        self._refresh_task = asyncio.create_task(self._refresh_pairs())
        self._monitor_task = asyncio.create_task(self._monitor_supervisors())
        try:
            while not self._closed:
                item = await self.queue.get()
                if item is _STOP:
                    break
                yield item
        finally:
            await self.close()

    async def prepare_factory_candidate(
        self, *, address: Any = None, topics: Iterable[str] | None = None
    ) -> Any:
        """Open and ACK the factory WS before the HTTP replay barrier."""
        opener = getattr(self.factory_stream, "prepare_log_candidate", None) or getattr(
            self.factory_stream, "open_log_candidate", None
        )
        if not callable(opener):
            return None
        candidate = await opener(
            address=address or self.configured_factory_addresses or None,
            topics=tuple(topics) if topics else self._factory_topics,
        )
        self._prepared_factory_candidate = candidate
        return candidate

    async def _pump_factory(self) -> None:
        owner_stream = self.factory_stream
        try:
            if self._prepared_factory_candidate is not None:
                candidate = self._prepared_factory_candidate
                self._prepared_factory_candidate = None
                drain = getattr(candidate, "drain_buffer", None)
                if callable(drain):
                    for buffered in sorted(drain(), key=_log_sort_key):
                        await self._handle_factory_log(buffered)
                commit = getattr(owner_stream, "commit_prepared_candidate", None)
                if callable(commit):
                    commit(candidate)
                    iterator = owner_stream.subscribe_logs(
                        address=self._factory_address, topics=self._factory_topics
                    )
                else:
                    iterator = candidate
            else:
                iterator = owner_stream.subscribe_logs(
                    address=self._factory_address, topics=self._factory_topics
                )
            async for log in iterator:
                if self._closed:
                    return
                await self._handle_factory_log(log)
            if not self._closed and self.configured_factory_addresses:
                self._factory_error = ConnectionError("BSC factory stream ended unexpectedly")
                await self.queue.put(_STOP)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - transport failures must be surfaced
            if not self._closed:
                self._factory_error = exc
                await self.queue.put(_STOP)

    async def _handle_factory_log(self, log: Any) -> None:
        if self.configured_factory_addresses:
            try:
                emitter = normalize_bsc_address(getattr(log, "address", ""))
            except ValueError:
                return
            if emitter not in self.configured_factory_addresses:
                return
        record = self.pair_created_decoder(log)
        if record is None:
            await self._enqueue(log)
            return
        if self.configured_factory_addresses:
            try:
                record_factory = normalize_bsc_address(record.factory or "")
            except ValueError:
                return
            if record_factory not in self.configured_factory_addresses:
                return
        # Persist configured-factory history even when quote policy excludes
        # live activation; non-quote pairs remain chain facts but never become
        # active lanes.
        await self._enqueue(log)
        if self.configured_quote_tokens and (
            record.quote_token is None
            or normalize_bsc_address(record.quote_token) not in self.configured_quote_tokens
        ):
            return
        canonical = str(record.address).lower()
        if getattr(log, "removed", False):
            block_hash = getattr(log, "block_hash", None)
            current = self.registry.get(canonical)
            if (
                block_hash
                and current is not None
                and current.creation_block_hash == str(block_hash).lower()
            ):
                self.registry.remove(canonical, state="ORPHANED")
                await self._ensure_pair_tasks()
            return
        was_known = self.registry.get(canonical) is not None
        if not was_known:
            self._activating.add(canonical)
            self._activation_buffers[canonical] = []
            self._pair_checkpoints[canonical] = (
                int(record.creation_block),
                int(getattr(log, "log_index", -1)),
                str(getattr(log, "transaction_hash", "")),
            )
        if record.creation_timestamp is None and self.timestamp_resolver is not None:
            try:
                timestamp = await self.timestamp_resolver(record.creation_block)
                record = replace(record, creation_timestamp=timestamp)
            except (OSError, RuntimeError, ValueError, TypeError) as exc:
                logger.debug("pair timestamp lookup unavailable: %s", type(exc).__name__)
        self.registry.add(record)
        await self._ensure_pair_tasks()
        if not was_known:
            self.registry.set_subscription_state(canonical, "ACTIVATING")
            await self._activation_catch_up(record)
            await self._complete_activation(canonical)

    async def _refresh_pairs(self) -> None:
        while not self._closed:
            try:
                self.registry.expire()
                await self._ensure_pair_tasks()
                self._refresh_error = None
                await asyncio.sleep(self.refresh_interval_seconds)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - supervisor must keep retrying
                self._refresh_error = exc
                logger.warning("BSC pair subscription supervisor degraded: %s", exc)
                await asyncio.sleep(min(max(self.refresh_interval_seconds, 0.05) * 4, 5.0))

    async def _monitor_supervisors(self) -> None:
        while not self._closed:
            await asyncio.sleep(self.refresh_interval_seconds)
            for name, task in (("factory", self._factory_task), ("refresh", self._refresh_task)):
                if task is None or not task.done() or task.cancelled():
                    continue
                error = task.exception()
                if error is None:
                    if name == "factory" and not self.configured_factory_addresses:
                        # Preserve the finite fixture/replay mode used by
                        # offline callers; configured live lanes treat the
                        # same completion as an unexpected transport exit.
                        continue
                    error = RuntimeError(f"BSC {name} supervisor exited unexpectedly")
                if name == "factory":
                    self._factory_error = error
                else:
                    self._refresh_error = error
                if not self._closed:
                    await self.queue.put(_STOP)
                return

    async def _ensure_pair_tasks(self) -> None:
        wanted = {tuple(item.pair_address for item in batch) for batch in self.registry.batches()}
        for key in tuple(self._tasks):
            task = self._tasks[key]
            if key not in wanted or task.done():
                task.cancel()
                self._tasks.pop(key, None)
                self._ready_events.pop(key, None)
                self._ready_errors.pop(key, None)
                self._pair_handoff_events.pop(key, None)
                self._prepared_pair_candidates.pop(key, None)
        for key in sorted(wanted):
            if key in self._tasks:
                continue
            self._ready_events[key] = asyncio.Event()
            self._ready_errors.pop(key, None)
            self._pair_handoff_events[key] = asyncio.Event()
            task = asyncio.create_task(self._pump_pair_batch(key))
            self._tasks[key] = task
            ready = self._ready_events[key]
            for address in key:
                self.registry.set_subscription_state(address, "ACTIVATING")
            await ready.wait()
            if key in self._ready_errors:
                raise RuntimeError("pair subscription readiness failed") from self._ready_errors[key]
            try:
                await self._batch_catch_up(key)
                owner_stream = self._streams.get(key)
                candidate = self._prepared_pair_candidates.pop(key, None)
                if candidate is not None:
                    commit = getattr(owner_stream, "commit_prepared_candidate", None)
                    if callable(commit):
                        commit(candidate)
            finally:
                handoff = self._pair_handoff_events.get(key)
                if handoff is not None:
                    handoff.set()

    async def _batch_catch_up(self, addresses: tuple[str, ...]) -> None:
        provider = self.activation_replay_provider
        get_head = getattr(provider, "get_latest_block_number", None)
        get_logs = getattr(provider, "get_logs", None)
        if not callable(get_head) or not callable(get_logs):
            return
        checkpoints = [self._pair_checkpoints[address][0] for address in addresses if address in self._pair_checkpoints]
        boundary = min(checkpoints) if checkpoints else (
            self._durable_checkpoint[0] if self._durable_checkpoint else self._start_block
        )
        if boundary is None:
            return
        barrier = int(await get_head())
        if barrier < boundary:
            return
        if barrier - boundary > self.activation_replay_max_blocks:
            raise ValueError("pair batch replay exceeds configured block bound")
        logs = await get_logs(
            from_block=int(boundary),
            to_block=barrier,
            address=addresses,
            topics=self.pair_topics,
        )
        if len(logs) > self.activation_replay_max_events:
            raise ValueError("pair batch replay exceeds configured event bound")
        for log in sorted(logs, key=_log_sort_key):
            if self.registry.get(log.address) is not None:
                self.registry.touch(log.address, getattr(log, "timestamp", None))
                canonical = str(log.address).lower()
                if canonical in self._activating:
                    self._buffer_activation_log(canonical, log)
                else:
                    await self._enqueue(log)

    async def _activation_catch_up(self, record: Any) -> None:
        """Bounded replay from a brand-new pair's creation block.

        Restored historical pairs never enter this path, so recovery remains
        bounded to the durable cursor/lookback while new pairs close the
        PairCreated-to-WS activation window.
        """
        provider = self.activation_replay_provider
        creation_block = getattr(record, "creation_block", None)
        address = getattr(record, "address", None)
        if provider is None or creation_block is None or not address:
            return
        get_head = getattr(provider, "get_latest_block_number", None)
        get_logs = getattr(provider, "get_logs", None)
        if not callable(get_head) or not callable(get_logs):
            return
        barrier = int(await get_head())
        start = int(creation_block)
        if barrier < start:
            return
        if barrier - start > self.activation_replay_max_blocks:
            raise ValueError("pair activation replay exceeds configured block bound")
        logs = await get_logs(
            from_block=start,
            to_block=barrier,
            address=str(address).lower(),
            topics=self.pair_topics,
        )
        if len(logs) > self.activation_replay_max_events:
            raise ValueError("pair activation replay exceeds configured event bound")
        for log in sorted(logs, key=_log_sort_key):
            if not self._activation_replayed.accept(log):
                continue
            if self.registry.get(log.address) is not None:
                self.registry.touch(log.address, getattr(log, "timestamp", None))
                self._buffer_activation_log(str(log.address).lower(), log)

    def _buffer_activation_log(self, address: str, log: Any) -> None:
        buffer = self._activation_buffers.setdefault(address, [])
        if len(buffer) >= self.activation_replay_max_events:
            raise ValueError("pair activation buffer exceeds configured event bound")
        buffer.append(log)

    async def _complete_activation(self, address: str) -> None:
        # Keep buffering until the current snapshot is fully drained.  There
        # is no await between the final empty check and switching to primary.
        while self._activation_buffers.get(address):
            buffered = self._activation_buffers[address]
            self._activation_buffers[address] = []
            for log in sorted(buffered, key=_log_sort_key):
                await self._enqueue(log)
        self._activation_buffers.pop(address, None)
        self._activating.discard(address)
        self.registry.set_subscription_state(address, "SUBSCRIBED")

    async def _enqueue(self, log: Any) -> None:
        if not self._seen.accept(log):
            return
        await self.queue.put(log)

    async def _pump_pair_batch(self, addresses: tuple[str, ...]) -> None:
        ready = self._ready_events.get(addresses)
        if ready is None:
            ready = asyncio.Event()
            self._ready_events[addresses] = ready
        try:
            owner_stream = self.pair_stream_factory(addresses, self.pair_topics)
            candidate = None
            opener = getattr(owner_stream, "prepare_log_candidate", None) or getattr(
                owner_stream, "open_log_candidate", None
            )
            if callable(opener):
                candidate = await opener(address=addresses, topics=self.pair_topics)
            else:
                wait_ready = getattr(owner_stream, "wait_ready", None) or getattr(
                    owner_stream, "wait_subscription_ready", None
                )
                if callable(wait_ready):
                    result = wait_ready()
                    if hasattr(result, "__await__"):
                        await result
            self._streams[addresses] = owner_stream
            if candidate is not None:
                self._prepared_pair_candidates[addresses] = candidate
            ready.set()
            if candidate is not None:
                handoff = self._pair_handoff_events.setdefault(addresses, asyncio.Event())
                await handoff.wait()
            setter = getattr(owner_stream, "set_start_block", None)
            boundary = self._durable_checkpoint[0] if self._durable_checkpoint else self._start_block
            if callable(setter) and boundary is not None:
                setter(boundary)
            if candidate is not None and not callable(getattr(owner_stream, "commit_prepared_candidate", None)):
                iterator = candidate
            else:
                iterator = owner_stream.subscribe_logs(address=addresses, topics=self.pair_topics) if hasattr(owner_stream, "subscribe_logs") else owner_stream
            async for log in iterator:
                if self._closed:
                    return
                if self.registry.get(log.address) is None:
                    continue
                self.registry.touch(log.address, getattr(log, "timestamp", None))
                canonical = str(log.address).lower()
                if canonical in self._activating:
                    self._buffer_activation_log(canonical, log)
                else:
                    await self._enqueue(log)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - all transport failures degrade the lane
            self._ready_errors[addresses] = exc
            ready.set()
            for address in addresses:
                self.registry.set_subscription_state(address, "DEGRADED")
        finally:
            ready.set()
            stream = self._streams.pop(addresses, None)
            close_stream = getattr(stream, "close", None) if stream is not None else None
            if callable(close_stream):
                result = close_stream()
                if hasattr(result, "__await__"):
                    await result


_STOP = object()


__all__ = ["ActivePair", "ActivePairRegistry", "DynamicPairSubscription"]
