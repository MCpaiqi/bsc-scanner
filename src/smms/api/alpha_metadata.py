"""Batch presentation enrichment. Never participates in Alpha scoring."""
from urllib.parse import urlsplit

from sqlalchemy import func, select, tuple_

from smms.db.models import Pair, Token, TokenMarketSnapshot
from smms.market.identity import display_text


def safe_external_url(value):
    if not isinstance(value, str) or any(ord(c) < 32 for c in value):
        return None
    try:
        parsed = urlsplit(value)
        return value if parsed.scheme.lower() in {"http", "https"} and parsed.hostname and not parsed.username and not parsed.password else None
    except ValueError:
        return None


def load_alpha_metadata(session, rows):
    """Four bounded batch queries regardless of candidate count; exact pair match."""
    if not rows:
        return {}
    addresses = {r.token_address for r in rows}
    pair_addresses = {r.pair_address for r in rows}
    tokens = {t.address: t for t in session.scalars(select(Token).where(Token.chain == "bsc", Token.address.in_(addresses)))}
    pairs = {p.address: p for p in session.scalars(select(Pair).where(Pair.chain_id == 56, Pair.address.in_(pair_addresses), Pair.canonical.is_(True), Pair.removed.is_(False)))}
    quote_addresses = {p.quote_token for p in pairs.values() if p.quote_token}
    quotes = {t.address: t.symbol for t in session.scalars(select(Token).where(Token.chain == "bsc", Token.address.in_(quote_addresses)))}
    ranked = select(TokenMarketSnapshot.id, func.row_number().over(
        partition_by=(TokenMarketSnapshot.token_address, TokenMarketSnapshot.pair_address),
        order_by=(TokenMarketSnapshot.as_of.desc(), TokenMarketSnapshot.id.desc()),
    ).label("rank")).where(
        TokenMarketSnapshot.chain == "bsc", TokenMarketSnapshot.source == "DEXSCREENER",
        TokenMarketSnapshot.status == "KNOWN",
        tuple_(TokenMarketSnapshot.token_address, TokenMarketSnapshot.pair_address).in_({(r.token_address, r.pair_address) for r in rows}),
    ).subquery()
    snapshots = {(s.token_address, s.pair_address): s for s in session.scalars(select(TokenMarketSnapshot).join(ranked, ranked.c.id == TokenMarketSnapshot.id).where(ranked.c.rank == 1))}
    result = {}
    for row in rows:
        token, pair = tokens.get(row.token_address), pairs.get(row.pair_address)
        snapshot = snapshots.get((row.token_address, row.pair_address))
        data = snapshot.metadata_json or {} if snapshot else {}
        result[(row.token_address, row.pair_address)] = {
            "symbol": (display_text(token.symbol, row.token_address) if token else None) or display_text(data.get("symbol"), row.token_address),
            "name": (display_text(token.name, row.token_address) if token else None) or display_text(data.get("name"), row.token_address),
            "image_url": safe_external_url(data.get("image_url")),
            "dex_url": safe_external_url(data.get("dex_url")),
            "quote_symbol": {"0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c": "WBNB", "0x55d398326f99059ff775485246999027b3197955": "USDT", "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d": "USDC", "0xe9e7cea3dedca5984780bafc599bd69add087d56": "BUSD"}.get(pair.quote_token if pair else None) or display_text(data.get("quote_symbol")) or (display_text(quotes.get(pair.quote_token)) if pair else None),
            "dex": display_text(pair.dex if pair else snapshot.dex if snapshot else None),
        }
    return result
