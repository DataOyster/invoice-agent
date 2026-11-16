"""Real-API smoke test. Run by hand only: pytest -m live

Checks the three things that usually break on day one: the API key, the model name in
ANTHROPIC_MODEL, and that a response comes back as text. Costs a fraction of a cent.
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.live


def test_api_key_and_model_name_work():
    anthropic = pytest.importorskip("anthropic")
    from dotenv import load_dotenv

    load_dotenv()
    model = os.environ.get("ANTHROPIC_MODEL")
    if not os.environ.get("ANTHROPIC_API_KEY") or not model:
        pytest.fail("set ANTHROPIC_API_KEY and ANTHROPIC_MODEL in .env (see .env.example)")

    client = anthropic.Anthropic()
    response = client.messages.create(
        model=model,
        # Current models think by default; leave room so thinking does not eat the whole reply.
        max_tokens=1024,
        output_config={"effort": "low"},
        messages=[{"role": "user", "content": "Reply with exactly the word: pong"}],
    )
    assert response.stop_reason == "end_turn", response.stop_reason
    text = "".join(block.text for block in response.content if block.type == "text")
    assert "pong" in text.lower()
    print(
        f"model={response.model} input={response.usage.input_tokens} "
        f"output={response.usage.output_tokens}"
    )
