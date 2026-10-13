from datetime import timedelta

import pytest
from sqlalchemy import select
from test_phase9_alpha_baseline import PAIR, T0, TOKEN, _pair, _session

from smms.api.alpha_metadata import load_alpha_metadata
from smms.config.settings import Settings
from smms.db.models import AlphaSnapshot, Pair, Token
from smms.market.snapshots import materialize_dexscreener_snapshot
from smms.radar.engine import RadarEngine
from smms.radar.views import candidate_detail, candidate_view
from smms.runtime import materialize_dexscreener_enrichment


def alpha_row():
    return AlphaSnapshot(chain="bsc", token_address=TOKEN, pair_address=PAIR,
                         pair_created_at=T0, snapshot_at=T0, alpha_score=20)


@pytest.mark.parametrize("symbol,name", [(None, None), ("—", "Unknown token"), ("  ", "  ")])
def test_discovered_token_gets_provider_identity_in_both_views(symbol, name):
    with _session() as session:
        _pair(session)
        token = session.scalar(select(Token).where(Token.address == TOKEN))
        token.symbol, token.name = symbol, name
        pair = session.scalar(select(Pair).where(Pair.address == PAIR))
        candidate = RadarEngine(session).ingest(pair, at=T0)[0]
        settings = Settings(_env_file=None)
        assert materialize_dexscreener_enrichment(session, [{
            "token_address": TOKEN, "pair_address": PAIR,
            "symbol": "DOGEAI", "name": "Doge AI",
        }], settings=settings) == 1
        alpha = load_alpha_metadata(session, [alpha_row()])[(TOKEN, PAIR)]
        assert alpha["name"] == "Doge AI"
        assert alpha["symbol"] == "DOGEAI"
        assert candidate_view(session, candidate)["name"] == "Doge AI"
        detail = candidate_detail(session, settings.radar, 56, "bsc", TOKEN, at=T0)
        assert detail["name"] == "Doge AI"


def test_alpha_can_read_identity_from_exact_pair_snapshot_without_token_metadata():
    with _session() as session:
        _pair(session)
        token = session.scalar(select(Token).where(Token.address == TOKEN))
        token.symbol, token.name = None, None
        materialize_dexscreener_snapshot(session, {
            "token_address": TOKEN, "pair_address": PAIR,
            "symbol": "DOGEAI", "name": "Doge AI",
        }, as_of=T0)
        materialize_dexscreener_snapshot(session, {
            "token_address": TOKEN, "pair_address": "other-pair",
            "symbol": "WRONG", "name": "Wrong Pair",
        }, as_of=T0 + timedelta(seconds=1))
        metadata = load_alpha_metadata(session, [alpha_row()])[(TOKEN, PAIR)]
        assert metadata["name"] == "Doge AI"
        assert metadata["symbol"] == "DOGEAI"


def test_radar_includes_known_name_when_symbol_is_missing():
    with _session() as session:
        _pair(session)
        token = session.scalar(select(Token).where(Token.address == TOKEN))
        token.symbol, token.name = None, "果蝇"
        pair = session.scalar(select(Pair).where(Pair.address == PAIR))
        candidate = RadarEngine(session).ingest(pair, at=T0)[0]
        assert candidate_view(session, candidate)["name"] == "果蝇"


def test_secondary_identity_preserves_known_fields_and_rejects_undiscovered_pairs():
    with _session() as session:
        _pair(session)
        token = session.scalar(select(Token).where(Token.address == TOKEN))
        token.symbol, token.name = "CHAIN", "On-chain Name"
        settings = Settings(_env_file=None)
        materialize_dexscreener_enrichment(session, [{
            "token_address": TOKEN, "pair_address": PAIR,
            "symbol": "DEX", "name": "Secondary Name",
        }], settings=settings)
        metadata = load_alpha_metadata(session, [alpha_row()])[(TOKEN, PAIR)]
        assert metadata["name"] == "On-chain Name"
        assert metadata["symbol"] == "CHAIN"
        token.symbol, token.name = None, None
        assert materialize_dexscreener_enrichment(session, [{
            "token_address": TOKEN, "pair_address": "undiscovered-pair",
            "symbol": "WRONG", "name": "Wrong Pair",
        }], settings=settings) == 0
        assert token.symbol is None and token.name is None


def test_secondary_identity_keeps_unknown_metadata_missing():
    with _session() as session:
        _pair(session)
        token = session.scalar(select(Token).where(Token.address == TOKEN))
        token.symbol, token.name = None, None
        materialize_dexscreener_enrichment(session, [{
            "token_address": TOKEN, "pair_address": PAIR,
            "symbol": "—", "name": "Unknown token",
        }], settings=Settings(_env_file=None))
        metadata = load_alpha_metadata(session, [alpha_row()])[(TOKEN, PAIR)]
        assert metadata["name"] is None
        assert metadata["symbol"] is None
        assert token.symbol is None and token.name is None
