"""Deterministic wallet intelligence graph and alpha primitives.

The module is deliberately independent from SQLAlchemy.  Callers provide the
normalized trade dictionaries (or ORM-like objects); every operation accepts an
optional cutoff so historical reads cannot observe future events.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict, deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from itertools import islice
from typing import Any

from smms.chains.constants import DEFAULT_CHAIN, validate_bsc_chain


def _get(row: Any, key: str, default: Any = None) -> Any:
    if isinstance(row, Mapping):
        return row.get(key, default)
    return getattr(row, key, default)


def _dec(value: Any) -> Decimal:
    try:
        return Decimal(str(value or 0))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(0)


def _time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _cutoff(value: datetime | None) -> datetime:
    now = value or datetime.now(UTC)
    return now.replace(tzinfo=UTC) if now.tzinfo is None else now.astimezone(UTC)


@dataclass(frozen=True)
class WalletEntity:
    address: str
    chain: str = DEFAULT_CHAIN


@dataclass(frozen=True)
class TokenEntity:
    address: str
    chain: str = DEFAULT_CHAIN


@dataclass(frozen=True)
class ClusterEntity:
    cluster_id: str
    wallets: tuple[str, ...]


@dataclass(frozen=True)
class TradeEntity:
    wallet: str
    token: str
    side: str
    timestamp: datetime
    value: Decimal = Decimal(0)
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Relationship:
    source: str
    target: str
    kind: str
    weight: Decimal = Decimal(1)
    evidence: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WalletKnowledgeGraph:
    wallets: tuple[WalletEntity, ...]
    tokens: tuple[TokenEntity, ...]
    trades: tuple[TradeEntity, ...]
    relationships: tuple[Relationship, ...]
    truncated: bool = False
    truncation: Mapping[str, Any] = field(default_factory=dict)

    @property
    def truncation_reasons(self) -> tuple[str, ...]:
        return (
            tuple(self.truncation.get("truncation_reasons", ()))
            if isinstance(self.truncation.get("truncation_reasons"), tuple)
            else ()
        )

    @property
    def clusters(self) -> tuple[ClusterIntelligence, ...]:
        return detect_wallet_clusters(self)

    def relationships_for(self, address: str, kind: str | None = None) -> tuple[Relationship, ...]:
        key = address.lower()
        return tuple(
            r
            for r in self.relationships
            if (r.source.lower() == key or r.target.lower() == key)
            and (kind is None or r.kind == kind)
        )


def _normalise_trades(trades: Iterable[Any], as_of: datetime | None = None) -> list[TradeEntity]:
    cutoff = _cutoff(as_of)
    result: list[TradeEntity] = []
    for row in trades:
        wallet = str(_get(row, "wallet_address", _get(row, "trader_address", ""))).lower()
        token = str(_get(row, "token_address", _get(row, "token", ""))).lower()
        timestamp = _time(_get(row, "timestamp", _get(row, "block_timestamp")))
        side = str(getattr(_get(row, "side", ""), "value", _get(row, "side", ""))).upper()
        if (
            not wallet
            or not token
            or not timestamp
            or timestamp > cutoff
            or side not in {"BUY", "SELL"}
        ):
            continue
        metadata = _get(row, "metadata_json", _get(row, "metadata", {})) or {}
        result.append(
            TradeEntity(
                wallet,
                token,
                side,
                timestamp,
                _dec(_get(row, "usd_value", _get(row, "value_usd"))),
                metadata if isinstance(metadata, Mapping) else {},
            )
        )
    return sorted(result, key=lambda t: (t.timestamp, t.wallet, t.token, t.side))


def build_wallet_knowledge_graph(
    trades: Iterable[Any],
    *,
    chain: str = DEFAULT_CHAIN,
    as_of: datetime | None = None,
    sync_window: timedelta = timedelta(minutes=5),
    max_wallets_per_token: int = 500,
    max_events_per_token: int = 2_000,
    max_graph_nodes: int = 2_000,
    max_graph_edges: int = 10_000,
    max_relationships_per_wallet: int = 1_000,
    max_total_nodes: int | None = None,
    max_wallet_nodes: int | None = None,
    max_token_nodes: int | None = None,
    max_trade_nodes: int | None = None,
    max_source_events: int | None = None,
    max_pair_checks: int | None = None,
) -> WalletKnowledgeGraph:
    chain = validate_bsc_chain(chain)
    if (
        min(
            max_wallets_per_token,
            max_events_per_token,
            max_graph_nodes,
            max_graph_edges,
            max_relationships_per_wallet,
        )
        < 1
    ):
        raise ValueError("graph limits must be positive")
    enforce_total = max_total_nodes is not None or any(
        value is not None
        for value in (
            max_wallet_nodes,
            max_token_nodes,
            max_trade_nodes,
            max_source_events,
            max_pair_checks,
        )
    )
    # ``max_graph_nodes`` is the historical per-kind limit. The explicit
    # ``max_total_nodes`` knob enables the Phase 8 total-node contract while
    # preserving callers that still rely on the legacy argument semantics.
    total_limit = max_total_nodes if max_total_nodes is not None else max_graph_nodes * 3
    wallet_limit = max_graph_nodes if max_wallet_nodes is None else max_wallet_nodes
    token_limit = max_graph_nodes if max_token_nodes is None else max_token_nodes
    trade_limit = max_graph_nodes if max_trade_nodes is None else max_trade_nodes
    source_limit = (
        max_events_per_token * max(1, token_limit)
        if max_source_events is None
        else max_source_events
    )
    pair_limit = max(1, max_graph_edges * 8) if max_pair_checks is None else max_pair_checks
    if min(total_limit, wallet_limit, token_limit, trade_limit, source_limit, pair_limit) < 1:
        raise ValueError("graph limits must be positive")
    source_rows = list(islice(trades, source_limit + 1))
    source_truncated = len(source_rows) > source_limit
    all_events = _normalise_trades(source_rows[:source_limit], as_of)
    all_by_token: dict[str, list[TradeEntity]] = defaultdict(list)
    for event in all_events:
        all_by_token[event.token].append(event)

    # Bound the materialized event set before any relationship work. This is
    # the important guard for a hot token: later pair generation never sees an
    # unbounded list even when the caller supplied millions of trades.
    truncated = False
    truncation: dict[str, int] = {
        "node_limit": total_limit,
        "total_node_limit": total_limit,
        "wallet_node_limit": wallet_limit,
        "token_node_limit": token_limit,
        "trade_node_limit": trade_limit,
        "edge_limit": max_graph_edges,
        "event_limit_per_token": max_events_per_token,
        "wallet_limit_per_token": max_wallets_per_token,
        "events_examined": len(all_events),
        "events_included": 0,
        "events_dropped": 0,
        "tokens_examined": len(all_by_token),
        "tokens_included": 0,
        "pair_checks": 0,
        "source_events_examined": len(source_rows),
        "source_events_dropped": 1 if source_truncated else 0,
    }
    truncated = source_truncated
    by_token: dict[str, list[TradeEntity]] = {}
    for token in sorted(all_by_token):
        token_events = all_by_token[token]
        if len(token_events) > max_events_per_token:
            truncated = True
            truncation["events_dropped"] += len(token_events) - max_events_per_token
        by_token[token] = token_events[:max_events_per_token]
    events = [event for token in sorted(by_token) for event in by_token[token]]
    truncation["events_included"] = len(events)
    # Token nodes are capped independently from event materialization.
    truncation["tokens_included"] = min(len(by_token), token_limit)
    truncation["tokens_dropped"] = max(0, len(by_token) - token_limit)

    wallet_addresses = {t.wallet for t in events}
    for event in events:
        for key in ("funded_by", "funding_source", "followed_wallet", "followed_by"):
            value = event.metadata.get(key)
            if value:
                wallet_addresses.add(str(value).lower())
    if len(wallet_addresses) > wallet_limit or len(by_token) > token_limit:
        truncated = True
    wallets = tuple(WalletEntity(a, chain) for a in sorted(wallet_addresses)[:wallet_limit])
    tokens = tuple(TokenEntity(a, chain) for a in sorted(by_token)[:token_limit])
    if len(wallets) + len(tokens) > total_limit:
        truncated = True
        token_count = min(len(tokens), max(0, total_limit - len(wallets)))
        tokens = tokens[:token_count]
        if len(wallets) + len(tokens) > total_limit:
            wallets = wallets[: max(0, total_limit - len(tokens))]
    allowed_wallets = {item.address for item in wallets}
    allowed_tokens = {item.address for item in tokens}
    relations: list[Relationship] = []
    aggregates: dict[tuple[str, str, str], dict[str, Any]] = {}

    # Keep pair work bounded independently of the edge output cap. Without a
    # check budget, a same-timestamp burst could still spend quadratic time
    # discovering pairs that the edge cap would later discard.
    pair_budget = pair_limit

    # The total node cap applies across wallet, token, and trade nodes.
    remaining_trade_nodes = (
        max(0, total_limit - len(wallets) - len(tokens)) if enforce_total else trade_limit
    )
    included_trade_limit = min(trade_limit, remaining_trade_nodes)
    if len(events) > included_trade_limit:
        truncated = True
        truncation["events_dropped"] += len(events) - included_trade_limit
        events = events[:included_trade_limit]
    truncation["events_included"] = len(events)

    def add(
        source: str,
        target: str,
        kind: str,
        weight: Decimal = Decimal(1),
        evidence: Mapping[str, Any] | None = None,
    ) -> None:
        nonlocal truncated
        source, target = str(source).lower(), str(target).lower()
        if kind == "bought_token":
            if source not in allowed_wallets or target not in allowed_tokens:
                truncated = True
                return
        elif source not in allowed_wallets or target not in allowed_wallets:
            truncated = True
            return
        if len(aggregates) >= max_graph_edges and (source, target, kind) not in aggregates:
            truncated = True
            return
        key = (source, target, kind)
        item = aggregates.setdefault(
            key,
            {
                "weight": Decimal(0),
                "count": 0,
                "first": None,
                "last": None,
                "tokens": set(),
                "sample": [],
            },
        )
        item["weight"] += weight
        item["count"] += 1
        timestamp = _time((evidence or {}).get("timestamp"))
        if timestamp is not None:
            item["first"] = min(item["first"], timestamp) if item["first"] else timestamp
            item["last"] = max(item["last"], timestamp) if item["last"] else timestamp
        token = (evidence or {}).get("token")
        if token:
            item["tokens"].add(str(token))
        token_list = (evidence or {}).get("tokens", ())
        if isinstance(token_list, (str, bytes)):
            token_list = (token_list,)
        elif not isinstance(token_list, Iterable):
            token_list = ()
        for token in token_list:
            item["tokens"].add(str(token))
        if evidence and len(item["sample"]) < 5:
            item["sample"].append(dict(evidence))

    for event in events:
        if event.side == "BUY":
            add(
                event.wallet,
                event.token,
                "bought_token",
                evidence={
                    "token": event.token,
                    "timestamp": event.timestamp.isoformat(),
                    "value": str(event.value),
                },
            )
        meta = event.metadata
        funded = meta.get("funded_by") or meta.get("funding_source")
        followed = meta.get("followed_wallet") or meta.get("followed_by")
        if funded:
            add(
                str(funded).lower(),
                event.wallet,
                "funded_wallet",
                evidence={"timestamp": event.timestamp.isoformat()},
            )
        if followed:
            add(
                event.wallet,
                str(followed).lower(),
                "followed_wallet",
                evidence={"timestamp": event.timestamp.isoformat()},
            )

    def emit_temporal_pairs(
        items: list[TradeEntity], kind: str, *, bidirectional: bool = False
    ) -> None:
        nonlocal truncated
        active: deque[TradeEntity] = deque()
        window_seconds = sync_window.total_seconds()
        for current in items:
            while (
                active
                and (current.timestamp - active[0].timestamp).total_seconds() > window_seconds
            ):
                active.popleft()
            for previous in active:
                if previous.wallet == current.wallet:
                    continue
                if truncation["pair_checks"] >= pair_budget:
                    truncated = True
                    return
                truncation["pair_checks"] += 1
                delta = abs((current.timestamp - previous.timestamp).total_seconds())
                evidence = {
                    "token": current.token,
                    "delta_seconds": int(delta),
                    "timestamp": min(previous.timestamp, current.timestamp).isoformat(),
                }
                weight = Decimal(1) / (Decimal(1) + Decimal(str(delta)))
                add(previous.wallet, current.wallet, kind, weight, evidence)
                if bidirectional:
                    add(current.wallet, previous.wallet, kind, weight, evidence)
            active.append(current)

    for token, token_events in by_token.items():
        buyers = sorted({e.wallet for e in token_events if e.side == "BUY"})
        if len(buyers) > max_wallets_per_token:
            buyers = buyers[:max_wallets_per_token]
            truncated = True
        buys = [e for e in token_events if e.side == "BUY" and e.wallet in buyers]
        emit_temporal_pairs(buys, "traded_together", bidirectional=True)
        emit_temporal_pairs([e for e in token_events if e.side == "BUY"], "synchronized_entry")
        emit_temporal_pairs([e for e in token_events if e.side == "SELL"], "coordinated_exit")

    by_wallet_tokens: dict[str, set[str]] = defaultdict(set)
    for event in events:
        if (
            event.side == "BUY"
            and event.wallet in allowed_wallets
            and event.token in allowed_tokens
        ):
            by_wallet_tokens[event.wallet].add(event.token)
    # Invert the wallet/token relation so repeated co-buy evidence does not
    # scan every wallet pair when most wallets have disjoint token sets.
    token_wallets: dict[str, set[str]] = defaultdict(set)
    for wallet, wallet_tokens in by_wallet_tokens.items():
        for token in wallet_tokens:
            token_wallets[token].add(wallet)
    shared_tokens: dict[tuple[str, str], list[str]] = defaultdict(list)
    for token in sorted(token_wallets):
        wallets_for_token = sorted(token_wallets[token])[:max_wallets_per_token]
        if len(token_wallets[token]) > max_wallets_per_token:
            truncated = True
        for index, wallet_a in enumerate(wallets_for_token):
            for wallet_b in wallets_for_token[index + 1 :]:
                if truncation["pair_checks"] >= pair_budget:
                    truncated = True
                    break
                truncation["pair_checks"] += 1
                shared_tokens[(wallet_a, wallet_b)].append(token)
            if truncation["pair_checks"] >= pair_budget:
                break
        if truncation["pair_checks"] >= pair_budget:
            break
    for (wallet_a, wallet_b), shared in sorted(shared_tokens.items()):
        if len(shared) < 2:
            continue
        first_timestamp = min(
            event.timestamp
            for event in events
            if event.wallet in {wallet_a, wallet_b}
            and event.token in shared
            and event.side == "BUY"
        )
        add(
            wallet_a,
            wallet_b,
            "repeated_co_buy",
            Decimal(len(shared)),
            evidence={
                "tokens": shared,
                "count": len(shared),
                "timestamp": first_timestamp.isoformat(),
            },
        )

    degree: dict[str, int] = defaultdict(int)
    for key, item in list(aggregates.items()):
        if len(relations) >= max_graph_edges:
            truncated = True
            break
        source, target, kind = key
        if (
            degree[source] >= max_relationships_per_wallet
            or degree[target] >= max_relationships_per_wallet
        ):
            truncated = True
            continue
        evidence = {
            "evidence_count": item["count"],
            "event_count": item["count"],
            "token_count": len(item["tokens"]),
            "tokens": sorted(item["tokens"])[:20],
            "first_seen_at": item["first"].isoformat() if item["first"] else None,
            "last_seen_at": item["last"].isoformat() if item["last"] else None,
            "samples": item["sample"],
        }
        relations.append(Relationship(source, target, kind, item["weight"], evidence))
        degree[source] += 1
        degree[target] += 1
    reasons: list[str] = []
    if truncation["source_events_dropped"]:
        reasons.append("source_event_limit")
    if truncation["tokens_dropped"]:
        reasons.append("token_node_limit")
    if len(wallet_addresses) > wallet_limit:
        reasons.append("wallet_node_limit")
    if truncation["events_dropped"]:
        reasons.append("trade_node_limit")
    if len(relations) >= max_graph_edges:
        reasons.append("edge_limit")
    if truncation["pair_checks"] >= pair_budget:
        reasons.append("pair_check_limit")
    if enforce_total and len(wallets) + len(tokens) + len(events) >= total_limit:
        reasons.append("total_node_limit")
    if reasons:
        truncation["truncation_reasons"] = tuple(dict.fromkeys(reasons))
    return WalletKnowledgeGraph(
        wallets, tokens, tuple(events), tuple(relations), truncated, truncation
    )


@dataclass(frozen=True)
class ClusterIntelligence:
    cluster_id: str
    wallets: tuple[str, ...]
    evidence: Mapping[str, int]
    confidence: Decimal
    cluster_version: str = "v1"
    member_count: int = 0


def detect_wallet_clusters(
    graph: WalletKnowledgeGraph, *, min_shared_signals: int = 1
) -> tuple[ClusterIntelligence, ...]:
    adjacency: dict[str, set[str]] = defaultdict(set)
    evidence: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: defaultdict(int))
    cluster_kinds = {
        "funded_wallet",
        "funded_by",
        "traded_together",
        "synchronized_entry",
        "coordinated_exit",
        "repeated_co_buy",
    }
    for rel in graph.relationships:
        if rel.kind not in cluster_kinds:
            continue
        a, b = sorted((rel.source.lower(), rel.target.lower()))
        if a == b:
            continue
        adjacency[a].add(b)
        adjacency[b].add(a)
        evidence[(a, b)][rel.kind] += 1
    result: list[ClusterIntelligence] = []
    visited: set[str] = set()
    for start in sorted(adjacency):
        if start in visited:
            continue
        stack, members = [start], set()
        while stack:
            current = stack.pop()
            if current in visited:
                continue
            visited.add(current)
            members.add(current)
            stack.extend(adjacency[current] - visited)
        if len(members) < 2:
            continue
        pair_evidence = [evidence[pair] for pair in evidence if set(pair).issubset(members)]
        counts: dict[str, int] = defaultdict(int)
        for item in pair_evidence:
            for kind, count in item.items():
                counts[kind] += count
        signals = sum(counts.values())
        if signals < min_shared_signals:
            continue
        confidence = min(Decimal(1), Decimal(signals) / Decimal(max(1, len(members) * 2)))
        canonical = "|".join(
            (
                graph.wallets[0].chain if graph.wallets else DEFAULT_CHAIN,
                *sorted(members),
                "cluster-v1",
            )
        )
        cluster_id = "cluster_" + hashlib.sha256(canonical.encode()).hexdigest()[:16]
        result.append(
            ClusterIntelligence(
                cluster_id, tuple(sorted(members)), dict(counts), confidence, "v1", len(members)
            )
        )
    return tuple(result)


@dataclass(frozen=True)
class SmartMoneyAlphaScore:
    score: Decimal
    historical_skill: Decimal
    current_behavior: Decimal
    network_influence: Decimal
    risk_adjustment: Decimal
    contributions: Mapping[str, Decimal]
    explanation: Mapping[str, str]


def calculate_smart_money_alpha(
    profile: Any,
    *,
    network_influence: Decimal | float = 0,
    risk_adjustment: Decimal | float | None = None,
) -> SmartMoneyAlphaScore:
    def value(name: str, default: Any = 0) -> Decimal:
        return _dec(
            profile.get(name, default)
            if isinstance(profile, Mapping)
            else getattr(profile, name, default)
        )

    skill = min(
        Decimal(1),
        max(
            Decimal(0),
            value("win_rate") * Decimal("0.4")
            + min(Decimal(1), max(Decimal(0), value("realized_roi"))) * Decimal("0.35")
            + value("consistency") * Decimal("0.25"),
        ),
    )
    behavior = min(
        Decimal(1),
        max(
            Decimal(0),
            value("entry_timing", value("early_entry_rate")) * Decimal("0.5")
            + min(Decimal(1), value("exit_efficiency")) * Decimal("0.25")
            + min(Decimal(1), value("holding_period") / Decimal(86400) / Decimal(30))
            * Decimal("0.25"),
        ),
    )
    network = min(Decimal(1), max(Decimal(0), _dec(network_influence)))
    if risk_adjustment is not None:
        risk = min(Decimal(1), max(Decimal(0), _dec(risk_adjustment)))
    else:
        risk_state = str(
            profile.get("risk_state", "")
            if isinstance(profile, Mapping)
            else getattr(profile, "risk_state", "")
        ).upper()
        sample_size = value("sample_size")
        # Missing risk evidence is explicitly non-safe. A profile with no
        # observations cannot receive the maximum risk-adjustment contribution.
        if risk_state in {"UNKNOWN", "UNAVAILABLE", "MISSING"} or sample_size <= 0:
            risk = Decimal(0)
        else:
            risk = min(
                Decimal(1),
                max(
                    Decimal(0),
                    Decimal(1)
                    - value("rug_exposure") * Decimal("0.6")
                    - value("bot_probability") * Decimal("0.2")
                    - value("wash_probability", value("wash_trading_probability")) * Decimal("0.2"),
                ),
            )
    contributions = {
        "historical_skill": (skill * Decimal("0.4") * 100).quantize(Decimal("0.01")),
        "current_behavior": (behavior * Decimal("0.25") * 100).quantize(Decimal("0.01")),
        "network_influence": (network * Decimal("0.2") * 100).quantize(Decimal("0.01")),
        "risk_adjustment": (risk * Decimal("0.15") * 100).quantize(Decimal("0.01")),
    }
    score = sum(contributions.values(), Decimal(0)).quantize(Decimal("0.01"))
    explanation = {
        "historical_skill": "40% from win rate, realized ROI and consistency",
        "current_behavior": "25% from entry timing, exit efficiency and holding period",
        "network_influence": "20% from explainable graph connections and cluster reach",
        "risk_adjustment": "15% penalty-aware adjustment for rugs, bots and wash trading",
    }
    return SmartMoneyAlphaScore(score, skill, behavior, network, risk, contributions, explanation)


FLOW_WINDOWS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "24h": 86400}


def smart_money_flow(
    trades: Iterable[Any],
    *,
    token: str,
    smart_wallets: Iterable[str],
    window: str = "24h",
    as_of: datetime | None = None,
) -> dict[str, Any]:
    if window not in FLOW_WINDOWS:
        raise ValueError(f"window must be one of {sorted(FLOW_WINDOWS)}")
    cutoff = _cutoff(as_of)
    start = cutoff - timedelta(seconds=FLOW_WINDOWS[window])
    selected = {w.lower() for w in smart_wallets}
    inflow = outflow = Decimal(0)
    matched = 0
    for event in _normalise_trades(trades, cutoff):
        if event.token != token.lower() or event.wallet not in selected or event.timestamp < start:
            continue
        matched += 1
        if event.side == "BUY":
            inflow += event.value
        else:
            outflow += event.value
    net = inflow - outflow
    return {
        "token": token,
        "window": window,
        "as_of": cutoff,
        "inflow": inflow,
        "outflow": outflow,
        "net_flow": net,
        "accumulation": net > 0,
        "distribution": net < 0,
        "data_status": "OBSERVED" if matched else "UNKNOWN",
    }


def replay_historical_smart_money(
    successful_tokens: Iterable[Any],
    trades: Iterable[Any],
    *,
    as_of: datetime | None = None,
    entry_window: timedelta = timedelta(hours=24),
) -> dict[str, Any]:
    cutoff = _cutoff(as_of)
    graph = build_wallet_knowledge_graph(trades, as_of=cutoff)
    winners = []
    for token in successful_tokens:
        address = str(_get(token, "address", _get(token, "token_address", ""))).lower()
        first_seen = _time(_get(token, "first_seen_at", _get(token, "created_at")))
        multiple = _dec(_get(token, "ath_multiple", 0))
        if not address or not first_seen or first_seen > cutoff:
            continue
        entrants = sorted(
            {
                e.wallet
                for e in graph.trades
                if e.token == address
                and e.side == "BUY"
                and first_seen <= e.timestamp <= first_seen + entry_window
            }
        )
        winners.append(
            {
                "token": address,
                "ath_multiple": multiple,
                "wallets": entrants,
                "entry_time": first_seen,
            }
        )
    appearances: dict[str, int] = defaultdict(int)
    for row in winners:
        for wallet in row["wallets"]:
            appearances[wallet] += 1
    return {
        "as_of": cutoff,
        "successful_tokens": winners,
        "recurring_wallets": sorted(appearances, key=lambda w: (-appearances[w], w)),
        "wallet_appearances": dict(appearances),
        "clusters": [c.__dict__ for c in detect_wallet_clusters(graph)],
    }


__all__ = [
    "FLOW_WINDOWS",
    "ClusterEntity",
    "ClusterIntelligence",
    "Relationship",
    "SmartMoneyAlphaScore",
    "TokenEntity",
    "TradeEntity",
    "WalletEntity",
    "WalletKnowledgeGraph",
    "build_wallet_knowledge_graph",
    "calculate_smart_money_alpha",
    "detect_wallet_clusters",
    "replay_historical_smart_money",
    "smart_money_flow",
]
