import pytest

from core.config import load_config


def _valid_env() -> dict[str, str]:
    return {
        "TELEGRAM_BOT_TOKEN": "test-token",
        "TELEGRAM_CHAT_ID": "-123456",
        "GOOGLE_API_KEY": "key",
    }


def test_load_config_requires_core_vars():
    with pytest.raises(RuntimeError, match="Missing required env vars"):
        load_config({})


def test_load_config_sets_defaults():
    cfg = load_config(_valid_env())
    assert cfg.telegram.bot_token == "test-token"
    assert cfg.telegram.chat_id == "-123456"
    assert cfg.google.model == "gemini-2.5-pro"
    assert cfg.locale_tz == "Asia/Taipei"
    assert cfg.market_tz == "Asia/Kolkata"


def test_load_config_accepts_optional_news_keys():
    env = {**_valid_env(), "GOOGLE_SEARCH_API_KEY": "sk", "GOOGLE_CSE_ID": "cid"}
    cfg = load_config(env)
    assert cfg.google.search_api_key == "sk"
    assert cfg.google.cse_id == "cid"


def test_load_config_rejects_invalid_tz():
    env = {**_valid_env(), "DIGEST_LOCALE_TZ": "Not/A/Zone"}
    with pytest.raises(RuntimeError, match="Invalid IANA timezone"):
        load_config(env)
