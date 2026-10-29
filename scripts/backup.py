"""Store a private backup on the bot server. No credentials or user data go to stdout."""

import asyncio
import json
import os
import sys
import tarfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402
from services.db_service import db_service  # noqa: E402


async def backup():
    os.umask(0o077)
    base = config.BASE_DIR
    directory = base / "backups" / time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    directory.mkdir(parents=True, exist_ok=False)
    (base / "backups").chmod(0o700)
    with tarfile.open(directory / "application.tar.gz", "w:gz") as archive:
        for path in base.iterdir():
            if path.name not in ("venv", ".venv", "backups", ".git", "__pycache__", ".cache", ".upgrade"):
                archive.add(path, arcname=path.name)
    names = ["users", "contexts", "payments", "payment_orders"]
    data = {name: await db_service.get_db(name) for name in names}
    target = directory / "firebase.json"
    target.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    target.chmod(0o600)
    print(directory)


if __name__ == "__main__":
    asyncio.run(backup())
