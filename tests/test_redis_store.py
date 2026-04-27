import json
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from bot.redis_store import (
    RATE_LIMIT_TTL_SECONDS,
    RedisConfig,
    RedisStore,
    _hash_user_id,
)


@pytest.fixture
def config():
    return RedisConfig(url="https://mock.upstash", token="secret", user_id_salt="pepper")


def _response(result, *, ok: bool = True, status: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = {"result": result} if ok else {"error": str(result)}
    resp.text = json.dumps(resp.json.return_value)
    return resp


def _http_mock(responses: list[MagicMock]) -> MagicMock:
    http = MagicMock()

    def post(url, headers=None, json=None, timeout=None):
        return responses.pop(0)

    http.post.side_effect = post
    return http


def test_hash_user_id_is_deterministic_and_salt_keyed():
    a = _hash_user_id(42, "pepper")
    b = _hash_user_id(42, "pepper")
    c = _hash_user_id(42, "other-salt")
    assert a == b
    assert a != c
    assert len(a) == 16


def test_from_env_requires_all_three_vars(monkeypatch):
    monkeypatch.delenv("UPSTASH_REDIS_REST_URL", raising=False)
    monkeypatch.delenv("UPSTASH_REDIS_REST_TOKEN", raising=False)
    monkeypatch.delenv("BOT_USER_ID_SALT", raising=False)
    assert RedisConfig.from_env() is None

    monkeypatch.setenv("UPSTASH_REDIS_REST_URL", "https://x")
    monkeypatch.setenv("UPSTASH_REDIS_REST_TOKEN", "t")
    assert RedisConfig.from_env() is None

    monkeypatch.setenv("BOT_USER_ID_SALT", "s")
    cfg = RedisConfig.from_env()
    assert cfg is not None and cfg.url == "https://x"


def test_get_offset_returns_zero_when_missing(config):
    http = _http_mock([_response(None)])
    store = RedisStore(config, http_client=http)
    assert store.get_offset() == 0


def test_get_offset_parses_int(config):
    http = _http_mock([_response("42")])
    store = RedisStore(config, http_client=http)
    assert store.get_offset() == 42


def test_get_offset_raises_on_non_integer_value(config):
    """A malformed offset must not silently reset — it would mass-replay updates."""
    http = _http_mock([_response("not a number")])
    store = RedisStore(config, http_client=http)
    with pytest.raises(RuntimeError, match="corrupt"):
        store.get_offset()


def test_set_offset_sends_int_value(config):
    http = _http_mock([_response("OK")])
    store = RedisStore(config, http_client=http)
    store.set_offset(100)
    args = http.post.call_args
    assert args.kwargs["json"] == ["SET", "telegram:offset", "100"]
    # Token is sent in the header, not the body.
    assert args.kwargs["headers"]["Authorization"] == "Bearer secret"


def test_rate_limit_uses_hashed_key(config):
    http = _http_mock([_response(None)])
    store = RedisStore(config, http_client=http)
    assert not store.is_rate_limited(42, seconds=30)
    args = http.post.call_args
    sent_key = args.kwargs["json"][1]
    assert sent_key.startswith("rate_limit:")
    # Raw user_id must not appear anywhere in the request body.
    assert "42" not in sent_key


def test_rate_limit_true_within_window(config):
    just_now = datetime.now(tz=UTC).isoformat()
    http = _http_mock([_response(just_now)])
    store = RedisStore(config, http_client=http)
    assert store.is_rate_limited(42, seconds=30)


def test_rate_limit_false_after_window(config):
    long_ago = (datetime.now(tz=UTC) - timedelta(seconds=120)).isoformat()
    http = _http_mock([_response(long_ago)])
    store = RedisStore(config, http_client=http)
    assert not store.is_rate_limited(42, seconds=30)


def test_rate_limit_malformed_treated_as_not_limited(config):
    http = _http_mock([_response("garbage-not-a-date")])
    store = RedisStore(config, http_client=http)
    assert not store.is_rate_limited(42, seconds=30)


def test_mark_user_sets_with_ttl(config):
    http = _http_mock([_response("OK")])
    store = RedisStore(config, http_client=http)
    store.mark_user(42)
    sent = http.post.call_args.kwargs["json"]
    assert sent[0] == "SET"
    assert sent[1].startswith("rate_limit:")
    # Timestamp is ISO-parseable.
    datetime.fromisoformat(sent[2])
    assert sent[3] == "EX"
    assert int(sent[4]) == RATE_LIMIT_TTL_SECONDS


def test_unmark_user_deletes(config):
    http = _http_mock([_response(1)])
    store = RedisStore(config, http_client=http)
    store.unmark_user(42)
    assert http.post.call_args.kwargs["json"][0] == "DEL"


def test_get_history_empty(config):
    http = _http_mock([_response(None)])
    store = RedisStore(config, http_client=http)
    assert store.get_history(-1) == []


def test_get_history_parses_json(config):
    payload = json.dumps(
        [{"role": "user", "text": "hi"}, {"role": "model", "text": "all good mate"}]
    )
    http = _http_mock([_response(payload)])
    store = RedisStore(config, http_client=http)
    history = store.get_history(-1)
    assert history == [
        {"role": "user", "text": "hi"},
        {"role": "model", "text": "all good mate"},
    ]


def test_get_history_returns_empty_on_corrupt(config):
    http = _http_mock([_response("not json")])
    store = RedisStore(config, http_client=http)
    assert store.get_history(-1) == []


def test_get_history_rejects_non_list(config):
    http = _http_mock([_response(json.dumps({"not": "a list"}))])
    store = RedisStore(config, http_client=http)
    assert store.get_history(-1) == []


def test_append_turn_persists_pair_with_ttl(config):
    # First call is the GET on existing history (returns nothing).
    # Second call is the SET of the updated list.
    http = _http_mock([_response(None), _response("OK")])
    store = RedisStore(config, http_client=http)
    store.append_turn(-100, "hey", "alright mate")
    second_call = http.post.call_args.kwargs["json"]
    assert second_call[0] == "SET"
    # Key is hashed to avoid leaking chat_id / user_id into Upstash.
    assert second_call[1].startswith("chat_history:")
    assert "-100" not in second_call[1]
    stored = json.loads(second_call[2])
    assert stored == [
        {"role": "user", "text": "hey"},
        {"role": "model", "text": "alright mate"},
    ]
    assert second_call[3] == "EX"


def test_append_turn_caps_history_at_limit(config):
    # Seed with 30 messages (15 turns) — limit is 10 turns (20 messages).
    existing = json.dumps(
        [{"role": "user" if i % 2 == 0 else "model", "text": f"msg {i}"} for i in range(30)]
    )
    http = _http_mock([_response(existing), _response("OK")])
    store = RedisStore(config, http_client=http)
    store.append_turn(-100, "new user", "new bot", limit=10)
    stored = json.loads(http.post.call_args.kwargs["json"][2])
    # 20 entries = 10 turns, with the latest pair included.
    assert len(stored) == 20
    assert stored[-1]["text"] == "new bot"
    assert stored[-2]["text"] == "new user"


def test_non_json_body_raises_runtime_error(config):
    # 200 status but non-JSON payload (e.g. Cloudflare challenge page).
    resp = MagicMock()
    resp.status_code = 200
    resp.json.side_effect = ValueError("not json")
    resp.text = "<html>edge rewrite</html>"
    http = _http_mock([resp])
    store = RedisStore(config, http_client=http)
    with pytest.raises(RuntimeError, match="non-JSON"):
        store.get_offset()


def test_upstash_error_field_raises(config):
    http = _http_mock([_response("WRONGTYPE", ok=False)])
    store = RedisStore(config, http_client=http)
    with pytest.raises(RuntimeError, match="Upstash"):
        store.get_offset()


def test_transport_timeout_raises_runtime_error(config):
    import httpx

    http = MagicMock()
    http.post.side_effect = httpx.ConnectTimeout("DNS failed")
    store = RedisStore(config, http_client=http)
    with pytest.raises(RuntimeError, match="timed out"):
        store.get_offset()


def test_transport_other_http_error_wrapped(config):
    import httpx

    http = MagicMock()
    http.post.side_effect = httpx.ConnectError("refused")
    store = RedisStore(config, http_client=http)
    with pytest.raises(RuntimeError, match="transport error"):
        store.get_offset()


def test_5xx_status_raises_runtime_error(config):
    resp = MagicMock()
    resp.status_code = 502
    resp.json.return_value = {}
    resp.text = "<html>Upstream error</html>"
    http = MagicMock()
    http.post.return_value = resp
    store = RedisStore(config, http_client=http)
    with pytest.raises(RuntimeError, match="5xx"):
        store.get_offset()


def test_corrupt_offset_raises_not_silent_reset(config):
    """A corrupt stored offset must NOT silently reset to 0 — that would
    trigger a mass replay of every update Telegram retains."""
    http = _http_mock([_response("not-an-int")])
    store = RedisStore(config, http_client=http)
    with pytest.raises(RuntimeError, match="corrupt"):
        store.get_offset()


def test_chat_history_key_is_hashed_to_avoid_raw_user_id_in_dms(config):
    """In a Telegram DM, chat_id == user_id — the key must not embed it raw."""
    http = _http_mock([_response(None)])
    store = RedisStore(config, http_client=http)
    store.get_history(8200970431)
    sent_key = http.post.call_args.kwargs["json"][1]
    assert sent_key.startswith("chat_history:")
    # Raw user_id must not appear in the key.
    assert "8200970431" not in sent_key


def test_public_call_method_runs_arbitrary_command(config):
    """Layered stores rely on RedisStore.call(...) instead of poking _call."""
    http = _http_mock([_response("OK")])
    store = RedisStore(config, http_client=http)
    result = store.call("SET", "k", "v")
    assert result == "OK"
    sent = http.post.call_args.kwargs["json"]
    assert sent == ["SET", "k", "v"]


def test_user_id_salt_property_exposes_config(config):
    """Public property — keeps PortfolioStore et al. out of `_config`."""
    store = RedisStore(config, http_client=MagicMock())
    assert store.user_id_salt == "pepper"
