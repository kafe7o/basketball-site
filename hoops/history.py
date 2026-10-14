"""Историята: резултати и затварящи линии.

НБА - ESPN (безплатно, без ключ): табло по дати (site.api.espn.com .../scoreboard?dates=YYYYMMDD) и линиите на всеки мач
(sports.core.api.espn.com .../events/{id}/competitions/{id}/odds - ESPN BET: хендикап, общо точки, коефициенти за победа, с отварящите).
Само редовен сезон, плейин и плейофи (без предсезон и Мача на звездите).
Евролига - официалното API (api-live.euroleague.net/v2/competitions/E/seasons/E{година}/games): резултати и час (UTC); линии назад няма.
"""

import json
import logging
import time
from datetime import date, datetime, timedelta, timezone

import requests

from . import config, market

log = logging.getLogger(__name__)
ESPN_BOARD = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
ESPN_ODDS = "https://sports.core.api.espn.com/v2/sports/basketball/leagues/nba/events/{id}/competitions/{id}/odds"
EL_GAMES = "https://api-live.euroleague.net/v2/competitions/E/seasons/E{year}/games"
NBA_TYPES = {2, 3, 5}                 # редовен сезон, плейофи, плейин


def _get(url, params=None, tries=3):
    for k in range(tries):
        try:
            r = requests.get(url, params=params, timeout=30)
            if r.status_code == 200:
                return r.json()
            log.warning("%s: %s", url, r.status_code)
            if r.status_code == 404:
                return None
        except (requests.RequestException, ValueError) as e:
            log.warning("%s: %s", url, e)
        time.sleep(1 + 2 * k)
    log.error("не се взе: %s", url)
    return None


def upsert(conn, g):
    conn.execute("""INSERT INTO games (id, league, tip, home, away, hp, ap, neutral, src, ml_home, ml_away, spread_home, total, book)
                    VALUES (:id, :league, :tip, :home, :away, :hp, :ap, :neutral, :src, :ml_home, :ml_away, :spread_home, :total, :book)
                    ON CONFLICT(id) DO UPDATE SET tip = excluded.tip, hp = COALESCE(excluded.hp, hp), ap = COALESCE(excluded.ap, ap),
                      ml_home = COALESCE(excluded.ml_home, ml_home), ml_away = COALESCE(excluded.ml_away, ml_away),
                      spread_home = COALESCE(excluded.spread_home, spread_home), total = COALESCE(excluded.total, total), book = COALESCE(excluded.book, book)""",
                 {"ml_home": None, "ml_away": None, "spread_home": None, "total": None, "book": None, "neutral": 0, **g})


# ---------- НБА (ESPN) ----------

def espn_odds(event_id, home_abbr, away_abbr):
    """Затварящите линии на мача (ESPN BET или първият доставчик, без „Live“) -> {ml_home, ml_away, spread_home, total, book}; кеш на диска."""
    cache = config.CACHE_DIR / "espn_odds" / f"{event_id}.json"
    if cache.exists():
        d = json.loads(cache.read_text(encoding="utf-8"))
    else:
        d = _get(ESPN_ODDS.format(id=event_id))
        if d is None:
            return {}
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(d), encoding="utf-8")
    items = [i for i in d.get("items", []) if "live" not in ((i.get("provider") or {}).get("name") or "").lower()]
    if not items:
        return {}
    it = next((i for i in items if "espn bet" in ((i.get("provider") or {}).get("name") or "").lower()), items[0])
    det = (it.get("details") or "").strip()
    sp = it.get("spread")
    line = None
    if det.upper().startswith("EVEN"):
        line = 0.0
    elif sp is not None and det:
        ab = det.split()[0].upper()
        line = -abs(float(sp)) if ab == home_abbr.upper() else abs(float(sp)) if ab == away_abbr.upper() else None
    return {"ml_home": market.american_to_decimal((it.get("homeTeamOdds") or {}).get("moneyLine")),
            "ml_away": market.american_to_decimal((it.get("awayTeamOdds") or {}).get("moneyLine")),
            "spread_home": line, "total": float(it["overUnder"]) if it.get("overUnder") is not None else None,
            "book": (it.get("provider") or {}).get("name")}


def nba_day(conn, day, with_odds=True):
    """Един ден от таблото на ESPN (американската дата) -> записва изиграните мачове. Връща броя."""
    d = _get(ESPN_BOARD, {"dates": day.strftime("%Y%m%d")})
    n = 0
    for ev in (d or {}).get("events", []):
        if (ev.get("season") or {}).get("type") not in NBA_TYPES or "all-star" in (ev.get("name") or "").lower():
            continue
        c = ev["competitions"][0]
        if not c["status"]["type"].get("completed"):
            continue
        teams = {t["homeAway"]: t for t in c["competitors"]}
        if "home" not in teams or "away" not in teams:
            continue
        h, a = teams["home"], teams["away"]
        g = {"id": f"NBA:espn:{ev['id']}", "league": "NBA", "tip": ev["date"].replace("Z", "+00:00"), "home": h["team"]["displayName"],
             "away": a["team"]["displayName"], "hp": int(h["score"]), "ap": int(a["score"]), "neutral": int(bool(c.get("neutralSite"))), "src": "espn"}
        have = conn.execute("SELECT spread_home FROM games WHERE id = ?", (g["id"],)).fetchone()
        if with_odds and not (have and have["spread_home"] is not None):          # линиите се теглят веднъж на мач
            g.update(espn_odds(ev["id"], h["team"]["abbreviation"], a["team"]["abbreviation"]))
        upsert(conn, g)
        n += 1
    conn.commit()
    return n


# ---------- Евролига ----------

def el_season(conn, year):
    d = _get(EL_GAMES.format(year=year))
    n = 0
    for g in (d or {}).get("data", []):
        if not g.get("played"):
            continue
        upsert(conn, {"id": f"EL:{g['identifier']}", "league": "EL", "tip": g["utcDate"].replace("Z", "+00:00"),
                      "home": g["local"]["club"]["name"], "away": g["road"]["club"]["name"], "hp": int(g["local"]["score"]),
                      "ap": int(g["road"]["score"]), "neutral": int(bool(g.get("isNeutralVenue"))), "src": "euroleague"})
        n += 1
    conn.commit()
    return n


def el_teams(year):
    """Имената на отборите в сезона (и неизиграните мачове) - за превода от odds API."""
    d = _get(EL_GAMES.format(year=year))
    return {g[s]["club"]["name"] for g in (d or {}).get("data", []) for s in ("local", "road")}


# ---------- обновяване ----------

def season_year(now):
    return now.year if now.month >= 7 else now.year - 1


def update(conn, now=None):
    """Часовото обновяване: НБА - от последния записан ден минус 3 до днес; Евролига - текущият сезон."""
    now = now or datetime.now(timezone.utc)
    last = conn.execute("SELECT MAX(tip) FROM games WHERE league = 'NBA'").fetchone()[0]
    start = (datetime.fromisoformat(last).date() - timedelta(days=3)) if last else (now.date() - timedelta(days=10))
    n = 0
    d = start
    while d <= now.date():
        n += nba_day(conn, d)
        d += timedelta(days=1)
    m = el_season(conn, season_year(now))
    log.info("История: НБА %d мача (от %s), Евролига %d", n, start, m)
    return n + m


def backfill(conn, first_season=2022, last_season=None, workers=16):
    """Еднократно (на лаптопа): НБА и Евролига от сезон first_season. Заявките към ESPN - паралелно (по ~1.5 с всяка); линиите се кешират на диска."""
    from concurrent.futures import ThreadPoolExecutor
    now = datetime.now(timezone.utc)
    last_season = last_season or season_year(now)
    for y in range(first_season, last_season + 1):
        days, d = [], date(y, 10, 1)
        while d <= min(date(y + 1, 6, 30), now.date()):
            days.append(d)
            d += timedelta(days=1)
        with ThreadPoolExecutor(workers) as ex:
            boards = list(ex.map(lambda dd: _get(ESPN_BOARD, {"dates": dd.strftime("%Y%m%d")}), days))
        evs = []
        for b in boards:
            for ev in (b or {}).get("events", []):
                if (ev.get("season") or {}).get("type") not in NBA_TYPES or "all-star" in (ev.get("name") or "").lower():
                    continue
                c = ev["competitions"][0]
                teams = {t["homeAway"]: t for t in c["competitors"]}
                if c["status"]["type"].get("completed") and "home" in teams and "away" in teams:
                    evs.append((ev, c, teams["home"], teams["away"]))
        with ThreadPoolExecutor(workers) as ex:
            odds = list(ex.map(lambda x: espn_odds(x[0]["id"], x[2]["team"]["abbreviation"], x[3]["team"]["abbreviation"]), evs))
        for (ev, c, h, a), o in zip(evs, odds):
            upsert(conn, {"id": f"NBA:espn:{ev['id']}", "league": "NBA", "tip": ev["date"].replace("Z", "+00:00"), "home": h["team"]["displayName"],
                          "away": a["team"]["displayName"], "hp": int(h["score"]), "ap": int(a["score"]), "neutral": int(bool(c.get("neutralSite"))),
                          "src": "espn", **o})
        conn.commit()
        m = el_season(conn, y)
        log.info("сезон %d/%d: НБА %d (с линии %d), Евролига %d", y, (y + 1) % 100, len(evs), sum(1 for o in odds if o.get("spread_home") is not None), m)
