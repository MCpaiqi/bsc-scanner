from datetime import UTC, datetime, timedelta

from smms.evidence import EvidenceResolver, Freshness
from smms.external_evidence.models import EvidenceObservation
from smms.risk import analyze_token_risk


def obs(field, value, source, level, *, observed_at=None, fetched_at=None, stale=False):
    now = fetched_at or datetime.now(UTC)
    return EvidenceObservation(
        chain="solana", subject_type="TOKEN", subject_address="mint", field=field,
        value=value, source=source, source_type="CHAIN" if "CHAIN" in level else "secondary_api",
        observed_at=observed_at or now, fetched_at=now, confidence=0.9,
        evidence_level=level, stale=stale,
    )


def test_chain_wins_over_provider_and_conflict_remains_visible():
    result = EvidenceResolver().resolve([
        obs("ownership_renounced", True, "goplus", "TRUSTED_PROVIDER"),
        obs("ownership_renounced", False, "rpc", "ON_CHAIN_VERIFIED"),
    ])["ownership_renounced"]
    assert result.resolved_value is False
    assert result.resolution_source == "rpc"
    assert result.conflict is True
    assert result.conflicting_sources == ("goplus",)


def test_provider_fills_unknown_when_no_chain_value_exists():
    result = EvidenceResolver().resolve([obs("is_honeypot", False, "goplus", "TRUSTED_PROVIDER")])["is_honeypot"]
    assert result.resolved_value is False
    assert result.evidence_level == "TRUSTED_PROVIDER"


def test_stale_provider_is_not_fresh():
    now = datetime.now(UTC)
    result = EvidenceResolver().resolve([obs("price_usd", "1", "dexscreener", "TRUSTED_PROVIDER", fetched_at=now, stale=True)], now=now)["price_usd"]
    assert result.freshness is Freshness.STALE


def test_expired_and_unknown_are_preserved():
    old = datetime.now(UTC) - timedelta(days=2)
    expired = EvidenceResolver().resolve([obs("price_usd", "1", "dexscreener", "TRUSTED_PROVIDER", fetched_at=old)])['price_usd']
    unknown = EvidenceResolver().resolve([obs("is_honeypot", None, "goplus", "UNKNOWN")])['is_honeypot']
    assert expired.freshness is Freshness.EXPIRED
    assert unknown.resolved_value is None
    assert unknown.freshness is Freshness.UNKNOWN


def test_provider_wallet_labels_do_not_override_smart_money_score():
    snapshot = analyze_token_risk(
        {"address": "mint", "metadata_json": {}},
        resolved_evidence={"smart_wallet_label": {"resolved_value": "KOL", "source": "goplus"}},
    )
    assert snapshot.bot_probability is None
    assert snapshot.evidence_provenance["smart_wallet_label"]["resolved_value"] == "KOL"

