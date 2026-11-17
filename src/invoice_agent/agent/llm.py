"""Chat interface for the agent loop: one model turn in, one response out.

`AnthropicChatClient` is the real client; `ScriptedChatClient` replays prepared turns so the whole
loop runs offline in tests. The model name comes from ANTHROPIC_MODEL, never hard-coded.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Protocol

# Dollars per million tokens (input, output), from Anthropic's published price list. Used only
# to enforce the per-invoice cost limit; unknown models are charged at the highest listed price,
# so the limit stays conservative rather than silently off.
PRICES_PER_MTOK = {
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
    "claude-haiku-5-5": (0.10, 0.50),
}
FALLBACK_PRICE = (10.00, 50.00)


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    price_in, price_out = PRICES_PER_MTOK.get(model, FALLBACK_PRICE)
    return (input_tokens * price_in + output_tokens * price_out) / 1_000_000


@dataclass
class Block:
    """The parts of a content block the loop needs, independent of the SDK's classes."""

    type: str  # "text", "tool_use", "thinking", ...
    text: str = ""
    id: str = ""
    name: str = ""
    input: dict = field(default_factory=dict)


@dataclass
class ChatResponse:
    blocks: list[Block]
    raw_content: Any  # sent back unchanged in the next request (keeps thinking blocks valid)
    stop_reason: str
    input_tokens: int
    output_tokens: int


class ChatClient(Protocol):
    model: str

    def create(self, system: str, messages: list[dict], tools: list[dict]) -> ChatResponse: ...


class AnthropicChatClient:
    def __init__(self, model: str | None = None, max_tokens: int = 16000):
        import anthropic

        self.model = model or os.environ["ANTHROPIC_MODEL"]
        self.max_tokens = max_tokens
        self._client = anthropic.Anthropic()

    def create(self, system: str, messages: list[dict], tools: list[dict]) -> ChatResponse:
        response = self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=messages,
            tools=tools,
        )
        blocks = []
        for b in response.content:
            if b.type == "text":
                blocks.append(Block(type="text", text=b.text))
            elif b.type == "tool_use":
                blocks.append(Block(type="tool_use", id=b.id, name=b.name, input=dict(b.input)))
            else:
                blocks.append(Block(type=b.type))
        return ChatResponse(
            blocks=blocks,
            raw_content=response.content,
            stop_reason=response.stop_reason,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )


class ScriptedChatClient:
    """Fake model for tests. Each turn is a list of tool calls (name, input) or a text reply.

    The script can be a list of turns, or a function that receives the messages so far and
    returns the next turn (useful when a turn depends on a tool result).
    """

    model = "scripted-fake"

    def __init__(self, turns):
        self.turns = turns
        self.calls: list[list[dict]] = []

    def create(self, system: str, messages: list[dict], tools: list[dict]) -> ChatResponse:
        self.calls.append([dict(m) for m in messages])
        n = len(self.calls) - 1
        turn = (
            self.turns(messages)
            if callable(self.turns)
            else (self.turns[n] if n < len(self.turns) else "done")
        )
        if isinstance(turn, str):
            blocks = [Block(type="text", text=turn)]
            stop = "end_turn"
        else:
            blocks = [
                Block(type="tool_use", id=f"call_{n}_{i}", name=name, input=args)
                for i, (name, args) in enumerate(turn)
            ]
            stop = "tool_use"
        raw = [
            {"type": "text", "text": b.text}
            if b.type == "text"
            else {"type": "tool_use", "id": b.id, "name": b.name, "input": b.input}
            for b in blocks
        ]
        return ChatResponse(blocks, raw, stop, input_tokens=1000, output_tokens=200)
