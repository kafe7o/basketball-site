"""Предстоящите мачове и цените (odds API), записът преди мача (tips - не се мени) и уреждането.

„Денят“ е 07:00-07:00 българско време (мачовете на НБА в 02:00 са от „днес“). Всеки мач се записва при първото пускане, в което е в деня
и още не е започнал: шансовете на модела, честните шансове на Pinnacle, цените (средна, най-добра с букмейкъра, Pinnacle) и „ценовият ръб“
(най-добрата цена поне 3% над честната). Уреждане - от историята (ESPN / Евролигата), безплатно.
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import config, db, market, model, odds_api, teams
from .leagues import LEAGUES

log = logging.getLogger(__name__)
SOFIA = ZoneInfo(config.TZ)


def day_of(now):
    local = now.astimezone(SOFIA)
    return (local.date() - timedelta(days=1) if local.hour < 7 else local.date()).isoformat()


def day_bounds(day):
    start = datetime.fromisoformat(day).replace(hour=7, tzinfo=SOFIA)
    return start.astimezone(timezone.utc), (start + timedelta(days=1)).astimezone(timezone.utc)


# ---------- цените ----------

def refresh_prices(conn, now=None):
    """Цените на предстоящите мачове: 3 кредита на първенство; на 3 ч, ако има мач до 12 ч, иначе на 8 ч; само ако има мач до 36 ч."""
    now = now or datetime.now(timezone.utc)
    n = 0
    for code, lg in LEAGUES.items():
        nxt = conn.execute("SELECT MIN(tip) FROM fixtures WHERE league = ? AND tip > ?", (code, now.isoformat())).fetchone()[0]
        last = db.get_meta(conn, f"prices_at:{code}")
        soon = nxt and datetime.fromisoformat(nxt) - now <= timedelta(hours=12)
        every = timedelta(hours=3 if soon else 8)
        if last and now - datetime.fromisoformat(last) < every:
            continue
        free = odds_api.get(conn, f"/sports/{lg['odds_key']}/events", {}, 0, now)          # безплатно - има ли мач до 36 ч
        if not free or not any(datetime.fromisoformat(e["commence_time"].replace("Z", "+00:00")) - now <= timedelta(hours=36) for e in free):
            continue
        evs = odds_api.odds(conn, lg["odds_key"], now)
        if evs is None:
            continue
        db.set_meta(conn, f"prices_at:{code}", now.isoformat(timespec="seconds"))
        for ev in evs:
            tip = datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00"))
            if tip - now > timedelta(hours=36):
                continue
            conn.execute("INSERT INTO fixtures (id, league, tip, home, away, prices_json, updated) VALUES (?, ?, ?, ?, ?, ?, ?) "
                         "ON CONFLICT(id) DO UPDATE SET tip = excluded.tip, prices_json = excluded.prices_json, updated = excluded.updated",
                         (ev["id"], code, tip.isoformat(), ev["home_team"], ev["away_team"], json.dumps(market.event_prices(ev)),
                          now.isoformat(timespec="seconds")))
            n += 1
        log.info("Цени %s: %d мача до 36 ч", code, sum(1 for ev in evs if datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00")) - now <= timedelta(hours=36)))
    conn.execute("DELETE FROM fixtures WHERE tip < ?", ((now - timedelta(days=2)).isoformat(),))
    conn.commit()
    return n


# ---------- прогнозата ----------

def fitted(conn, now):
    """Моделът на всяко първенство - само от мачове преди now; връща {лига: (Ratings или None, мачовете)}."""
    out = {}
    for code in LEAGUES:
        gs = model.league_games(conn, code)
        out[code] = (model.fit(gs, now), gs)
    return out


def forecast(conn, fx, models, now):
    """Записът за един мач: модел (ако познава отборите), честните шансове, цените, ръбът."""
    prices = json.loads(fx["prices_json"] or "{}")
    fair = market.fair(prices)
    r, gs = models.get(fx["league"], (None, []))
    mdl = None
    if r is not None:
        names = set(r.teams)
        h, a = teams.match(conn, fx["league"], fx["home"], names), teams.match(conn, fx["league"], fx["away"], names)
        if h and a:
            tip = datetime.fromisoformat(fx["tip"])
            played = {t: max((datetime.fromisoformat(g["tip"]) for g in gs if t in (g["home"], g["away"]) and datetime.fromisoformat(g["tip"]) < tip), default=None)
                      for t in (h, a)}
            fh, fa = (int(played[t] is not None and tip - played[t] <= timedelta(hours=model.B2B_HOURS)) for t in (h, a))
            mdl = r.probs(h, a, False, fh, fa, (prices.get("spread") or {}).get("line"), (prices.get("total") or {}).get("line"))
            if mdl:
                mdl.update({"home_hist": h, "away_hist": a, "fatigue": [fh, fa]})
    return {"model": mdl, "fair": fair, "prices": prices, "edges": market.edges(prices, fair) if prices else []}


def lock(conn, now=None):
    """Записва мачовете от деня, които още не са започнали и не са записани. Записът не се мени. Връща броя."""
    now = now or datetime.now(timezone.utc)
    day = day_of(now)
    start, end = day_bounds(day)
    rows = conn.execute("SELECT * FROM fixtures WHERE tip >= ? AND tip < ? AND tip > ? AND id NOT IN (SELECT fixture_id FROM tips)",
                        (start.isoformat(), end.isoformat(), now.isoformat())).fetchall()
    if not rows:
        return 0
    models = fitted(conn, now)
    stamp = now.isoformat(timespec="seconds")
    for fx in rows:
        f = forecast(conn, fx, models, now)
        conn.execute("INSERT OR IGNORE INTO tips (fixture_id, league, tip, home, away, day, locked_at, basis, model_json, market_json, prices_json, picks_json) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (fx["id"], fx["league"], fx["tip"], fx["home"], fx["away"], day, stamp, "model" if f["model"] else "market",
                      json.dumps(f["model"]), json.dumps(f["fair"]), json.dumps(f["prices"]), json.dumps({"edges": f["edges"]})))
    conn.commit()
    log.info("Записани %d мача за %s", len(rows), day)
    return len(rows)


def preview(conn, now=None):
    """Предстоящите мачове до 36 ч: записаните - от записа, останалите - сега (предварително)."""
    now = now or datetime.now(timezone.utc)
    models = None
    out = []
    for fx in conn.execute("SELECT * FROM fixtures WHERE tip > ? ORDER BY tip", ((now - timedelta(hours=3)).isoformat(),)).fetchall():
        t = conn.execute("SELECT * FROM tips WHERE fixture_id = ?", (fx["id"],)).fetchone()
        if t:
            f = {"model": json.loads(t["model_json"] or "null"), "fair": json.loads(t["market_json"] or "{}"), "prices": json.loads(t["prices_json"] or "{}"),
                 "edges": json.loads(t["picks_json"] or "{}").get("edges", []), "locked_at": t["locked_at"], "hp": t["hp"], "ap": t["ap"]}
        else:
            models = models or fitted(conn, now)
            f = forecast(conn, fx, models, now)
        out.append({"id": fx["id"], "league": fx["league"], "tip": fx["tip"], "home": fx["home"], "away": fx["away"], **f})
    return out


# ---------- уреждане и мерки ----------

def settle(conn, now=None):
    """Неуредените записи от преди 3 ч -> резултатът от историята (същото първенство, ±6 ч, двата отбора по име)."""
    now = now or datetime.now(timezone.utc)
    n = 0
    for t in conn.execute("SELECT * FROM tips WHERE hp IS NULL AND tip < ?", ((now - timedelta(hours=3)).isoformat(),)).fetchall():
        tip = datetime.fromisoformat(t["tip"])
        cands = conn.execute("SELECT * FROM games WHERE league = ? AND tip BETWEEN ? AND ? AND hp IS NOT NULL",
                             (t["league"], (tip - timedelta(hours=6)).isoformat(), (tip + timedelta(hours=6)).isoformat())).fetchall()
        best = max(cands, key=lambda g: teams.similar(t["home"], g["home"]) + teams.similar(t["away"], g["away"]), default=None)
        if best and teams.similar(t["home"], best["home"]) >= 0.5 and teams.similar(t["away"], best["away"]) >= 0.5:
            conn.execute("UPDATE tips SET hp = ?, ap = ?, game_id = ?, settled_at = ? WHERE fixture_id = ?",
                         (best["hp"], best["ap"], best["id"], now.isoformat(timespec="seconds"), t["fixture_id"]))
            n += 1
    conn.commit()
    return n


def outcome(market_key, side, line, hp, ap):
    """1 излязло, 0 не, None - връщане (точно на линията)."""
    if market_key == "h2h":
        return int((hp > ap) == (side == "home"))
    if market_key == "spread":
        v = hp + line - ap
        return None if v == 0 else int((v > 0) == (side == "home"))
    v = hp + ap - line
    return None if v == 0 else int((v > 0) == (side == "over"))


def results(conn):
    """Мерките на живо: модел срещу пазара (Brier за победител), ценовите ръбове (излезли, доход при най-добрата цена), по първенства."""
    rows = conn.execute("SELECT * FROM tips WHERE hp IS NOT NULL ORDER BY tip DESC").fetchall()
    out = {"settled": len(rows), "by_league": {}, "recent": []}
    acc = {}
    for t in rows:
        lg = acc.setdefault(t["league"], {"n": 0, "bm": 0.0, "bk": 0.0, "nm": 0, "edges": 0, "e_hit": 0, "e_push": 0, "e_pay": 0.0})
        mdl, fair = json.loads(t["model_json"] or "null"), json.loads(t["market_json"] or "{}")
        y = 1 if t["hp"] > t["ap"] else 0
        if fair.get("h2h"):
            lg["n"] += 1
            lg["bk"] += (fair["h2h"]["p"][0] - y) ** 2
            if mdl:
                lg["nm"] += 1
                lg["bm"] += (mdl["h2h"][0] - y) ** 2
        for e in json.loads(t["picks_json"] or "{}").get("edges", []):
            o = outcome(e["market"], e["side"], e.get("line"), t["hp"], t["ap"])
            lg["edges"] += 1
            if o is None:
                lg["e_push"] += 1
                lg["e_pay"] += 1.0
            else:
                lg["e_hit"] += o
                lg["e_pay"] += e["price"] * o
        if len(out["recent"]) < 60:
            out["recent"].append({"league": t["league"], "tip": t["tip"], "home": t["home"], "away": t["away"], "hp": t["hp"], "ap": t["ap"],
                                  "model": mdl, "fair": fair, "edges": json.loads(t["picks_json"] or "{}").get("edges", [])})
    for code, a in acc.items():
        out["by_league"][code] = {"n": a["n"], "brier_market": a["bk"] / a["n"] if a["n"] else None, "n_model": a["nm"],
                                  "brier_model": a["bm"] / a["nm"] if a["nm"] else None, "edges": a["edges"], "edge_hits": a["e_hit"],
                                  "edge_push": a["e_push"], "edge_return": a["e_pay"] / a["edges"] if a["edges"] else None}
    return out
