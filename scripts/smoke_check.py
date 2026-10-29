"""Read-only integration checks; API probes never message Telegram users or debit balances."""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402
from consts.common import MODELS  # noqa: E402
from services.db_service import db_service  # noqa: E402
from services.openai_request import OpenAIRequest  # noqa: E402


async def check(with_image=False):
    from aiogram import Bot

    config.validate()
    api = OpenAIRequest()
    bot = Bot(token=config.BOT_TOKEN)
    try:
        users = await db_service.get_db("users") or {}
        contexts = await db_service.get_db("contexts") or {}
        print(f"Firebase OK: users={len(users)}, contexts={len(contexts)}", flush=True)
        me = await bot.get_me()
        webhook = await bot.get_webhook_info()
        print(f"Telegram OK: @{me.username}, webhook={bool(webhook.url)}", flush=True)
        print(
            "OpenAI key type:",
            "project" if config.OPENAI_API_KEY.startswith("sk-proj-") else "legacy/other",
            flush=True,
        )
        for key, model in MODELS.items():
            parts = [delta async for delta in api.stream_answer([], "Reply with exactly OK.", key, "brief")]
            if not "".join(parts).strip():
                raise RuntimeError(f"{model.id}: empty answer")
            print(f"{model.id}: streamed answer OK", flush=True)
        if with_image:
            image = await api.generate_image("A simple blue circle on a white background, no text.", 0)
            if not image.startswith(b"\x89PNG"):
                raise RuntimeError("Image response is not a PNG")
            print(f"{config.IMAGE_MODEL}: PNG OK, bytes={len(image)}", flush=True)
        print(f"Stars packages configured: {len(config.STARS_PACKAGES)}", flush=True)
    finally:
        await api.close()
        await bot.session.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--image", action="store_true", help="Generate one low-quality image as a paid API probe"
    )
    args = parser.parse_args()
    try:
        asyncio.run(check(args.image))
    except Exception as exc:
        print(
            f"CHECK FAILED: {type(exc).__name__}, status={getattr(exc, 'status_code', None)}, "
            f"code={getattr(exc, 'code', None)}",
            flush=True,
        )
        sys.exit(1)
