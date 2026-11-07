"""Projection effects share their canonical ingestion transaction and outbox."""

from datetime import UTC, datetime

from sqlalchemy import and_, or_, select

from smms.config.settings import get_settings
from smms.db.models import MarketEvent, Pair, RadarCandidate, Token
from smms.evaluation import utc

from .audit import AuditQueue
from .engine import RadarEngine
from .outbox import enqueue_event


def _candidate_state_for_rows(sync, rows):
    subjects = set()
    for row in rows:
        addresses = (row.token0_address, row.token1_address) if isinstance(row, Pair) else (
            getattr(row, "token_address", None),)
        chain = "bsc" if isinstance(row, (Pair, MarketEvent)) else getattr(row, "chain", None) or "bsc"
        subjects.update((chain, str(address).lower()) for address in addresses if address)
    if not subjects:
        return {}
    by_chain = {}
    for chain, address in subjects:
        by_chain.setdefault(chain, set()).add(address)

    return {candidate.id: {"status": candidate.status.value, "features": dict(candidate.features_json or {}),
                           "signal_id": candidate.latest_signal_id}
            for candidate in sync.scalars(select(RadarCandidate).join(Token, Token.id == RadarCandidate.token_id)
                .where(or_(*(and_(Token.chain == chain, Token.address.in_(addresses))
                             for chain, addresses in by_chain.items())))
                .execution_options(populate_existing=True))}


def _candidate_state_for_row(sync, row):
    return _candidate_state_for_rows(sync, [row])


def _apply_effects(sync, candidates, before, *, at):
    from .alerts import evaluate_enabled_rules
    from .live_forward import record_observation
    from .projection import projection_for

    for candidate in candidates:
        projection = projection_for(sync, candidate)
        if candidate.projection_id is not None and projection is None:
            continue  # A concurrent reorg fenced the just-computed projection.
        if candidate.status.value != "REJECTED" and candidate.features_json:
            AuditQueue(sync).schedule_candidate(candidate, now=at)
        token = sync.get(Token, candidate.token_id)
        if token is None:
            continue
        previous = before.get(candidate.id)
        kinds = []
        if previous is None:
            record_observation(sync, candidate, "MARKET_BASELINE", at=candidate.discovered_at,
                               reason="RADAR_ELIGIBLE_UNIVERSE_SAMPLE")
            kinds.append("CANDIDATE_DISCOVERED")
        else:
            old, new = previous["features"], candidate.features_json or {}
            if (previous["status"] != candidate.status.value or previous["signal_id"] != candidate.latest_signal_id
                    or old != new):
                kinds.append("CANDIDATE_UPDATED")
            old_wallets, new_wallets = old.get("known_smart_wallet_count"), new.get("known_smart_wallet_count")
            try:
                entry = new_wallets not in {None, "UNKNOWN"} and (
                    old_wallets in {None, "UNKNOWN"} or int(new_wallets) > int(old_wallets))
            except (TypeError, ValueError):
                entry = False
            if entry:
                kinds.append("SMART_MONEY_ENTRY")
            if old.get("risk") != new.get("risk"):
                kinds.append("RISK_CHANGED")
            if previous["signal_id"] != candidate.latest_signal_id:
                kinds.append("SIGNAL_CHANGED")
            if old.get("early_buyer_count") != new.get("early_buyer_count"):
                kinds.append("EARLY_BUYER_UPDATE")
        for kind in kinds:
            enqueue_event(sync, kind, chain=token.chain, token=token.address, occurred_at=at,
                          effect_key=f"projection:{candidate.id}:{candidate.projection_id}:{kind}",
                          data={"token_address": token.address, "chain": token.chain,
                                "status": candidate.status.value, "priority_score": float(candidate.priority_score),
                                "features": candidate.features_json or {}, "signal_id": candidate.latest_signal_id,
                                "projection_id": candidate.projection_id,
                                "source_epoch": projection.source_epoch if projection else 0, "as_of": at})
        for alert in evaluate_enabled_rules(sync, chain=token.chain, token_address=token.address,
                                             state=candidate.features_json or {}, at=at):
            enqueue_alert(sync, chain=token.chain, token=token.address, alert=alert, at=at)


def enqueue_alert(sync, *, chain, token, alert, at=None):
    return enqueue_event(sync, "ALERT_TRIGGERED", chain=chain, token=token, data=alert,
                         effect_key=f"alert:{alert['history_id']}", occurred_at=at)


async def project(session, row, *, at=None):
    settings = get_settings()
    at = utc(at or datetime.now(UTC))

    def apply(sync):
        engine = RadarEngine(sync, settings.radar, chain_id=settings.bsc_chain_id)
        before = _candidate_state_for_row(sync, row)
        result = engine.ingest(row, at=at)
        if not result:
            result = engine.refresh_for_event(row, at=at)
        _apply_effects(sync, result, before, at=at)
        return result

    return await session.run_sync(apply)


async def revalidate(session, raw):
    settings = get_settings()
    at = datetime.now(UTC)

    def apply(sync):
        engine = RadarEngine(sync, settings.radar, chain_id=settings.bsc_chain_id)
        candidates = engine.candidates_for_raw(raw)
        before = {candidate.id: {"status": candidate.status.value,
                                 "features": dict(candidate.features_json or {}),
                                 "signal_id": candidate.latest_signal_id} for candidate in candidates}
        engine.revalidate_raw(raw, at=at, candidates=candidates)
        _apply_effects(sync, candidates, before, at=at)

    await session.run_sync(apply)


async def refresh_deferred_rows(session, rows, *, at=None):
    """Retain every proof, then project/effect each affected candidate once."""
    settings = get_settings()
    at = utc(at or datetime.now(UTC))

    def apply(sync):
        engine = RadarEngine(sync, settings.radar, chain_id=settings.bsc_chain_id)
        before = _candidate_state_for_rows(sync, rows)
        affected = set(before)
        for row in rows:
            affected.update(candidate.id for candidate in engine.ingest(row, at=at, refresh=False))
        candidates = []
        for candidate_id in sorted(affected):
            candidate = sync.get(RadarCandidate, candidate_id)
            engine.refresh(candidate, at=at)
            candidates.append(candidate)
        _apply_effects(sync, candidates, before, at=at)
        return candidates

    return await session.run_sync(apply)
