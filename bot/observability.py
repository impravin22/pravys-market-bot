"""Optional observability shims — Sentry for errors, Logfire for traces.

Both integrations are opt-in via environment variables:

- ``SENTRY_DSN`` — if set and sentry-sdk is installed, uncaught exceptions
  in chatbot handlers get shipped to Sentry with custom tags. PII is
  suppressed at init time: ``send_default_pii=False`` blocks headers /
  cookies / IPs, and ``include_local_variables=False`` blocks frame
  locals (so raw ``user_id`` in ``_handle_one`` never leaks).
- ``LOGFIRE_TOKEN`` — if set and logfire is installed, httpx calls are
  instrumented so the Logfire dashboard shows Gemini latency, status
  codes, and errors.

If the libraries are not installed (optional dependencies), the module
logs a soft warning and falls through — the bot keeps working.
``capture_exception`` is a strict no-op unless ``init_sentry`` previously
succeeded; it does not attempt to send events against an uninitialised
client.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_sentry_active = False
_logfire_active = False


def init_sentry() -> bool:
    """Initialise Sentry if ``SENTRY_DSN`` is set. Returns True on success."""
    global _sentry_active
    dsn = os.getenv("SENTRY_DSN")
    if not dsn:
        return False
    try:
        import sentry_sdk  # noqa: PLC0415
    except ImportError:
        logger.info("SENTRY_DSN set but sentry_sdk not installed; skipping")
        return False
    try:
        sentry_sdk.init(
            dsn=dsn,
            # Stay within the 5k events/month free tier.
            traces_sample_rate=0.0,
            profiles_sample_rate=0.0,
            # Suppress every source of PII the SDK can attach by default.
            send_default_pii=False,
            include_local_variables=False,
        )
    except Exception as exc:  # noqa: BLE001 — observability must never crash the bot
        logger.error("Sentry init failed (DSN may be malformed): %s", exc)
        return False
    _sentry_active = True
    logger.info("Sentry initialised")
    return True


def init_logfire() -> bool:
    """Initialise Logfire if ``LOGFIRE_TOKEN`` is set. Returns True on success."""
    global _logfire_active
    token = os.getenv("LOGFIRE_TOKEN")
    if not token:
        return False
    try:
        import logfire  # noqa: PLC0415
    except ImportError:
        logger.info("LOGFIRE_TOKEN set but logfire not installed; skipping")
        return False
    try:
        logfire.configure(
            token=token,
            service_name=os.getenv("LOGFIRE_SERVICE_NAME", "pravys-market-bot"),
            send_to_logfire=True,
        )
    except (ValueError, TypeError) as exc:
        # Config-shape errors are human-fixable — log at ERROR so they are
        # visible in CI output.
        logger.error(
            "LOGFIRE_TOKEN rejected by logfire.configure (bad token or service_name): %s",
            exc,
        )
        return False
    except Exception as exc:  # noqa: BLE001
        logger.warning("Logfire configure failed (non-config): %s", exc)
        return False
    try:
        logfire.instrument_httpx()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Logfire httpx instrumentation failed: %s", exc)
        return False
    _logfire_active = True
    logger.info("Logfire initialised")
    return True


def capture_exception(exc: BaseException, **tags: str) -> None:
    """Ship an exception to Sentry with the given tags. No-op if Sentry is inactive."""
    if not _sentry_active:
        return
    try:
        import sentry_sdk  # noqa: PLC0415
    except ImportError:
        return
    scope_cm = getattr(sentry_sdk, "new_scope", None) or sentry_sdk.push_scope
    try:
        with scope_cm() as scope:
            for key, value in tags.items():
                scope.set_tag(key, value)
            sentry_sdk.capture_exception(exc)
    except Exception as capture_exc:  # noqa: BLE001
        logger.warning("sentry capture failed: %s", capture_exc)


def reset_for_tests() -> None:
    """Reset module-level flags so tests can run init_* more than once cleanly."""
    global _sentry_active, _logfire_active
    _sentry_active = False
    _logfire_active = False
