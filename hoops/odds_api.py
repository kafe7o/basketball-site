"""the-odds-api.com: предстоящите мачове с цените (h2h, spreads, totals; регион eu - с Pinnacle). Ключът е ОБЩ с футбола:
месечен таван за баскетбола (config.ODDS_BUDGET_MONTH) и резерв (config.ODDS_RESERVE) - под него не се харчи нищо. Ключът никога не се печата."""

import logging
from datetime import datetime, timezone

import requests

from . import config

log = logging.getLogger(__name__)
BASE = "https://api.the-odds-api.com/v4"


def _month(now=None):
    return (now or datetime.now(timezone.utc)).strftime("%Y-%m")


def spent(conn, now=None):
    r = conn.execute("SELECT used, remaining FROM credits WHERE month = ?", (_month(now),)).fetchone()
    return (r["used"], r["remaining"]) if r else (0, None)


def can_spend(conn, cost, now=None):
    used, remaining = spent(conn, now)
    if used + cost > config.ODDS_BUDGET_MONTH:
        log.warning("odds API: месечният таван за баскетбола (%d) е стигнат - %d похарчени", config.ODDS_BUDGET_MONTH, used)
        return False
    if remaining is not None and remaining - cost < config.ODDS_RESERVE:
        log.warning("odds API: остатъкът %s е под резерва %d за футбола - не се тегли", remaining, config.ODDS_RESERVE)
        return False
    return True


def get(conn, path, params, cost, now=None):
    """GET с ключа; връща JSON или None. Харченето се записва по месеци (по заглавките на отговора)."""
    key = config.key("ODDS_API_KEY")
    if not key:
        log.error("odds API: няма ODDS_API_KEY")
        return None
    if cost and not can_spend(conn, cost, now):
        return None
    try:
        r = requests.get(f"{BASE}{path}", params={**params, "apiKey": key}, timeout=40)
    except requests.RequestException as e:
        log.error("odds API %s: %s", path, str(e).replace(key, "***"))
        return None
    rem, last = r.headers.get("x-requests-remaining"), r.headers.get("x-requests-last")
    if last is not None:
        m = _month(now)
        conn.execute("INSERT INTO credits (month, used, remaining) VALUES (?, ?, ?) ON CONFLICT(month) DO UPDATE SET used = used + excluded.used, "
                     "remaining = excluded.remaining", (m, int(float(last)), int(float(rem)) if rem else None))
        conn.commit()
    if r.status_code != 200:
        log.error("odds API %s: %s %s", path, r.status_code, r.text[:200].replace(key, "***"))
        return None
    return r.json()


def odds(conn, sport, now=None):
    """Всички предстоящи мачове на първенството с цените - 3 кредита (3 пазара x 1 регион)."""
    return get(conn, f"/sports/{sport}/odds", {"regions": "eu", "markets": "h2h,spreads,totals", "oddsFormat": "decimal"}, 3, now)
