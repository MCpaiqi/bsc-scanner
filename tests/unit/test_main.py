from contextlib import asynccontextmanager

from smms import main
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


def test_intelligence_refresh_parses_versioned_point_in_time_options() -> None:
    args = build_parser().parse_args(
        [
            "intelligence",
            "refresh",
            "--chain",
            "bsc",
            "--as-of",
            "2026-09-11T10:00:00+00:00",
            "--score-version",
            "v3",
            "--profile-version",
            "v2",
        ]
    )

    assert args.intelligence_command == "refresh"
    assert args.score_version == "v3"
    assert args.as_of.isoformat() == "2026-09-11T10:00:00+00:00"


def test_alpha_backtest_loads_and_passes_coverage_manifest(monkeypatch, tmp_path) -> None:
    calls = {}
    sentinel = object()

    class FakeSession:
        async def run_sync(self, callback):
            return callback(object())

    @asynccontextmanager
    async def scope(_settings):
        yield FakeSession()

    class FakeManifest:
        @classmethod
        def read_json(cls, path, **kwargs):
            calls["manifest_path"] = path
            calls["manifest_kwargs"] = kwargs
            return sentinel

    class FakeBuilder:
        def __init__(self, _session, *, coverage_manifest):
            calls["coverage_manifest"] = coverage_manifest

        def build(self, _start, _end, *, windows):
            calls["windows"] = windows
            return []

    class FakeBacktest:
        def __init__(self, samples):
            assert samples == []

        def report(self, **_kwargs):
            return {"status": "LABEL_PIPELINE_BLOCKED"}

    monkeypatch.setattr(main, "get_settings", lambda: object())
    monkeypatch.setattr(main, "session_scope", scope)
    monkeypatch.setattr("smms.alpha.ResearchCoverageManifest", FakeManifest)
    monkeypatch.setattr("smms.alpha.AlphaDatasetBuilder", FakeBuilder)
    monkeypatch.setattr("smms.alpha.AlphaBacktest", FakeBacktest)

    manifest_path = tmp_path / "coverage.json"
    main.cli([
        "alpha", "backtest", "--from", "2026-01-01T00:00:00+00:00",
        "--to", "2026-01-01T01:00:00+00:00", "--coverage-manifest", str(manifest_path),
    ])

    assert calls["manifest_path"] == manifest_path
    assert calls["coverage_manifest"] is sentinel
    assert calls["windows"] == ("1m",)
