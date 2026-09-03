import pytest
from pydantic import ValidationError

from smms.config.settings import Settings


def test_defaults_are_paper_first() -> None:
    settings = Settings(_env_file=None)
    assert settings.trading_mode == "PAPER"
    assert settings.is_paper_mode
    assert settings.enable_live_trading is False


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

