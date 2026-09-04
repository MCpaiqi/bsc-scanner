from smms.main import build_parser


def test_flap_discover_and_backfill_commands_parse_explicit_ranges() -> None:
    parser = build_parser()

    discover = parser.parse_args(
        ["discover", "flap", "--from-block", "100", "--to-block", "200"]
    )
    backfill = parser.parse_args(
        [
            "backfill",
            "flap",
            "--token",
            "0xToken",
            "--from-block",
            "100",
            "--to-block",
            "200",
        ]
    )

    assert discover.command == "discover"
    assert discover.launchpad == "flap"
    assert discover.from_block == 100
    assert discover.to_block == 200
    assert backfill.command == "backfill"
    assert backfill.token == "0xToken"


def test_flap_commands_do_not_default_to_an_unbounded_chain_scan() -> None:
    parser = build_parser()

    discover = parser.parse_args(["discover", "flap"])
    backfill = parser.parse_args(["backfill", "flap", "--token", "0xToken"])

    assert discover.from_block is None
    assert discover.to_block is None
    assert backfill.from_block is None
    assert backfill.to_block is None
