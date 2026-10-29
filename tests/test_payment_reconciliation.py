from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

import services.payment_reconciliation as module


@pytest.mark.asyncio
async def test_reconciliation_pages_all_history_and_ignores_other_transfers(monkeypatch):
    refund = NS(
        receiver=NS(type="user", transaction_type="invoice_payment", user=NS(id=42), invoice_payload="order"),
        id="charge",
        amount=-99,
    )
    incoming = NS(receiver=None)
    withdrawal = NS(receiver=NS(type="fragment"))
    bot = NS(
        get_star_transactions=AsyncMock(
            side_effect=[NS(transactions=[incoming] * 99 + [withdrawal]), NS(transactions=[refund])]
        )
    )
    apply = AsyncMock()
    monkeypatch.setattr(module, "apply_refund", apply)
    await module.reconcile(bot)
    assert bot.get_star_transactions.call_args_list[1].kwargs == {"offset": 100, "limit": 100}
    apply.assert_awaited_once_with(42, "order", "charge", 99)


@pytest.mark.asyncio
async def test_failed_sync_disables_generation_until_next_success(monkeypatch):
    ledger = NS(payments_ready=AsyncMock())
    monkeypatch.setattr(module, "finance", ledger)
    check = AsyncMock(side_effect=[ValueError(), None])
    monkeypatch.setattr(module, "reconcile", check)
    assert not await module.check_payments(None)
    ledger.payments_ready.assert_awaited_with(False)
    assert await module.check_payments(None)
    ledger.payments_ready.assert_awaited_with(True)
