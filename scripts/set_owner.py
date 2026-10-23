"""Подставить имя аккаунта GitHub вместо OWNER в README, docker-compose и т. п.

    python scripts/set_owner.py <имя-аккаунта>
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FILES = ["README.md", "README.en.md", "docker-compose.yml", "docs/guide.md", "Dockerfile"]

if len(sys.argv) != 2:
    sys.exit(__doc__)
owner = sys.argv[1].strip().lower()
for name in FILES:
    p = ROOT / name
    s = p.read_text(encoding="utf-8")
    new = s.replace("OWNER", owner).replace("<owner>", owner)
    if new != s:
        p.write_text(new, encoding="utf-8")
        print("обновлён", name)
