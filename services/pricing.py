"""Integer micro-USD accounting; no floating point rounding in budget decisions."""

from decimal import ROUND_CEILING, Decimal

from consts.common import MODELS

MICRO = 1000000
TEXT_RATES = {
    "gpt-6-luna": (Decimal(".1"), Decimal(".01"), Decimal(".5")),
    "gpt-6.1-terra": (Decimal("1"), Decimal(".05"), Decimal("5")),
}


def as_dict(value):
    if value is None:
        return None
    return value.model_dump(mode="json") if hasattr(value, "model_dump") else value


def text_cost(usage, model):
    usage = as_dict(usage)
    if not usage:
        return None
    rate, cached_rate, output_rate = TEXT_RATES[model]
    details = usage.get("input_tokens_details") or {}
    cached = details.get("cached_tokens", 0) or 0
    writes = details.get("cache_write_tokens", 0) or 0
    lifetimes = details.get("cache_write_tokens_details") or {}
    long_writes = lifetimes.get("ttl_12h_tokens", 0) or 0
    ordinary = max(0, usage["input_tokens"] - cached - writes)
    # $/million tokens equals micro-USD/token. Output includes reasoning already.
    amount = (
        ordinary * rate
        + cached * cached_rate
        + (writes - long_writes) * rate * Decimal("1.25")
        + long_writes * rate * Decimal("2.5")
        + usage["output_tokens"] * output_rate
    )
    return int(amount.to_integral_value(rounding=ROUND_CEILING))


def image_cost(usage):
    usage = as_dict(usage)
    if not usage:
        return None
    details = usage.get("input_tokens_details") or {}
    text = details.get("text_tokens")
    image = details.get("image_tokens")
    if text is None or image is None:
        return None  # Unknown breakdown: retain the conservative reservation.
    return int(text * 5 + image * 8 + usage["output_tokens"] * 30)


def reservation_cost(kind, mode):
    if kind == "image":
        return 80000  # Fixed low/1024 output + at most 3000 input characters.
    import config

    model = MODELS[mode]
    rate, _, output_rate = TEXT_RATES[model.id]
    return int(
        (config.MAX_INPUT_TOKENS * rate * Decimal("1.25") + model.max_output * output_rate).to_integral_value(
            rounding=ROUND_CEILING
        )
    )
