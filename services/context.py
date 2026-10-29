"""Bound complete conversation turns."""

from config import MAX_CONTEXT_CHARS, MAX_CONTEXT_MESSAGES


def clean_context(raw):
    if isinstance(raw, dict):
        try:
            raw = [raw[k] for k in sorted(raw, key=int)]
        except (ValueError, TypeError):
            return []
    if not isinstance(raw, list):
        return []
    return [
        dict(role=m["role"], content=m["content"])
        for m in raw
        if isinstance(m, dict)
        and m.get("role") in ("user", "assistant")
        and isinstance(m.get("content"), str)
        and m["content"]
    ]


def trim_context(raw, budget=MAX_CONTEXT_CHARS, limit=MAX_CONTEXT_MESSAGES):
    turns, pending = [], None
    for message in clean_context(raw):
        if message["role"] == "user":
            pending = message
        elif pending is not None:
            turns.append([pending, message])
            pending = None
    selected, size = [], 0
    for turn in reversed(turns):
        length = sum(len(m["content"]) for m in turn)
        if size + length > budget or len(selected) + 2 > limit:
            break
        selected[0:0] = turn
        size += length
    return selected
