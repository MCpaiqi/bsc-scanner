from datetime import timedelta

from sqlalchemy import event, select
from test_phase9_alpha_baseline import PAIR, T0, TOKEN, _pair, _session

from smms.api.alpha_metadata import load_alpha_metadata, safe_external_url
from smms.api.radar import _alpha_payload
from smms.db.models import AlphaSnapshot, Token
from smms.market.snapshots import materialize_dexscreener_snapshot


def test_metadata_batch_missing_candidate_latest_pair_and_url_safety():
    with _session() as session:
        _pair(session)
        session.flush()
        token = session.scalar(select(Token).where(Token.address == TOKEN))
        token.symbol, token.name = "DOGEAI", "Doge AI"
        rows = [AlphaSnapshot(chain="bsc", token_address=TOKEN, pair_address=PAIR,
                              pair_created_at=T0, snapshot_at=T0, observation_seconds=30,
                              alpha_score=20, features_json={}, outcome_json={})]
        for seconds, image in [(0, "https://example.com/old.png"), (1, "https://example.com/logo.png")]:
            snapshot = materialize_dexscreener_snapshot(session, {
                "token_address": TOKEN, "pair_address": PAIR, "image_url": image,
                "dex_url": "https://dexscreener.com/bsc/pair", "quote_symbol": "WBNB",
            }, as_of=T0 + timedelta(seconds=seconds))
            assert snapshot.metadata_json["image_url"] == image
        session.flush()
        calls = []
        def record(*args):
            calls.append(args[2])
        event.listen(session.bind, "before_cursor_execute", record)
        metadata = load_alpha_metadata(session, rows * 50)
        event.remove(session.bind, "before_cursor_execute", record)
        assert len(calls) == 4
        payload = _alpha_payload(rows[0], now=T0, metadata=metadata[(TOKEN, PAIR)])
        assert payload["identity"]["symbol"] == "DOGEAI"
        assert payload["identity"]["name"] == "Doge AI"
        assert payload["identity"]["image_url"] == "https://example.com/logo.png"
        missing = AlphaSnapshot(chain="bsc", token_address="missing", pair_address="missing-pair",
                                pair_created_at=T0, snapshot_at=T0, alpha_score=20)
        absent = load_alpha_metadata(session, [missing])
        assert absent[("missing", "missing-pair")]["symbol"] is None
        assert _alpha_payload(missing, now=T0, metadata=absent[("missing", "missing-pair")])["alpha_score"] == 20
        materialize_dexscreener_snapshot(session, {"token_address": TOKEN, "pair_address": PAIR,
                                                  "image_url": "javascript:alert(1)"}, as_of=T0 + timedelta(seconds=2))
        assert load_alpha_metadata(session, rows)[(TOKEN, PAIR)]["image_url"] is None


def test_external_url_policy():
    for value in [None, "", "javascript:alert(1)", "data:image/png;base64,a", "//example.com/a", "https://user:password@example.com/a", "https://", "https://example.com/\nfoo"]:
        assert safe_external_url(value) is None
    assert safe_external_url("https://example.com/a.png") == "https://example.com/a.png"


def test_erc20_dynamic_string_preserves_full_name_and_symbol():
    from smms.market.metadata import _decode_string
    for value in ["WBNB", "Pershing Square", "果蝇"]:
        raw = value.encode("utf-8")
        encoded = "0x" + f"{32:064x}{len(raw):064x}" + raw.hex().ljust(64, "0")
        assert _decode_string(encoded) == value
