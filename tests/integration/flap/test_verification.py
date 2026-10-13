from decimal import Decimal

from smms.launchpads.flap.verification import (
    VERIFIED_FLAP_EVENTS,
    EvidenceLevel,
    VerifiedFlapContracts,
    build_flap_verification_report,
    keccak_topic,
    verified_curve_facts,
)


def test_verified_flap_contracts_keep_portal_and_unknown_roles_separate() -> None:
    contracts = VerifiedFlapContracts.bsc_mainnet()

    assert contracts.chain_id == 56
    assert contracts.portal.address == "0xe2cE6ab80874Fa9Fa2aAE65D277Dd6B8e65C9De0"
    assert contracts.portal.evidence_level is EvidenceLevel.VERIFIED
    assert contracts.factory is None
    assert contracts.router is None
    assert contracts.bonding_curve is None


def test_known_event_topics_and_field_positions_do_not_claim_fixture_verification() -> None:
    created = VERIFIED_FLAP_EVENTS["TokenCreated"]
    bought = VERIFIED_FLAP_EVENTS["TokenBought"]
    sold = VERIFIED_FLAP_EVENTS["TokenSold"]

    assert created.topic0 == "0x504e7f360b2e5fe33cbaaae4c593bc55305328341bf79009e43e0e3b7f699603"
    assert created.data_indexes == {"creator": 1, "token": 3}
    assert bought.topic0 == "0xa800a2038683844fac66747f771bfdfae862eb28b16bcfa387afa9fbacce8ff7"
    assert bought.data_indexes["token"] == 1
    assert bought.data_indexes["trader"] == 2
    assert bought.data_indexes["token_amount"] == 3
    assert bought.data_indexes["native_amount"] == 4
    assert bought.data_indexes["price"] == 6
    assert sold.topic0 == "0x03a4693e592f5e75dc7c136acb39b146d2b4966c0e509c34f362dee02b3b861a"
    assert sold.evidence_level is not EvidenceLevel.VERIFIED
    assert keccak_topic(created.signature) == created.topic0
    assert keccak_topic(bought.signature) == bought.topic0
    assert keccak_topic(sold.signature) == sold.topic0


def test_curve_facts_do_not_include_global_parameters() -> None:
    facts = verified_curve_facts()

    assert facts.token_decimals == 18
    assert facts.maximum_supply == Decimal(1000000000)
    assert facts.parameter_names == ("r", "h", "k")
    assert facts.global_parameters is None
    assert facts.state_method == "getTokenV8"
    assert facts.evidence_level is EvidenceLevel.UNKNOWN


def test_verification_report_is_machine_readable_without_unknown_addresses() -> None:
    report = build_flap_verification_report().as_dict()

    assert report["chain_id"] == 56
    assert report["contracts"]["portal"]
    assert report["contracts"]["factory"] is None
    assert report["verified_events"] == []
    assert report["curve"]["status"].startswith("PARTIAL")
