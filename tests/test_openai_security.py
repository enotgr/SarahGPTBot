from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.openai_request import InputTooLarge, OpenAIRequest


def api_with_counts(counts):
    api = object.__new__(OpenAIRequest)
    count = AsyncMock(side_effect=[SimpleNamespace(input_tokens=n) for n in counts])
    api.client = SimpleNamespace(
        responses=SimpleNamespace(input_tokens=SimpleNamespace(count=count), create=AsyncMock()),
        images=SimpleNamespace(generate=AsyncMock()),
    )
    return api


@pytest.mark.asyncio
async def test_token_limit_drops_old_turns_without_cutting_current_question():
    api = api_with_counts([12000, 10000, 8000])
    context = [{"role": r, "content": str(i)} for i in range(4) for r in ("user", "assistant")]
    prepared = await api.prepare(context, "current question", "smart")
    assert prepared["context"] == context[4:]
    assert prepared["params"]["input"][-1]["content"] == "current question"
    assert prepared["input_tokens"] == 8000
    api.client.responses.create.assert_not_called()


@pytest.mark.asyncio
async def test_oversized_token_input_never_reaches_generation():
    api = api_with_counts([20000])
    with pytest.raises(InputTooLarge):
        await api.prepare([], "字" * 10000, "smart")
    api.client.responses.create.assert_not_called()


@pytest.mark.asyncio
async def test_photo_and_instructions_are_included_in_counted_input():
    api = api_with_counts([8193])
    with pytest.raises(InputTooLarge):
        await api.prepare([], "question", "fast", image="data:image/jpeg;base64,AAAA")
    payload = api.client.responses.input_tokens.count.call_args.kwargs
    assert payload["instructions"]
    assert payload["input"][0]["content"][1]["type"] == "input_image"
    api.client.responses.create.assert_not_called()


@pytest.mark.asyncio
async def test_image_prompt_byte_bound_prevents_paid_request():
    api = api_with_counts([])
    with pytest.raises(InputTooLarge):
        await api.generate_image("😀" * 2000, 1)
    api.client.images.generate.assert_not_called()


@pytest.mark.asyncio
async def test_stream_retains_provider_usage_and_pins_cache_lifetime():
    api = api_with_counts([100])
    usage = {"input_tokens": 100, "output_tokens": 10, "input_tokens_details": {"cached_tokens": 0}}
    events = [
        SimpleNamespace(type="response.output_text.delta", delta="answer"),
        SimpleNamespace(type="response.completed", response=SimpleNamespace(usage=usage, id="resp-test")),
    ]

    class Stream:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def __aiter__(self):
            for event in events:
                yield event

    api.client.responses.create.return_value = Stream()
    audit = {}
    assert "".join([d async for d in api.stream_answer([], "question", "fast", audit=audit)]) == "answer"
    assert audit["usage"] == usage and audit["dispatched"]
    assert api.client.responses.create.call_count == 1
    params = api.client.responses.create.call_args.kwargs
    assert params["max_output_tokens"] == 1536
    assert params["extra_body"]["prompt_cache_options"]["ttl"] == "30m"
