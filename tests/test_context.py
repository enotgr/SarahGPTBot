from services.context import clean_context, trim_context
from utils.handler_utils import split_text


def test_context_drops_orphans_and_keeps_latest_complete_turns():
    raw = [{"role": "assistant", "content": "orphan"}]
    for n in range(30):
        raw += [{"role": "user", "content": f"q{n}"}, {"role": "assistant", "content": f"a{n}"}]
    raw.append({"role": "user", "content": "interrupted"})
    result = trim_context(raw, limit=4)
    assert [m["content"] for m in result] == ["q28", "a28", "q29", "a29"]


def test_context_numeric_firebase_map_and_invalid_records():
    raw = {"0": {"role": "user", "content": "hello"}, "1": {"role": "assistant", "content": "world"}}
    assert len(trim_context(raw)) == 2
    assert clean_context(None) == []
    assert clean_context([None, {"role": "system", "content": "bad"}]) == []


def test_context_character_budget_is_bounded():
    pair = [{"role": "user", "content": "q" * 10}, {"role": "assistant", "content": "a" * 10}]
    assert trim_context(pair * 100, budget=45, limit=100) == pair * 2


def test_long_messages_include_emoji_without_exceeding_telegram_limit():
    text = "😀<>&\n" * 5000
    chunks = split_text(text)
    assert "".join(chunks) == text
    assert all(len(c.encode("utf-16-le")) // 2 <= 3800 for c in chunks)
