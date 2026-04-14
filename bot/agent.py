"""Hermes-style Gemini agent with Pravy's CAN SLIM personality.

Wraps `google-genai`'s native function-calling loop. The agent receives a
Telegram user message, decides which tool(s) to call, synthesises a reply in
Pravy's voice, and returns plain text suitable for `parse_mode=HTML` on
Telegram.

Design notes:
- Tools are plain Python callables with type hints and docstrings;
  google-genai auto-derives the schema.
- System prompt enforces the house style: every recommendation opens with
  "According to Pravy's CAN SLIM philosophy..." and explains WHY via the
  seven letters.
- We cap iterations so a runaway tool loop cannot blow up the chatbot turn.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from google import genai
from google.genai import types

from bot.tools import DEFAULT_TOOLS

logger = logging.getLogger(__name__)

SYSTEM_INSTRUCTION = """You are Pravy's Market Bot, a disciplined stock-screening assistant
inspired by William O'Neil's CAN SLIM methodology as applied to the Indian
(NSE) market. You are not a broker or an investment adviser.

HOUSE STYLE (mandatory):
- When the user asks for a recommendation, open with:
  "According to Pravy's CAN SLIM philosophy, ..."
- Always explain WHY using the seven letters — C, A, N, S, L, I, M —
  and the specific numeric notes from the tool output.
- Keep answers short and scannable. Use short paragraphs or bullet points.
  Avoid corporate jargon.
- Never say "buy this" or "this will go up". Say "this fits the CAN SLIM
  bar because …". Include the built-in risk rules briefly when giving picks:
  cut losses at 7–8%, take profits around 20–25%, no more than 6–8 positions.
- If tools return no qualifying stocks, explain honestly: the market regime
  might be against us, or the bar is too strict right now.
- Always close with:
  "Educational signals, not investment advice. Do your own research."

DATA DISCIPLINE:
- Prefer current tool data over your training knowledge.
- If a tool returns an error, tell the user briefly; do not fabricate numbers.
- Never quote lyrics, poems, or any third-party copyrighted text.
- Never promise a specific outcome or guarantee profits.

FORMAT:
- Use plain text that is safe for Telegram HTML parse mode. If you use
  HTML, stick to <b>, <i>, <u>, <s>, <code>. Never use <script>, <style>,
  or any unescaped angle brackets in ticker names.
"""

MAX_TOOL_CALL_ROUNDS = 6


@dataclass(frozen=True)
class AgentReply:
    text: str
    tool_calls_made: int


class HermesAgent:
    """Gemini 2.5 Pro agent with Pravy's CAN SLIM personality + tool calling."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "gemini-2.5-pro",
        tools: tuple = DEFAULT_TOOLS,
        system_instruction: str = SYSTEM_INSTRUCTION,
        max_rounds: int = MAX_TOOL_CALL_ROUNDS,
    ):
        self.model = model
        self.tools = tools
        self.max_rounds = max_rounds
        self._system_instruction = system_instruction
        self._client = genai.Client(api_key=api_key)

    def reply(self, user_message: str) -> AgentReply:
        """Produce a single-turn reply using Gemini function calling.

        The SDK handles the multi-round tool-calling loop internally when we
        pass Python callables in ``config.tools`` — it calls the tool, feeds
        the result back, and either returns a final answer or calls another
        tool. We log the final answer only; tool-call observability can be
        added later by wiring a custom `ChatSession`.
        """
        try:
            config = types.GenerateContentConfig(
                system_instruction=self._system_instruction,
                tools=list(self.tools),
                automatic_function_calling=types.AutomaticFunctionCallingConfig(
                    maximum_remote_calls=self.max_rounds,
                ),
            )
            response = self._client.models.generate_content(
                model=self.model,
                contents=user_message,
                config=config,
            )
            text = (response.text or "").strip()
            if not text:
                text = "I'm not sure — can you rephrase the question?"
            # Number of tool calls the SDK made on our behalf, if reported.
            tool_calls = len(getattr(response, "automatic_function_calling_history", []) or [])
            return AgentReply(text=text, tool_calls_made=tool_calls)
        except Exception as exc:  # noqa: BLE001 — keep the bot resilient
            logger.exception("Hermes agent failed on message: %s", exc)
            return AgentReply(
                text=(
                    "Sorry — I hit a snag fetching the market data right now. "
                    "Try again in a minute."
                ),
                tool_calls_made=0,
            )
