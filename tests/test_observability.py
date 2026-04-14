import logging
import sys
import types

import pytest

from bot.observability import (
    capture_exception,
    init_logfire,
    init_sentry,
    reset_for_tests,
)


@pytest.fixture(autouse=True)
def _reset_observability_state():
    """Every test starts with Sentry / Logfire flagged off."""
    reset_for_tests()
    yield
    reset_for_tests()


def test_init_sentry_noop_without_dsn(monkeypatch):
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    assert init_sentry() is False


def test_init_logfire_noop_without_token(monkeypatch):
    monkeypatch.delenv("LOGFIRE_TOKEN", raising=False)
    assert init_logfire() is False


def test_init_sentry_skips_when_sdk_missing(monkeypatch, caplog):
    monkeypatch.setenv("SENTRY_DSN", "https://fake@sentry.io/0")
    monkeypatch.setitem(sys.modules, "sentry_sdk", None)
    with caplog.at_level(logging.INFO, logger="bot.observability"):
        assert init_sentry() is False
    assert any("sentry_sdk not installed" in rec.message for rec in caplog.records)


def test_init_sentry_calls_init_with_dsn(monkeypatch):
    monkeypatch.setenv("SENTRY_DSN", "https://fake@sentry.io/0")
    calls = {}

    fake_module = types.SimpleNamespace(
        init=lambda **kw: calls.update(kw),
        push_scope=lambda: None,
        new_scope=None,
        capture_exception=lambda _exc: None,
    )
    monkeypatch.setitem(sys.modules, "sentry_sdk", fake_module)

    assert init_sentry() is True
    assert calls["dsn"] == "https://fake@sentry.io/0"
    assert calls["send_default_pii"] is False
    # PII hardening: Sentry attaches frame locals by default. Turn it off so
    # a raw user_id inside _handle_one doesn't leak to the event payload.
    assert calls["include_local_variables"] is False
    # Free tier is 5k events/month — traces must be off to avoid eating it.
    assert calls["traces_sample_rate"] == 0.0


def test_init_sentry_returns_false_on_bad_dsn(monkeypatch):
    """Malformed DSN must not crash the bot — init returns False."""
    monkeypatch.setenv("SENTRY_DSN", "not-a-valid-dsn")

    def raising_init(**_kw):
        raise ValueError("invalid DSN")

    fake_module = types.SimpleNamespace(
        init=raising_init,
        push_scope=lambda: None,
        new_scope=None,
        capture_exception=lambda _e: None,
    )
    monkeypatch.setitem(sys.modules, "sentry_sdk", fake_module)
    assert init_sentry() is False


def test_capture_exception_is_true_noop_when_sentry_uninitialised(monkeypatch):
    """Even if sentry_sdk is importable, capture_exception must NOT send events
    without a prior successful init_sentry() — gates on the module flag."""
    called = {"capture": 0, "scope": 0}

    class FakeScope:
        def __enter__(self):
            called["scope"] += 1
            return self

        def __exit__(self, *a):
            return False

        def set_tag(self, k, v):
            pass

    fake_module = types.SimpleNamespace(
        init=lambda **_kw: None,
        push_scope=FakeScope,
        new_scope=FakeScope,
        capture_exception=lambda _e: called.__setitem__("capture", called["capture"] + 1),
    )
    monkeypatch.setitem(sys.modules, "sentry_sdk", fake_module)
    # We never called init_sentry, so capture_exception must stay silent.
    capture_exception(RuntimeError("boom"), update_id="1")
    assert called == {"capture": 0, "scope": 0}


def test_capture_exception_noop_without_sentry(monkeypatch):
    monkeypatch.setitem(sys.modules, "sentry_sdk", None)
    # Should not raise even though sentry is absent.
    capture_exception(RuntimeError("boom"), update_id="1")


def test_capture_exception_passes_tags(monkeypatch):
    monkeypatch.setenv("SENTRY_DSN", "https://fake@sentry.io/0")
    captured = {}

    class FakeScope:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def set_tag(self, key, value):
            captured.setdefault("tags", {})[key] = value

    fake_sentry = types.SimpleNamespace(
        init=lambda **_kw: None,
        push_scope=FakeScope,
        new_scope=FakeScope,
        capture_exception=lambda exc: captured.setdefault("exc", exc),
    )
    monkeypatch.setitem(sys.modules, "sentry_sdk", fake_sentry)

    assert init_sentry() is True
    exc = RuntimeError("boom")
    capture_exception(exc, update_id="42", chat_kind="group")

    assert captured["exc"] is exc
    assert captured["tags"] == {"update_id": "42", "chat_kind": "group"}


def test_capture_exception_swallows_sentry_failure(monkeypatch):
    monkeypatch.setenv("SENTRY_DSN", "https://fake@sentry.io/0")

    class BrokenScope:
        def __enter__(self):
            raise RuntimeError("sentry exploded")

        def __exit__(self, *a):
            return False

    monkeypatch.setitem(
        sys.modules,
        "sentry_sdk",
        types.SimpleNamespace(
            init=lambda **_kw: None,
            push_scope=BrokenScope,
            new_scope=BrokenScope,
            capture_exception=lambda _e: None,
        ),
    )
    assert init_sentry() is True
    # Must never re-raise — observability must not take down the bot.
    capture_exception(RuntimeError("boom"))


def test_init_logfire_skips_when_sdk_missing(monkeypatch):
    monkeypatch.setenv("LOGFIRE_TOKEN", "tok")
    monkeypatch.setitem(sys.modules, "logfire", None)
    assert init_logfire() is False


def test_init_logfire_configures_and_instruments_httpx(monkeypatch):
    monkeypatch.setenv("LOGFIRE_TOKEN", "tok")
    configured = {}

    fake_logfire = types.SimpleNamespace(
        configure=lambda **kw: configured.update(kw),
        instrument_httpx=lambda: configured.setdefault("httpx", True),
    )
    monkeypatch.setitem(sys.modules, "logfire", fake_logfire)

    assert init_logfire() is True
    assert configured["token"] == "tok"
    assert configured["httpx"] is True
