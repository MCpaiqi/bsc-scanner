"""Bounded presentation-only enrichment, separate from live ingestion/HTTP.

Run with --once for one batch, or leave running for 60-second refreshes.
Existing ERC-20 and DexScreener providers own all network decoding.
"""
import argparse
import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from smms.chains.bsc import BSCProvider
from smms.config import get_settings
from smms.db.models import Pair, Token
from smms.ingestion.market.dexscreener import DexScreenerMarketProvider
from smms.market.metadata import enrich_erc20_metadata
from smms.market.snapshots import materialize_dexscreener_snapshot


async def batch():
    settings = get_settings()
    engine = create_engine(settings.database_url.replace("+asyncpg", "+psycopg2"))
    try:
        with Session(engine) as session:
            pairs = session.scalars(select(Pair).where(
                Pair.chain_id == 56, Pair.canonical.is_(True), Pair.removed.is_(False),
                Pair.creation_timestamp >= datetime.now(UTC) - timedelta(minutes=15),
                Pair.quote_token.is_not(None),
            ).order_by(Pair.creation_timestamp.desc()).limit(20)).all()
            addresses = {a for p in pairs for a in (p.token0_address, p.token1_address) if a != p.quote_token}
            tokens = session.scalars(select(Token).where(Token.chain == "bsc", Token.address.in_(addresses))).all()
            provider = BSCProvider(settings, verify_on_use=True)
            updated = 0
            for token in tokens:
                if token.symbol and token.name:
                    continue
                try:
                    result = await asyncio.wait_for(enrich_erc20_metadata(provider, token.address, timeout_seconds=3, retries=0), timeout=5)
                    if result.get("symbol") and not token.symbol:
                        token.symbol = result["symbol"][:32]
                    if result.get("name") and not token.name:
                        token.name = result["name"][:128]
                    token.metadata_json = {**(token.metadata_json or {}), "presentation_metadata_source": "ERC20", "presentation_metadata_at": datetime.now(UTC).isoformat()}
                    # Decimals and all numerical/scoring facts are intentionally untouched.
                    updated += int(bool(result.get("symbol") or result.get("name")))
                except Exception as exc:  # noqa: BLE001 - optional enrichment must fail independently
                    print("erc20 presentation unavailable", type(exc).__name__, flush=True)
            session.commit()
            count = 0
            try:
                payload = await asyncio.wait_for(DexScreenerMarketProvider(timeout=5, rate_limit_per_second=1).fetch_pairs([p.address for p in pairs]), timeout=15)
                valid = {(a, p.address) for p in pairs for a in (p.token0_address, p.token1_address) if a != p.quote_token}
                for item in payload.get("tokens", []):
                    if (str(item.get("token_address", "")).lower(), str(item.get("pair_address", "")).lower()) in valid:
                        materialize_dexscreener_snapshot(session, item)
                        token = next((t for t in tokens if t.address == str(item.get("token_address", "")).lower()), None)
                        if token is not None:
                            if not token.symbol and item.get("symbol") not in (None, "—", "UNKNOWN"):
                                token.symbol = item["symbol"][:32]
                            if not token.name and item.get("name") not in (None, "Unknown token", "UNKNOWN"):
                                token.name = item["name"][:128]
                            token.metadata_json = {**(token.metadata_json or {}), "presentation_metadata_source": "DexScreener API"}
                        count += 1
                session.commit()
            except Exception as exc:  # noqa: BLE001 - optional enrichment must fail independently
                session.rollback()
                print("dex presentation unavailable", type(exc).__name__, flush=True)
            print(f"presentation batch pairs={len(pairs)} metadata_updated={updated} dex_snapshots={count}", flush=True)
    finally:
        engine.dispose()


async def main(once):
    while True:
        try:
            await batch()
        except Exception as exc:  # noqa: BLE001 - optional enrichment must fail independently
            print("presentation batch unavailable", type(exc).__name__, flush=True)
        if once:
            return
        await asyncio.sleep(60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    asyncio.run(main(parser.parse_args().once))
