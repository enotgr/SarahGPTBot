"""Build an explicit source-only deployment archive, never including credentials."""

import sys
import tarfile
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
FILES = [
    "bot.py",
    "config.py",
    "misc.py",
    "start.sh",
    "requirements.txt",
    "requirements-dev.txt",
    "pyproject.toml",
    "README.md",
    ".env.example",
    ".gitignore",
    "consts/__init__.py",
    "consts/common.py",
    "consts/db_keys.py",
]
for folder in ("handlers", "services", "utils", "scripts", "tests", "deploy"):
    FILES.extend(
        str(p.relative_to(BASE))
        for p in (BASE / folder).rglob("*")
        if p.is_file() and "__pycache__" not in p.parts and p.suffix in (".py", ".service")
    )
destination = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/sarah-upgrade.tar.gz")
with tarfile.open(destination, "w:gz") as archive:
    for relative in sorted(set(FILES)):
        archive.add(BASE / relative, arcname=relative)
print(f"{destination}: {len(set(FILES))} source files, {destination.stat().st_size} bytes")
