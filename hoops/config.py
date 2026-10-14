"""Настройките на баскетболния робот. Ключовете - САМО от .env (локално) или от тайните на GitHub (в облака); никога в кода и в логовете."""

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DB_PATH = Path(os.environ.get("HOOPS_DB", ROOT / "robot.db"))
SITE_DIR = Path(os.environ.get("HOOPS_SITE", ROOT / "docs"))
CACHE_DIR = Path(os.environ.get("HOOPS_CACHE", ROOT / "cache"))
LOG_DIR = Path(os.environ.get("HOOPS_LOGS", ROOT / "logs"))
DATA_DIR = ROOT / "data"
TEMPLATE = ROOT / "site_template.html"
SITE_URL = "https://kafe7o.github.io/basketball-site/"
REPO = "kafe7o/basketball-site"
TZ = "Europe/Sofia"

# odds API: ключът е ОБЩ с футбола (kafe7o/football-site) - баскетболът има свой месечен таван, а под резерва не се харчи нищо,
# за да не остане футболът без кредити
ODDS_BUDGET_MONTH = 3000
ODDS_RESERVE = 2500


def key(name):
    return os.environ.get(name)
