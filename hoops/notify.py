"""ntfy: отделна тема за баскетбола (NTFY_TOPIC в .env / в тайните на GitHub). Сутрин - мачовете за деня, „цена над честната“ и вчера."""

import json
import logging
import os
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

from . import config, db, tips
from .leagues import LEAGUES

log = logging.getLogger(__name__)
NTFY_URL = "https://ntfy.sh"
BOOKS = {"pinnacle": "Pinnacle", "betfair_ex_eu": "Betfair", "williamhill": "William Hill", "unibet_eu": "Unibet", "betsson": "Betsson",
         "nordicbet": "NordicBet", "marathonbet": "Marathon", "onexbet": "1xBet", "sport888": "888sport", "betclic": "Betclic", "coolbet": "Coolbet",
         "everygame": "Everygame", "gtbets": "GTbets", "tipico_de": "Tipico", "winamax_fr": "Winamax", "suprabets": "Suprabets", "leovegas": "LeoVegas"}


def send(title, message, tags="basketball", priority=3):
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        log.info("Известие (няма NTFY_TOPIC, не е пратено): %s | %s", title, message)
        return False
    body = json.dumps({"topic": topic, "title": title, "message": message, "tags": [tags], "priority": priority, "click": config.SITE_URL},
                      ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(NTFY_URL, data=body, method="POST", headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError) as e:
        log.error("Известието не тръгна: %s", e)
        return False


def edge_text(fx, e):
    name = {"home": fx["home"], "away": fx["away"], "over": "над", "under": "под"}[e["side"]]
    what = (f"{name} победа" if e["market"] == "h2h" else f"{name} {e['line']:+g}" if e["market"] == "spread" and e["side"] == "home"
            else f"{name} {-e['line']:+g}" if e["market"] == "spread" else f"{name} {e['line']:g} точки")
    return (f"{what} @{e['price']:.2f} ({BOOKS.get(e['book'], e['book'])}), честна {1 / e['fair']:.2f}, +{e['edge']:.0%}; "
            f"в Mr Bit - ако дава поне {1.03 / e['fair']:.2f}")


def morning(conn, now=None):
    now = now or datetime.now(timezone.utc)
    local = now.astimezone(tips.SOFIA)
    if not 7 <= local.hour < 13:
        return False
    day = tips.day_of(now)
    if db.get_meta(conn, f"notified:morning:{day}"):
        return False
    start, end = tips.day_bounds(day)
    rows = conn.execute("SELECT * FROM tips WHERE day = ? ORDER BY tip", (day,)).fetchall()
    lines = []
    if rows:
        by = {}
        for r in rows:
            by[r["league"]] = by.get(r["league"], 0) + 1
        lines.append(f"{len(rows)} мача: " + ", ".join(f"{LEAGUES[k]['title']} {v}" for k, v in by.items()) + ".")
        ed = [(r, e) for r in rows for e in json.loads(r["picks_json"] or "{}").get("edges", [])]
        if ed:
            lines.append("ЦЕНА НАД ЧЕСТНАТА (най-добрият букмейкър плаща поне 3% над Pinnacle без маржа; не е сигурен залог):")
            for r, e in ed[:8]:
                t = datetime.fromisoformat(r["tip"]).astimezone(tips.SOFIA).strftime("%H:%M")
                lines.append(f"{t} {r['home']} - {r['away']}: {edge_text(r, e)}")
        else:
            lines.append("Цена над честната: днес няма.")
    else:
        lines.append("Днес няма мачове в НБА и Евролигата.")
    ystart = start - timedelta(days=1)
    y = conn.execute("SELECT * FROM tips WHERE tip >= ? AND tip < ? AND hp IS NOT NULL", (ystart.isoformat(), start.isoformat())).fetchall()
    if y:
        lines.append(f"Вчера: {len(y)} уредени мача - " + "; ".join(f"{r['home']} {r['hp']}:{r['ap']} {r['away']}" for r in y[:6]) + ("..." if len(y) > 6 else ""))
    sent = send(f"Баскетбол {local.strftime('%d.%m')}", "\n".join(lines))
    db.set_meta(conn, f"notified:morning:{day}", now.isoformat(timespec="seconds"))
    return sent
