import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from smms.alpha import (
    AlphaBacktest,
    AlphaDatasetBuilder,
    CoverageChunk,
    ResearchCoverageManifest,
    coverage_chunks_verified,
    derive_raw_swap_amounts,
)
from smms.alpha.live import process_live_alpha
from smms.db.models import AlphaSnapshot, Base, Pair, Token, TradeEvent
from smms.market.models import MarketEventRecord, TradeSide
from smms.market.pipeline import BSCMarketPipeline

T0 = datetime(2026, 1, 1, tzinfo=UTC)
TOKEN = "0x00000000000000000000000000000000000000aa"
OTHER_TOKEN = "0x00000000000000000000000000000000000000ab"
PAIR = "0x00000000000000000000000000000000000000bb"
QUOTE = "0x00000000000000000000000000000000000000dd"
WALLET = "0x00000000000000000000000000000000000000cc"


def _session() -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return Session(engine)


def _trade(
    at: datetime,
    *,
    tx: str,
    price_quote: str | None,
    quote: str,
    token: str | None = None,
    side: str = "BUY",
    pair: str = PAIR,
    token_address: str = TOKEN,
    wallet: str = WALLET,
    price: str | None = None,
    metadata: dict | None = None,
) -> TradeEvent:
    return TradeEvent(
        chain="bsc",
        token_address=token_address,
        pair_address=pair,
        trader_address=wallet,
        tx_hash=tx,
        event_index=0,
        block_hash=f"block-{tx}",
        block_number=1,
        timestamp=at,
        side=side,
        price=Decimal(price) if price is not None else None,
        price_quote=Decimal(price_quote) if price_quote is not None else None,
        quote_amount=Decimal(quote),
        token_amount=Decimal(token) if token is not None else None,
        source="fixture",
        canonical=True,
        removed=False,
        metadata_json=metadata or {},
    )


def _pair(
    session: Session,
    *,
    address: str = PAIR,
    quote: str = QUOTE,
    token: str = TOKEN,
    quote_symbol: str = "WBNB",
    token_symbol: str = "MEME",
) -> None:
    session.add_all(
        [
            Token(chain="bsc", address=token, symbol=token_symbol, decimals=18),
            Token(chain="bsc", address=quote, symbol=quote_symbol, decimals=18),
            Pair(
                chain_id=56,
                address=address,
                token0_address=token,
                token1_address=quote,
                quote_token=quote,
                dex="pancakeswap",
                creation_timestamp=T0,
                canonical=True,
                removed=False,
            ),
        ]
    )


def _builder(session: Session, *, covered: bool = False) -> AlphaDatasetBuilder:
    return AlphaDatasetBuilder(session, coverage_resolver=lambda _pair, _end: covered)


def test_price_quote_is_used_when_price_is_null() -> None:
    with _session() as session:
        _pair(session)
        session.add_all(
            [
                _trade(T0 + timedelta(seconds=10), tx="entry", price_quote="2", quote="4", token="2"),
                _trade(T0 + timedelta(minutes=2), tx="future", price_quote="20", quote="20", token="1"),
            ]
        )
        session.commit()

        sample = _builder(session).build(T0, T0, windows=("30s",))[0]

        assert sample.entry_price == Decimal(2)
        assert sample.labels["reached_10x_15m"] is True
        assert sample.labels["reached_10x_15m_status"] == "POSITIVE_CONFIRMED"


def test_quote_vwap_uses_real_quote_and_token_amounts_and_ignores_dust() -> None:
    with _session() as session:
        _pair(session)
        session.add_all(
            [
                _trade(T0 + timedelta(seconds=5), tx="dust", price_quote="1000", quote="0.001", token="0.000001"),
                _trade(T0 + timedelta(seconds=10), tx="one", price_quote="2", quote="2", token="1"),
                _trade(T0 + timedelta(seconds=20), tx="two", price_quote="4", quote="8", token="2"),
            ]
        )
        session.commit()

        sample = _builder(session).build(T0, T0, windows=("30s",))[0]

        assert sample.entry_price == Decimal(10) / Decimal(3)
        assert sample.entry_price_method == "quote_vwap_min_quote"


def test_repeated_buyer_does_not_make_buy_volume_unknown() -> None:
    with _session() as session:
        _pair(session)
        session.add_all(
            [
                _trade(T0 + timedelta(seconds=5), tx="one", price_quote="2", quote="1", token="0.5"),
                _trade(T0 + timedelta(seconds=10), tx="two", price_quote="2", quote="2", token="1"),
                _trade(T0 + timedelta(seconds=20), tx="three", price_quote="2", quote="3", token="1.5"),
            ]
        )
        session.commit()

        sample = _builder(session).build(T0, T0, windows=("30s",))[0]

        assert sample.features["unique_buyers"] == 1
        assert sample.features["buy_volume_quote"] == Decimal(6)


def test_confirmed_positive_survives_missing_later_coverage() -> None:
    with _session() as session:
        _pair(session)
        session.add_all(
            [
                _trade(T0 + timedelta(seconds=10), tx="entry", price_quote="1", quote="1", token="1"),
                _trade(T0 + timedelta(minutes=2), tx="ten-x", price_quote="10", quote="10", token="1"),
            ]
        )
        session.commit()

        sample = _builder(session, covered=False).build(T0, T0, windows=("1m",))[0]

        assert sample.labels["reached_10x_1h"] is True
        assert sample.labels["reached_10x_1h_status"] == "POSITIVE_CONFIRMED"
        assert sample.labels["reached_20x_1h"] is None
        assert sample.labels["reached_20x_1h_status"] == "COVERAGE_UNKNOWN"


def test_negative_requires_verified_horizon_coverage() -> None:
    with _session() as session:
        _pair(session)
        session.add_all(
            [
                _trade(T0 + timedelta(seconds=10), tx="entry", price_quote="1", quote="1", token="1"),
                _trade(T0 + timedelta(minutes=2), tx="two-x", price_quote="2", quote="2", token="1"),
            ]
        )
        session.commit()

        unknown = _builder(session, covered=False).build(T0, T0, windows=("1m",))[0]
        covered = _builder(session, covered=True).build(T0, T0, windows=("1m",))[0]

        assert unknown.labels["reached_10x_1h"] is None
        assert unknown.labels["reached_10x_1h_status"] == "COVERAGE_UNKNOWN"
        assert covered.labels["reached_10x_1h"] is False
        assert covered.labels["reached_10x_1h_status"] == "NEGATIVE_CONFIRMED"


def test_future_trade_cannot_enter_features_but_can_confirm_label() -> None:
    with _session() as session:
        _pair(session)
        session.add_all(
            [
                _trade(T0 + timedelta(seconds=10), tx="observed", price_quote="2", quote="2", token="1"),
                _trade(T0 + timedelta(minutes=1), tx="future", price_quote="22", quote="22", token="1"),
            ]
        )
        session.commit()

        sample = _builder(session).build(T0, T0, windows=("30s",))[0]

        assert sample.features["trade_count"] == 1
        assert sample.features["volume_quote"] == Decimal(2)
        assert sample.labels["reached_10x_15m"] is True


def test_mixed_quote_assets_are_never_combined() -> None:
    other_pair = "0x00000000000000000000000000000000000000ee"
    usdt = "0x00000000000000000000000000000000000000ff"
    with _session() as session:
        _pair(session)
        _pair(
            session,
            address=other_pair,
            quote=usdt,
            token=OTHER_TOKEN,
            quote_symbol="USDT",
        )
        session.add_all(
            [
                _trade(T0 + timedelta(seconds=10), tx="bnb-entry", price_quote="2", quote="2", token="1"),
                _trade(T0 + timedelta(minutes=2), tx="bnb-future", price_quote="20", quote="20", token="1"),
                _trade(T0 + timedelta(seconds=10), tx="usd-entry", pair=other_pair, token_address=OTHER_TOKEN, price_quote="100", quote="100", token="1"),
                _trade(T0 + timedelta(minutes=2), tx="usd-future", pair=other_pair, token_address=OTHER_TOKEN, price_quote="101", quote="101", token="1"),
            ]
        )
        session.commit()

        samples = _builder(session).build(T0, T0, windows=("30s",))
        by_pair = {sample.pair_address: sample for sample in samples}

        assert by_pair[PAIR].quote_token == QUOTE
        assert by_pair[PAIR].price_unit == "WBNB per MEME"
        assert by_pair[PAIR].labels["reached_10x_15m"] is True
        assert by_pair[other_pair].quote_token == usdt
        assert by_pair[other_pair].price_unit == "USDT per MEME"
        assert by_pair[other_pair].labels["reached_10x_15m"] is None


def test_buy_and_sell_price_quote_direction_are_quote_per_token() -> None:
    token0_is_quote = Pair(
        chain_id=56,
        address=PAIR,
        token0_address=QUOTE,
        token1_address=TOKEN,
        quote_token=QUOTE,
        dex="pancakeswap",
        creation_timestamp=T0,
    )
    buy = BSCMarketPipeline._attach_pair_token(
        MarketEventRecord(
            event_type="TRADE", chain_id=56, token=None, pair=PAIR, wallet=WALLET,
            tx_hash="buy", block=1, timestamp=T0, side=TradeSide.UNKNOWN, source="fixture",
            metadata={"amounts_raw": [200, 0, 0, 100]},
        ),
        token0_is_quote,
    )
    sell = BSCMarketPipeline._attach_pair_token(
        MarketEventRecord(
            event_type="TRADE", chain_id=56, token=None, pair=PAIR, wallet=WALLET,
            tx_hash="sell", block=1, timestamp=T0, side=TradeSide.UNKNOWN, source="fixture",
            metadata={"amounts_raw": [0, 100, 200, 0]},
        ),
        token0_is_quote,
    )

    assert buy.side == TradeSide.BUY
    assert buy.quote_amount / buy.token_amount == Decimal(2)
    assert sell.side == TradeSide.SELL
    assert sell.quote_amount / sell.token_amount == Decimal(2)


def test_backtest_reports_denominators_for_valid_labels_only() -> None:
    with _session() as session:
        _pair(session)
        session.add_all(
            [
                _trade(T0 + timedelta(seconds=10), tx="entry", price_quote="1", quote="1", token="1"),
                _trade(T0 + timedelta(minutes=61), tx="horizon", price_quote="11", quote="11", token="1"),
            ]
        )
        session.commit()

        samples = _builder(session, covered=True).build(T0, T0, windows=("1m",))
        report = AlphaBacktest(samples).report(observation_window="1m", horizon="1h")

        assert report["status"] == "OK"
        assert report["dataset"]["positive_labels"] == 1
        assert report["backtest"]["overall"]["10x_hit_rate"] == {
            "hits": 1,
            "denominator": 1,
            "rate": Decimal(1),
        }
        assert report["hit_tokens"][0]["token_address"] == TOKEN


def test_entry_unavailable_is_explicit() -> None:
    with _session() as session:
        _pair(session)
        session.add(_trade(T0 + timedelta(seconds=10), tx="missing", price_quote=None, quote="1"))
        session.commit()

        sample = _builder(session).build(T0, T0, windows=("30s",))[0]

        assert sample.entry_price is None
        assert sample.labels["reached_10x_15m_status"] == "ENTRY_PRICE_UNAVAILABLE"


def test_no_swap_pair_remains_in_the_research_universe() -> None:
    with _session() as session:
        _pair(session)
        session.commit()

        samples = _builder(session).build(T0, T0, windows=("30s",))

        assert len(samples) == 1
        assert samples[0].features["trade_count"] == 0


def test_raw_buy_amount_derivation_without_decimals() -> None:
    pair = Pair(
        chain_id=56, address=PAIR, token0_address=TOKEN, token1_address=QUOTE,
        quote_token=QUOTE, dex="pancakeswap", creation_timestamp=T0,
    )
    event = _trade(
        T0, tx="raw-buy", price_quote=None, quote="2", token=None,
        metadata={"amounts_raw": [0, 2_000_000_000_000_000_000, 1_000_000_000, 0]},
    )
    result = derive_raw_swap_amounts(event, pair)
    assert result is not None
    assert result.side == "BUY"
    assert result.raw_target_token_amount == Decimal(1_000_000_000)
    assert result.raw_quote_amount == Decimal(2_000_000_000_000_000_000)


def test_raw_sell_amount_derivation_and_token0_quote() -> None:
    pair = Pair(
        chain_id=56, address=PAIR, token0_address=QUOTE, token1_address=TOKEN,
        quote_token=QUOTE, dex="pancakeswap", creation_timestamp=T0,
    )
    event = _trade(
        T0, tx="raw-sell", price_quote=None, quote="2", token=None, side="SELL",
        metadata={"amounts_raw": [0, 1_000_000_000, 2_000_000_000_000_000_000, 0]},
    )
    result = derive_raw_swap_amounts(event, pair)
    assert result is not None
    assert result.side == "SELL"
    assert result.raw_target_token_amount == Decimal(1_000_000_000)
    assert result.raw_quote_amount == Decimal(2_000_000_000_000_000_000)


def test_malformed_raw_v2_amounts_are_unresolved() -> None:
    pair = Pair(
        chain_id=56, address=PAIR, token0_address=TOKEN, token1_address=QUOTE,
        quote_token=QUOTE, dex="pancakeswap", creation_timestamp=T0,
    )
    event = _trade(
        T0, tx="raw-bad", price_quote=None, quote="2", token=None,
        metadata={"amounts_raw": [1, 2, 3, 4]},
    )
    assert derive_raw_swap_amounts(event, pair) is None


def test_raw_ratio_unblocks_labels_and_keeps_single_price_basis() -> None:
    with _session() as session:
        _pair(session)
        session.add_all([
            _trade(
                T0 + timedelta(seconds=10), tx="raw-entry", price_quote=None,
                quote="2", token=None,
                metadata={"amounts_raw": [0, 2_000_000_000_000_000_000, 1_000_000_000, 0]},
            ),
            _trade(
                T0 + timedelta(minutes=2), tx="raw-future", price_quote=None,
                quote="20", token=None,
                metadata={"amounts_raw": [0, 20_000_000_000_000_000_000, 1_000_000_000, 0]},
            ),
        ])
        session.commit()
        sample = _builder(session).build(T0, T0, windows=("1m",))[0]
        assert sample.price_basis == "RAW_PAIR_RATIO"
        assert sample.entry_price == Decimal(2_000_000_000)
        assert sample.labels["reached_10x_1h"] is True


def test_raw_and_human_return_multiple_cancel_decimals() -> None:
    for decimals in (6, 9, 18):
        entry_raw = Decimal(2) * (Decimal(10) ** decimals)
        future_raw = Decimal(20) * (Decimal(10) ** decimals)
        assert future_raw / entry_raw == Decimal(10)


def test_raw_vwap_uses_quote_notional_for_dust_filtering() -> None:
    with _session() as session:
        _pair(session)
        session.add_all([
            _trade(
                T0 + timedelta(seconds=5), tx="raw-dust", price_quote=None,
                quote="0.001", token=None,
                metadata={"amounts_raw": [0, 1_000_000_000_000_000, 1, 0]},
            ),
            _trade(
                T0 + timedelta(seconds=10), tx="raw-one", price_quote=None,
                quote="2", token=None,
                metadata={"amounts_raw": [0, 2_000_000_000_000_000_000, 1_000_000_000, 0]},
            ),
        ])
        session.commit()
        sample = _builder(session).build(T0, T0, windows=("30s",))[0]
        assert sample.entry_price == Decimal(2_000_000_000)


def test_dust_tick_spike_does_not_create_realisable_10x() -> None:
    with _session() as session:
        _pair(session)
        session.add_all([
            _trade(T0 + timedelta(seconds=10), tx="entry", price_quote="1", quote="1", token="1"),
            _trade(
                T0 + timedelta(minutes=2), tx="dust-spike", price_quote="1000000",
                quote="0.00000212", token="0.00000000000212",
            ),
        ])
        session.commit()
        sample = AlphaDatasetBuilder(
            session, coverage_resolver=lambda _pair, _end: True,
            min_future_quote=Decimal("0.01"),
        ).build(T0, T0, windows=("30s",))[0]

        assert sample.labels["max_return_1h"] > Decimal(100000)
        assert sample.labels["max_realisable_return_1h"] is None
        assert sample.labels["max_realisable_return_1h_status"] == "DUST_ONLY"
        assert sample.labels["reached_10x_1h"] is False
        assert sample.labels["reached_10x_1h_status"] == "DUST_ONLY"


def test_realisable_return_uses_vwap_not_single_tick() -> None:
    with _session() as session:
        _pair(session)
        session.add_all([
            _trade(T0 + timedelta(seconds=10), tx="entry", price_quote="1", quote="1", token="1"),
            _trade(T0 + timedelta(minutes=2), tx="a", price_quote="10", quote="0.006", token="0.0006"),
            _trade(T0 + timedelta(minutes=2, seconds=10), tx="b", price_quote="2", quote="0.006", token="0.003"),
        ])
        session.commit()
        sample = AlphaDatasetBuilder(
            session, coverage_resolver=lambda _pair, _end: True,
            min_future_quote=Decimal("0.01"),
        ).build(T0, T0, windows=("30s",))[0]

        assert sample.labels["max_return_1h"] == Decimal(9)
        assert sample.labels["max_realisable_return_1h"] == Decimal("2.333333333333333333333333333")
        assert sample.labels["reached_10x_1h"] is False
        assert sample.labels["reached_3x_1h"] is True


def test_human_and_raw_price_basis_are_never_mixed() -> None:
    with _session() as session:
        _pair(session)
        session.add_all([
            _trade(T0 + timedelta(seconds=10), tx="human-entry", price_quote="1", quote="1", token="1"),
            _trade(
                T0 + timedelta(minutes=2), tx="raw-future", price_quote=None, quote="20", token=None,
                metadata={"amounts_raw": [0, 20_000_000_000_000_000_000, 1_000_000_000, 0]},
            ),
        ])
        session.commit()
        sample = _builder(session).build(T0, T0, windows=("30s",))[0]
        assert sample.price_basis == "HUMAN_QUOTE_PRICE"
        assert sample.labels["reached_10x_1h"] is None


def test_live_alpha_snapshot_is_point_in_time_and_idempotent() -> None:
    with _session() as session:
        from test_phase9_0_5_integrity import covered_pair

        from smms.db.models import AlphaCoverageReceipt

        covered_pair(session)
        proof = session.query(AlphaCoverageReceipt).one()
        proof.end_at = proof.completed_at = T0 + timedelta(seconds=300)
        session.add_all([
            _trade(T0 + timedelta(seconds=10), tx="early", price_quote="1", quote="1", token="1"),
            _trade(T0 + timedelta(seconds=45), tx="late", price_quote="100", quote="10", token="0.1"),
        ])
        session.commit()
        assert process_live_alpha(session, now=T0 + timedelta(seconds=300)) == 4
        session.commit()
        rows = session.query(AlphaSnapshot).order_by(AlphaSnapshot.observation_seconds).all()
        assert [row.observation_seconds for row in rows] == [30, 60, 180, 300]
        assert rows[0].features_json["trade_count"] == 1
        assert rows[1].features_json["trade_count"] == 2
        assert process_live_alpha(session, now=T0 + timedelta(seconds=300)) == 0


def test_unresolved_raw_trade_does_not_create_partial_vwap() -> None:
    with _session() as session:
        _pair(session)
        session.add(_trade(
            T0 + timedelta(seconds=10), tx="bad-raw", price_quote=None, quote="1", token=None,
            metadata={"amounts_raw": [1, 2, 3, 4]},
        ))
        session.commit()
        sample = _builder(session).build(T0, T0, windows=("30s",))[0]
        assert sample.entry_price is None
        assert sample.price_basis == "RAW_AMOUNT_UNRESOLVED"


def test_coverage_manifest_requires_all_chunks_and_accepts_zero_logs() -> None:
    assert coverage_chunks_verified([
        CoverageChunk(1, 2, successful=True, persisted=True, raw_log_count=0),
    ])
    assert not coverage_chunks_verified([
        CoverageChunk(1, 2, successful=False, persisted=False),
    ])
    common = {
        "chain_id": 56,
        "protocol": "pancakeswap-v2",
        "factory": None,
        "start_block": 1,
        "end_block": 2,
        "start_timestamp": T0,
        "end_timestamp": T0 + timedelta(hours=1),
        "pair_created_query_complete": True,
        "swap_query_complete": True,
        "relevant_pair_count": 0,
        "rpc_source": "fixture",
        "query_parameters": {},
        "started_at": T0,
        "completed_at": T0,
    }
    assert ResearchCoverageManifest.from_chunks(
        **common,
        chunks=[CoverageChunk(1, 2, successful=True, persisted=True)],
    ).status == "VERIFIED"
    assert ResearchCoverageManifest.from_chunks(
        **common,
        chunks=[CoverageChunk(1, 2, successful=False, persisted=False)],
    ).status == "INCOMPLETE"


def test_verified_manifest_permits_negative_but_incomplete_does_not() -> None:
    kwargs = {
        "chain_id": 56, "protocol": "pancakeswap-v2", "factory": None,
        "start_block": 1, "end_block": 100, "start_timestamp": T0,
        "end_timestamp": T0 + timedelta(hours=2), "pair_created_query_complete": True,
        "swap_query_complete": True, "relevant_pair_count": 1, "raw_log_count": 1,
        "decoded_swap_count": 1, "rpc_source": "fixture", "query_parameters": {},
        "started_at": T0, "completed_at": T0,
    }
    with _session() as session:
        _pair(session)
        session.query(Pair).first().creation_block = 1
        session.commit()
        pair = session.query(Pair).first()
        verified = ResearchCoverageManifest(
            status="VERIFIED", relevant_pair_addresses=frozenset({PAIR}), **kwargs
        )
        incomplete = ResearchCoverageManifest(status="INCOMPLETE", **kwargs)
        assert verified.covers(pair, T0 + timedelta(hours=1))
        assert not incomplete.covers(pair, T0 + timedelta(hours=1))


def test_manifest_round_trip_rejects_wrong_chain_protocol_and_empty_coverage(tmp_path) -> None:
    manifest = ResearchCoverageManifest.from_chunks(
        chain_id=56,
        protocol="pancakeswap-v2",
        factory="0xfactory",
        start_block=1,
        end_block=100,
        start_timestamp=T0,
        end_timestamp=T0 + timedelta(hours=2),
        pair_created_query_complete=True,
        swap_query_complete=True,
        relevant_pair_count=1,
        rpc_source="fixture",
        query_parameters={"chunk_size": 5_000},
        started_at=T0,
        completed_at=T0,
        chunks=[CoverageChunk(1, 100, successful=True, persisted=True)],
        relevant_pair_addresses=[PAIR],
    )
    path = tmp_path / "coverage.json"
    manifest.write_json(path)

    assert ResearchCoverageManifest.read_json(path) == manifest
    try:
        ResearchCoverageManifest.read_json(path, chain_id=1)
    except ValueError as exc:
        assert "chain/protocol" in str(exc)
    else:
        raise AssertionError("wrong chain must be rejected")
    try:
        ResearchCoverageManifest.read_json(path, protocol="other-v2")
    except ValueError as exc:
        assert "chain/protocol" in str(exc)
    else:
        raise AssertionError("wrong protocol must be rejected")

    payload = manifest.as_dict()
    payload["relevant_pair_addresses"] = []
    path.write_text(json.dumps(payload), encoding="utf-8")
    try:
        ResearchCoverageManifest.read_json(path)
    except ValueError as exc:
        assert "explicitly list" in str(exc)
    else:
        raise AssertionError("empty pair set must not silently cover a nonzero count")


def test_manifest_requires_explicit_pair_and_horizon_within_range() -> None:
    manifest = ResearchCoverageManifest.from_chunks(
        chain_id=56,
        protocol="pancakeswap-v2",
        factory=None,
        start_block=1,
        end_block=100,
        start_timestamp=T0,
        end_timestamp=T0 + timedelta(hours=2),
        pair_created_query_complete=True,
        swap_query_complete=True,
        relevant_pair_count=1,
        rpc_source="fixture",
        query_parameters={},
        started_at=T0,
        completed_at=T0,
        chunks=[CoverageChunk(1, 100, successful=True, persisted=True)],
        relevant_pair_addresses=[OTHER_TOKEN],
    )
    with _session() as session:
        _pair(session)
        pair = session.query(Pair).first()
        pair.creation_block = 1
        session.commit()
        assert not manifest.covers(pair, T0 + timedelta(hours=1))
        assert not manifest.covers(pair, T0 + timedelta(hours=3))


def test_failed_chunk_is_incomplete_but_zero_log_chunk_can_verify() -> None:
    common = {
        "chain_id": 56, "protocol": "pancakeswap-v2", "factory": None,
        "start_block": 1, "end_block": 2, "start_timestamp": T0,
        "end_timestamp": T0 + timedelta(hours=1), "pair_created_query_complete": True,
        "swap_query_complete": True, "relevant_pair_count": 0, "rpc_source": "fixture",
        "query_parameters": {}, "started_at": T0, "completed_at": T0,
    }
    assert ResearchCoverageManifest.from_chunks(
        **common,
        chunks=[CoverageChunk(1, 2, successful=True, persisted=True, raw_log_count=0)],
    ).status == "VERIFIED"
    assert ResearchCoverageManifest.from_chunks(
        **common,
        chunks=[CoverageChunk(1, 2, successful=False, persisted=False, error="timeout")],
    ).status == "INCOMPLETE"
