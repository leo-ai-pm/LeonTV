from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from models.llm_gemini import Gemini
from models.llm_gpt import GPT
from models.vlm_gemini import GeminiVLClient
from models.vlm_gpt import GPTVLClient


@pytest.mark.parametrize("client_type", [GPT, Gemini, GPTVLClient, GeminiVLClient])
def test_empty_model_responses_use_finite_retry_budget(client_type):
    client = client_type.__new__(client_type)
    client.max_attempts = 3
    client.max_tokens = 10
    client.api_key = "test-only"
    empty = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=""))] if client_type is GPT else [])
    create = Mock(side_effect=[empty] * 3 + [RuntimeError("bounded test guard")] * 3)
    client.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    with patch("models.llm_gpt.time.sleep"), patch("models.llm_gemini.time.sleep"):
        with pytest.raises(Exception, match="Max attempts|最大重试"):
            if hasattr(client, "query"):
                client.query("mock prompt", model="mock-model")
            else:
                client.chat("mock prompt", [], model="mock-model")
    assert create.call_count == 3
