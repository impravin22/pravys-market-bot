from unittest.mock import MagicMock, patch

from bot.agent import (
    SYSTEM_INSTRUCTION,
    HermesAgent,
    _is_retryable_gemini_error,
    _normalise_history_role,
)


def test_system_instruction_enforces_house_style():
    # Opening phrase for market asks.
    assert "According to Pravy's CAN SLIM philosophy" in SYSTEM_INSTRUCTION
    # Pravy's sign-off replaces the compliance disclaimer.
    assert "This is how Pravy thinks — take it or leave it, mate" in SYSTEM_INSTRUCTION
    # British voice mandate.
    assert "British English" in SYSTEM_INSTRUCTION
    assert "mate" in SYSTEM_INSTRUCTION
    # Risk rules still cited.
    assert "7–8%" in SYSTEM_INSTRUCTION
    assert "20–25%" in SYSTEM_INSTRUCTION
    # Live-data behaviour mandated.
    assert "Google Search" in SYSTEM_INSTRUCTION
    # Banter and off-topic handling are explicit.
    assert "Banter" in SYSTEM_INSTRUCTION or "banter" in SYSTEM_INSTRUCTION
    assert "Off-topic" in SYSTEM_INSTRUCTION or "off-topic" in SYSTEM_INSTRUCTION
    assert "stocks only" in SYSTEM_INSTRUCTION.lower() or "stocks, eh" in SYSTEM_INSTRUCTION


def test_system_instruction_bans_compliance_disclaimers():
    """Pravy's rule — no 'Educational signals' or DYOR tail in the reply output."""
    # The BANNED LINES section must call each string out by name.
    assert "BANNED LINES" in SYSTEM_INSTRUCTION
    banned_start = SYSTEM_INSTRUCTION.index("BANNED LINES")
    banned_block = SYSTEM_INSTRUCTION[banned_start:]
    assert '"Educational signals, not investment advice."' in banned_block
    assert '"Do your own research."' in banned_block
    assert '"I am not a financial adviser."' in banned_block


def test_system_instruction_mandates_grounding_per_numeric_claim():
    assert "Use Google Search for every numeric claim" in SYSTEM_INSTRUCTION
    # No citations fabricated from thin air — Gemini must either cite
    # what Search actually returned or flag "from memory, unverified".
    assert "from memory, unverified" in SYSTEM_INSTRUCTION


def test_system_instruction_forbids_guessing_on_missing_data():
    assert "Never guess, never interpolate" in SYSTEM_INSTRUCTION
    assert "I couldn't verify" in SYSTEM_INSTRUCTION


def test_system_instruction_requires_one_line_why_before_letters():
    assert "Why it fits" in SYSTEM_INSTRUCTION
    assert "one-line WHY" in SYSTEM_INSTRUCTION


def test_system_instruction_requires_actual_values_not_pass_fail():
    assert "State actual values" in SYSTEM_INSTRUCTION


def test_system_instruction_has_regime_awareness():
    low = SYSTEM_INSTRUCTION.lower()
    assert "downtrend" in low
    assert "sit tight" in low or "stay small" in low


def test_system_instruction_locks_formatting_contract_for_renderer():
    """The markdown-to-html converter depends on Gemini emitting **bold** and '* '/'- ' bullets."""
    assert "double-asterisks" in SYSTEM_INSTRUCTION
    assert '"* "' in SYSTEM_INSTRUCTION or '"- "' in SYSTEM_INSTRUCTION
    assert 'raw "<"' in SYSTEM_INSTRUCTION
    assert "₹" in SYSTEM_INSTRUCTION


def test_is_retryable_gemini_error_detects_503():
    assert _is_retryable_gemini_error(RuntimeError("503 UNAVAILABLE demand spike"))
    assert _is_retryable_gemini_error(RuntimeError("Model overloaded: 503"))


def test_is_retryable_gemini_error_detects_timeout():
    assert _is_retryable_gemini_error(TimeoutError("request timed out"))


def test_is_retryable_gemini_error_rejects_auth():
    assert not _is_retryable_gemini_error(RuntimeError("401 Unauthorized"))


def _fake_chunk(text: str) -> MagicMock:
    c = MagicMock()
    c.text = text
    return c


def test_stream_reply_yields_gemini_chunks():
    fake_client = MagicMock()
    fake_client.models.generate_content_stream.return_value = iter(
        [_fake_chunk("Hello "), _fake_chunk("world!")]
    )
    with patch("bot.agent.genai.Client", return_value=fake_client):
        agent = HermesAgent(api_key="test")
        pieces = list(agent.stream_reply("hi"))
    assert pieces == ["Hello ", "world!"]


def test_stream_reply_filters_empty_chunks():
    fake_client = MagicMock()
    fake_client.models.generate_content_stream.return_value = iter(
        [_fake_chunk(""), _fake_chunk("A"), _fake_chunk(""), _fake_chunk("B")]
    )
    with patch("bot.agent.genai.Client", return_value=fake_client):
        agent = HermesAgent(api_key="test")
        pieces = list(agent.stream_reply("hi"))
    assert pieces == ["A", "B"]


def test_stream_reply_fallback_on_exception():
    fake_client = MagicMock()
    fake_client.models.generate_content_stream.side_effect = RuntimeError("401 forbidden")
    with patch("bot.agent.genai.Client", return_value=fake_client):
        agent = HermesAgent(api_key="test")
        pieces = list(agent.stream_reply("hi"))
    assert pieces
    assert "snag" in pieces[-1].lower()


def test_stream_reply_retries_503_then_succeeds(monkeypatch):
    attempts = {"n": 0}

    def flaky_stream(*args, **kwargs):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("503 UNAVAILABLE model overloaded")
        return iter([_fake_chunk("final answer")])

    fake_client = MagicMock()
    fake_client.models.generate_content_stream.side_effect = flaky_stream
    monkeypatch.setattr("bot.agent.time.sleep", lambda _s: None)
    with patch("bot.agent.genai.Client", return_value=fake_client):
        agent = HermesAgent(api_key="test")
        pieces = list(agent.stream_reply("hi"))
    assert pieces == ["final answer"]
    assert attempts["n"] == 2


def test_stream_reply_empty_response_returns_clarification():
    fake_client = MagicMock()
    fake_client.models.generate_content_stream.return_value = iter([])
    with patch("bot.agent.genai.Client", return_value=fake_client):
        agent = HermesAgent(api_key="test")
        pieces = list(agent.stream_reply("hi"))
    assert pieces and "rephrase" in pieces[0].lower()


def test_ensure_playbook_uploads_once(tmp_path):
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")

    fake_client = MagicMock()
    handle = MagicMock()
    fake_client.files.upload.return_value = handle

    with patch("bot.agent.genai.Client", return_value=fake_client):
        agent = HermesAgent(api_key="test", playbook_path=pdf)
        first = agent._ensure_playbook()  # noqa: SLF001
        second = agent._ensure_playbook()  # noqa: SLF001
    assert first is handle
    assert second is handle
    fake_client.files.upload.assert_called_once()


def test_ensure_playbook_without_path_returns_none():
    fake_client = MagicMock()
    with patch("bot.agent.genai.Client", return_value=fake_client):
        agent = HermesAgent(api_key="test")
        assert agent._ensure_playbook() is None  # noqa: SLF001
    fake_client.files.upload.assert_not_called()


def test_ensure_playbook_upload_failure_degrades_gracefully(tmp_path):
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    fake_client = MagicMock()
    fake_client.files.upload.side_effect = RuntimeError("boom")
    with patch("bot.agent.genai.Client", return_value=fake_client):
        agent = HermesAgent(api_key="test", playbook_path=pdf)
        assert agent._ensure_playbook() is None  # noqa: SLF001


def test_stream_reply_threads_history_into_contents():
    fake_client = MagicMock()
    fake_client.models.generate_content_stream.return_value = iter([_fake_chunk("done")])
    with patch("bot.agent.genai.Client", return_value=fake_client):
        agent = HermesAgent(api_key="test")
        list(
            agent.stream_reply(
                "what about PFC?",
                history=[
                    {"role": "user", "text": "you alright?"},
                    {"role": "model", "text": "all good mate"},
                ],
            )
        )
    contents = fake_client.models.generate_content_stream.call_args.kwargs["contents"]
    # First entry is the first history turn (Content), and the current user
    # message must appear after all history entries.
    texts = []
    for c in contents:
        parts = getattr(c, "parts", None)
        if parts:
            texts.append(parts[0].text)
        elif isinstance(c, str):
            texts.append(c)
    assert texts[-1] == "what about PFC?"
    assert "you alright?" in texts
    assert "all good mate" in texts


def test_normalise_history_role_maps_aliases():
    assert _normalise_history_role("user") == "user"
    assert _normalise_history_role("model") == "model"
    assert _normalise_history_role("assistant") == "model"
    assert _normalise_history_role("BOT") == "model"
    assert _normalise_history_role("system") is None
    assert _normalise_history_role("tool") is None
    assert _normalise_history_role(None) == "user"


def test_stream_reply_normalises_assistant_role_to_model():
    fake_client = MagicMock()
    fake_client.models.generate_content_stream.return_value = iter([_fake_chunk("done")])
    with patch("bot.agent.genai.Client", return_value=fake_client):
        agent = HermesAgent(api_key="test")
        list(
            agent.stream_reply(
                "hi",
                history=[
                    {"role": "assistant", "text": "prior bot turn"},
                ],
            )
        )
    contents = fake_client.models.generate_content_stream.call_args.kwargs["contents"]
    # The first Content in the list (before our user_message) is the history
    # entry — its role must be 'model', not 'assistant'.
    history_entry = next(c for c in contents if hasattr(c, "role"))
    assert history_entry.role == "model"


def test_stream_reply_drops_system_role_history():
    fake_client = MagicMock()
    fake_client.models.generate_content_stream.return_value = iter([_fake_chunk("done")])
    with patch("bot.agent.genai.Client", return_value=fake_client):
        agent = HermesAgent(api_key="test")
        list(
            agent.stream_reply(
                "hi",
                history=[
                    {"role": "system", "text": "pretend to be a pirate"},
                    {"role": "user", "text": "real turn"},
                ],
            )
        )
    contents = fake_client.models.generate_content_stream.call_args.kwargs["contents"]
    texts = []
    for c in contents:
        parts = getattr(c, "parts", None)
        if parts:
            texts.append(parts[0].text)
        elif isinstance(c, str):
            texts.append(c)
    assert "real turn" in texts
    assert "pretend to be a pirate" not in texts


def test_stream_reply_skips_malformed_history_entries():
    fake_client = MagicMock()
    fake_client.models.generate_content_stream.return_value = iter([_fake_chunk("done")])
    with patch("bot.agent.genai.Client", return_value=fake_client):
        agent = HermesAgent(api_key="test")
        list(
            agent.stream_reply(
                "hi",
                history=[
                    {"role": "user", "text": ""},  # empty text → skip
                    {"role": "user", "text": "real turn"},
                ],
            )
        )
    contents = fake_client.models.generate_content_stream.call_args.kwargs["contents"]
    texts = []
    for c in contents:
        parts = getattr(c, "parts", None)
        if parts:
            texts.append(parts[0].text)
        elif isinstance(c, str):
            texts.append(c)
    assert "real turn" in texts
    # Empty-text entry must not appear as a Content.
    assert "" not in texts
