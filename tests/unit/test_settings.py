import pytest
from pydantic import ValidationError

from smms.config.settings import Settings


def test_defaults_are_paper_first(monkeypatch) -> None:
    # _env_file=None disables dotenv, not explicit process-level CI overrides.
    monkeypatch.delenv("BSC_RPC_URL", raising=False)
    monkeypatch.delenv("BSC_RPC_URLS", raising=False)
    settings = Settings(_env_file=None)
    assert settings.trading_mode == "PAPER"
    assert settings.is_paper_mode
    assert settings.enable_live_trading is False
    assert settings.bsc_chain_id == 56
    assert settings.bsc_rpc_url is None
    assert settings.bsc_rpc_endpoints == ()
    assert settings.bsc_live_tail_stream_name == "bsc_realtime_live_tail_v2"
    assert settings.bsc_log_address_batch_size == 4


def test_explicit_blank_provider_overrides_remain_not_configured() -> None:
    settings = Settings(_env_file=None, BSC_RPC_URL="", BSC_RPC_URLS="", BSC_WS_URL="", BSC_WS_URLS="")
    assert settings.bsc_rpc_endpoints == () and settings.bsc_ws_endpoints == ()
    assert settings.bsc_provider_name == "not_configured"


def test_bsc_rpc_operational_limits_and_endpoint_failover_are_configurable() -> None:
    settings = Settings(
        _env_file=None,
        BSC_RPC_URLS="https://one.example, https://two.example",
        BSC_LOG_CHUNK_SIZE=500,
        BSC_LOG_ADDRESS_BATCH_SIZE=2,
        BSC_RPC_MAX_RETRIES=1,
    )

    assert settings.bsc_rpc_endpoints == ("https://one.example", "https://two.example")
    assert settings.bsc_log_chunk_size == 500
    assert settings.bsc_log_address_batch_size == 2
    assert settings.bsc_rpc_max_retries == 1


@pytest.mark.parametrize("batch_size", [0, -1])
def test_log_address_batches_must_have_positive_size(batch_size: int) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, BSC_LOG_ADDRESS_BATCH_SIZE=batch_size)


def test_live_tail_epoch_name_is_configurable() -> None:
    settings = Settings(_env_file=None, BSC_LIVE_TAIL_STREAM_NAME="bsc_realtime_live_tail_demo")
    assert settings.bsc_live_tail_stream_name == "bsc_realtime_live_tail_demo"


def test_live_mode_requires_two_explicit_guards() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, TRADING_MODE="LIVE", ENABLE_LIVE_TRADING=True)

    settings = Settings(
        _env_file=None,
        TRADING_MODE="LIVE",
        ENABLE_LIVE_TRADING=True,
        LIVE_CONFIRMATION=True,
    )
    assert settings.trading_mode == "LIVE"


def test_secrets_are_not_plain_strings_in_settings_repr() -> None:
    settings = Settings(_env_file=None, TRADING_WALLET_PRIVATE_KEY="secret")
    assert "secret" not in repr(settings)
