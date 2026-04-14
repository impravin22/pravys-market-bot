from unittest.mock import MagicMock, patch

from bot.agent import MAX_TOOL_CALL_ROUNDS, SYSTEM_INSTRUCTION, AgentReply, HermesAgent


def test_system_instruction_enforces_house_style():
    """The personality prompt must include Pravy's phrasing + disclaimer."""
    assert "According to Pravy's CAN SLIM philosophy" in SYSTEM_INSTRUCTION
    assert "Educational signals, not investment advice" in SYSTEM_INSTRUCTION
    assert "7–8%" in SYSTEM_INSTRUCTION  # stop-loss rule
    assert "20–25%" in SYSTEM_INSTRUCTION  # profit-take rule


def test_agent_returns_text_on_success():
    fake_response = MagicMock()
    fake_response.text = "According to Pravy's CAN SLIM philosophy, RELIANCE qualifies…"
    fake_response.automatic_function_calling_history = [MagicMock(), MagicMock()]

    fake_client = MagicMock()
    fake_client.models.generate_content.return_value = fake_response

    with patch("bot.agent.genai.Client", return_value=fake_client):
        agent = HermesAgent(api_key="test")
        reply = agent.reply("what should I invest in?")

    assert isinstance(reply, AgentReply)
    assert "Pravy's CAN SLIM philosophy" in reply.text
    assert reply.tool_calls_made == 2


def test_agent_falls_back_on_exception():
    fake_client = MagicMock()
    fake_client.models.generate_content.side_effect = RuntimeError("gemini blew up")

    with patch("bot.agent.genai.Client", return_value=fake_client):
        agent = HermesAgent(api_key="test")
        reply = agent.reply("what's up?")

    assert "snag" in reply.text.lower()
    assert reply.tool_calls_made == 0


def test_agent_fills_in_placeholder_when_empty():
    fake_response = MagicMock()
    fake_response.text = ""
    fake_response.automatic_function_calling_history = []
    fake_client = MagicMock()
    fake_client.models.generate_content.return_value = fake_response
    with patch("bot.agent.genai.Client", return_value=fake_client):
        agent = HermesAgent(api_key="test")
        reply = agent.reply("hi")
    assert reply.text  # not empty
    assert reply.text != ""


def test_agent_passes_tools_and_system_instruction():
    fake_response = MagicMock()
    fake_response.text = "ok"
    fake_response.automatic_function_calling_history = []
    fake_client = MagicMock()
    fake_client.models.generate_content.return_value = fake_response
    with patch("bot.agent.genai.Client", return_value=fake_client):
        agent = HermesAgent(api_key="test")
        agent.reply("hello")
    call_args = fake_client.models.generate_content.call_args
    config = call_args.kwargs["config"]
    assert config.system_instruction == SYSTEM_INSTRUCTION
    assert config.tools  # tools list populated
    assert config.automatic_function_calling.maximum_remote_calls == MAX_TOOL_CALL_ROUNDS
