"""Small adapter hooks; all mutations share the ingestion transaction."""

from sqlalchemy import select

from smms.config.settings import get_settings

from .audit import AuditQueue
from .engine import RadarEngine


def _candidate_state_for_row(sync, row):
    """Capture only candidates that a row can actually change.

    This hook is invoked on every canonical BSC log.  Reading the entire
    candidate table here makes catch-up cost grow with unrelated history and
    unnecessarily lengthens the transaction that owns ingestion locks.
    """
    from smms.db.models import Pair, RadarCandidate, Token

    if isinstance(row, Pair):
        addresses = (row.token0_address, row.token1_address)
        chain = "bsc"
    else:
        addresses = (getattr(row, "token_address", None),)
        chain = getattr(row, "chain", None) or "bsc"
    canonical_addresses = tuple(
        str(address).lower() for address in addresses if address
    )
    if not canonical_addresses:
        return {}
    return {
        candidate.id: {
            "status": candidate.status.value,
            "features": dict(candidate.features_json or {}),
            "signal_id": candidate.latest_signal_id,
        }
        for candidate in sync.scalars(
            select(RadarCandidate)
            .join(Token, Token.id == RadarCandidate.token_id)
            .where(Token.chain == chain, Token.address.in_(canonical_addresses))
        ).all()
    }


async def project(session, row):
    settings = get_settings()
    alert_events = []
    changes = []

    def apply(sync):
        from smms.db.models import Token
        from smms.radar.alerts import evaluate_enabled_rules

        engine = RadarEngine(sync, settings.radar, chain_id=settings.bsc_chain_id)
        before = _candidate_state_for_row(sync, row)
        result = engine.ingest(row)
        if not result:
            result = engine.refresh_for_event(row)
        for candidate in result:
            if candidate.status.value != "REJECTED" and candidate.features_json:
                AuditQueue(sync).schedule_candidate(candidate)
            token = sync.get(Token, candidate.token_id)
            if token:
                previous = before.get(candidate.id)
                if previous is None:
                    from smms.radar.live_forward import record_observation

                    record_observation(
                        sync,
                        candidate,
                        "MARKET_BASELINE",
                        at=candidate.discovered_at,
                        reason="RADAR_ELIGIBLE_UNIVERSE_SAMPLE",
                    )
                    changes.append(("CANDIDATE_DISCOVERED", token.chain, token.address, candidate))
                else:
                    old = previous["features"]
                    new = candidate.features_json or {}
                    changed = (
                        previous["status"] != candidate.status.value
                        or previous["signal_id"] != candidate.latest_signal_id
                        or old != new
                    )
                    if changed:
                        changes.append(("CANDIDATE_UPDATED", token.chain, token.address, candidate))
                    old_wallets = old.get("known_smart_wallet_count")
                    new_wallets = new.get("known_smart_wallet_count")
                    try:
                        smart_money_entry = (
                            new_wallets not in {None, "UNKNOWN"}
                            and (old_wallets in {None, "UNKNOWN"} or int(new_wallets) > int(old_wallets))
                        )
                    except (TypeError, ValueError):
                        smart_money_entry = False
                    if smart_money_entry:
                        changes.append(("SMART_MONEY_ENTRY", token.chain, token.address, candidate))
                    if old.get("risk") != new.get("risk"):
                        changes.append(("RISK_CHANGED", token.chain, token.address, candidate))
                    if previous["signal_id"] != candidate.latest_signal_id:
                        changes.append(("SIGNAL_CHANGED", token.chain, token.address, candidate))
                    if old.get("early_buyer_count") != new.get("early_buyer_count"):
                        changes.append(("EARLY_BUYER_UPDATE", token.chain, token.address, candidate))
                alert_events.extend(
                    (token.chain, token.address, alert)
                    for alert in evaluate_enabled_rules(
                        sync,
                        chain=token.chain,
                        token_address=token.address,
                        state=candidate.features_json or {},
                    )
                )
        return result

    result = await session.run_sync(apply)
    # Publish outside the DB mutation path so ingestion is never blocked by clients.
    try:
        from smms.radar.events import publish_domain_event

        for event_type, chain, address, candidate in changes:
            await publish_domain_event(
                event_type,
                chain=chain,
                token=address,
                data={
                    "token_address": address,
                    "chain": chain,
                    "status": candidate.status.value,
                    "priority_score": float(candidate.priority_score),
                    "features": candidate.features_json or {},
                    "signal_id": candidate.latest_signal_id,
                },
            )
        for chain, token, alert in alert_events:
            await publish_domain_event(
                "ALERT_TRIGGERED", chain=chain, token=token, data=alert
            )
    except (LookupError, TypeError, ValueError, RuntimeError):
        # Event delivery is best-effort; canonical state remains durable.
        pass
    return result


async def revalidate(session, raw):
    settings = get_settings()
    await session.run_sync(
        lambda sync: RadarEngine(
            sync, settings.radar, chain_id=settings.bsc_chain_id
        ).revalidate_raw(raw)
    )


async def refresh_deferred_rows(session, rows):
    """Refresh Radar once per token after a bounded ingestion chunk.

    Catch-up persists canonical Raw/Market/Trade facts first and coalesces
    repeated swaps for the same token.  This keeps Radar feature queries out
    of the per-log critical path while still materializing candidates and
    latest features before the chunk checkpoint is committed.
    """
    settings = get_settings()

    def apply(sync):
        engine = RadarEngine(sync, settings.radar, chain_id=settings.bsc_chain_id)
        pair_rows = {}
        event_rows = {}
        for row in rows:
            token_address = getattr(row, "token_address", None)
            if token_address:
                event_rows[str(token_address).lower()] = row
            else:
                for address in (
                    getattr(row, "token0_address", None),
                    getattr(row, "token1_address", None),
                ):
                    if address:
                        pair_rows[str(address).lower()] = row
        # PairCreated rows establish candidates; latest event rows then refresh
        # their features. Keep both when a token appears in one chunk's pair
        # admission and first swap.
        selected = list(pair_rows.values()) + list(event_rows.values())
        for row in selected:
            result = engine.ingest(row)
            if not result:
                engine.refresh_for_event(row)

    await session.run_sync(apply)
