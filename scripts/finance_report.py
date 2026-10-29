"""Read-only aggregate cost report; never prints user IDs or conversations."""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.finance import finance  # noqa: E402

if __name__ == "__main__":
    print(json.dumps(asyncio.run(finance.report()), ensure_ascii=False, indent=2))
