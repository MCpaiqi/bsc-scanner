import pytest
from pydantic import ValidationError

from smms.config.settings import Settings


def test_defaults_are_paper_first() -> None:
    settings = Settings(_env_file=None)
    assert settings.trading_mode == "PAPER"
    assert settings.is_paper_mode
    assert settings.enable_live_trading is False
    assert settings.bsc_chain_id == 56
    assert settings.bsc_rpc_url is None
    assert settings.bsc_rpc_endpoints == ()


def test_bsc_rpc_operational_limits_and_endpoint_failover_are_configurable() -> None:
    settings = Settings(
        _env_file=None,
        BSC_RPC_URLS="https://one.example, https://two.example",
        BSC_LOG_CHUNK_SIZE=500,
        BSC_RPC_MAX_RETRIES=1,
    )

    assert settings.bsc_rpc_endpoints == ("https://one.example", "https://two.example")
    assert settings.bsc_log_chunk_size == 500
    assert settings.bsc_rpc_max_retries == 1


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
