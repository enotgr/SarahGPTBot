import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import config
from services.finance import BudgetExceeded, Finance, RateLimited
from services.pricing import image_cost, reservation_cost, text_cost


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "REQUEST_COOLDOWN_SECONDS", 0)
    monkeypatch.setattr(config, "MONTHLY_API_BUDGET_MICRO", 10000)
    monkeypatch.setattr(config, "DAILY_API_BUDGET_MICRO", 10000)
    result = Finance(tmp_path / "ledger.sqlite3")
    result._payments_ready(True)
    return result


@pytest.mark.asyncio
async def test_concurrent_requests_cannot_oversubscribe_budget(ledger):
    results = await asyncio.gather(
        *[ledger.reserve(i, "r", "text", "gpt-6-luna", "paid_or_legacy", 1, 2000) for i in range(20)],
        return_exceptions=True,
    )
    assert sum(isinstance(r, str) for r in results) == 5
    assert sum(isinstance(r, BudgetExceeded) for r in results) == 15
    assert (await ledger.report())["cost_micro"] == 10000


@pytest.mark.asyncio
async def test_settlement_is_idempotent_and_releases_only_unused_reserve(ledger):
    key = await ledger.reserve(1, "r", "text", "gpt-6-luna", "paid_or_legacy", 1, 10000)
    await ledger.start(key)
    await ledger.settle(key, 500, "completed", charge=True)
    await ledger.settle(key, 0, "cancelled", charge=False)
    assert (await ledger.report())["cost_micro"] == 500
    assert await ledger.wallet_charge(1, "r") is True
    await ledger.reserve(2, "r2", "text", "gpt-6-luna", "paid_or_legacy", 1, 9500)


@pytest.mark.asyncio
async def test_restart_retains_unknown_started_cost_and_refunds_unstarted(ledger):
    a = await ledger.reserve(1, "started", "text", "gpt-6-luna", "paid_or_legacy", 1, 2000)
    await ledger.start(a)
    await ledger.reserve(2, "queued", "text", "gpt-6-luna", "paid_or_legacy", 1, 2000)
    restarted = Finance(ledger.path)
    await restarted.recover()
    assert (await restarted.report())["cost_micro"] == 2000
    assert await restarted.wallet_charge(1, "started") is True
    assert await restarted.wallet_charge(2, "queued") is False


@pytest.mark.asyncio
async def test_unexpected_price_overrun_stops_new_requests(ledger):
    key = await ledger.reserve(1, "r", "image", "gpt-image-2.5-flare", "paid_or_legacy", 10, 1000)
    await ledger.start(key)
    await ledger.settle(key, 1100, "completed", charge=True)
    with pytest.raises(BudgetExceeded):
        await ledger.reserve(2, "r2", "text", "gpt-6-luna", "paid_or_legacy", 1, 100)


@pytest.mark.asyncio
async def test_promo_limits_do_not_block_paid_requests(ledger, monkeypatch):
    monkeypatch.setattr(config, "PROMO_MONTHLY_BUDGET_MICRO", 500)
    with pytest.raises(BudgetExceeded):
        await ledger.reserve(1, "free", "text", "gpt-6-luna", "promo", 1, 1000)
    await ledger.reserve(1, "paid", "text", "gpt-6-luna", "paid_or_legacy", 1, 1000)


@pytest.mark.asyncio
async def test_welcome_issuance_is_atomic_limited_and_retryable(ledger, monkeypatch):
    monkeypatch.setattr(config, "PROMO_DAILY_CREDITS", 50)
    answers = await asyncio.gather(*[ledger.claim_grant("welcome", i, 25) for i in range(20)])
    assert sum(answers) == 2
    accepted = answers.index(True)
    assert await ledger.claim_grant("welcome", accepted, 25)
    assert not await ledger.claim_grant("welcome", 100, 25)


@pytest.mark.asyncio
async def test_cancelled_attempts_still_count_toward_rate_limit(ledger, monkeypatch):
    monkeypatch.setattr(config, "PROMO_DAILY_REQUESTS", 2)
    for index in range(2):
        key = await ledger.reserve(1, str(index), "text", "gpt-6-luna", "promo", 1, 100)
        await ledger.settle(key, 0, "cancelled")
    with pytest.raises(RateLimited):
        await ledger.reserve(1, "3", "text", "gpt-6-luna", "promo", 1, 100)


@pytest.mark.asyncio
async def test_new_day_releases_daily_limit_without_resetting_monthly_usage(ledger, monkeypatch):
    monkeypatch.setattr(config, "DAILY_API_BUDGET_MICRO", 1000)
    times = [datetime(2026, 10, 29, 23, 59, tzinfo=ZoneInfo("Asia/Tbilisi")).timestamp()]
    ledger.clock = lambda: times[0]
    ledger._payments_ready(True)
    a = await ledger.reserve(1, "day1", "text", "gpt-6-luna", "paid_or_legacy", 1, 1000)
    await ledger.start(a)
    await ledger.settle(a, 1000, "completed", charge=True)
    with pytest.raises(BudgetExceeded):
        await ledger.reserve(2, "blocked", "text", "gpt-6-luna", "paid_or_legacy", 1, 1)
    times[0] += 120
    await ledger.reserve(2, "day2", "text", "gpt-6-luna", "paid_or_legacy", 1, 1000)
    assert (await ledger.report())["cost_micro"] == 2000


def test_pricing_includes_cache_writes_and_does_not_double_bill_reasoning():
    usage = {
        "input_tokens": 12000,
        "input_tokens_details": {
            "cached_tokens": 4000,
            "cache_write_tokens": 7000,
            "cache_write_tokens_details": {"ttl_30m_tokens": 3000, "ttl_12h_tokens": 4000},
        },
        "output_tokens": 500,
        "output_tokens_details": {"reasoning_tokens": 400},
    }
    assert text_cost(usage, "gpt-6.1-terra") == 17450
    assert (
        image_cost({"input_tokens_details": {"text_tokens": 100, "image_tokens": 0}, "output_tokens": 196})
        == 6380
    )
    assert reservation_cost("text", "smart") == 17920


@pytest.mark.asyncio
async def test_unverified_or_stale_payments_block_api(tmp_path):
    now = [2000000000]
    ledger = Finance(tmp_path / "unverified.sqlite3", clock=lambda: now[0])

    async def request():
        return await ledger.reserve(1, "r", "text", "gpt-6-luna", "paid_or_legacy", 1, 100)

    with pytest.raises(BudgetExceeded):
        await request()
    await ledger.payments_ready(True)
    now[0] += 3601
    with pytest.raises(BudgetExceeded):
        await request()
    await ledger.payments_ready(False)
    with pytest.raises(BudgetExceeded):
        await request()
    await ledger.payments_ready(True)
    assert await request()
