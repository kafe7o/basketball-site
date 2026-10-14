"""Сайтът: site_template.html + данните като JSON -> docs/index.html (GitHub Pages от папката docs)."""

import json
import logging
from datetime import datetime, timezone

from . import config, db, odds_api, tips
from .leagues import LEAGUES

log = logging.getLogger(__name__)


def build(conn, now=None):
    now = now or datetime.now(timezone.utc)
    bt = config.DATA_DIR / "backtest.json"
    used, remaining = odds_api.spent(conn, now)
    data = {"generated": now.isoformat(timespec="seconds"), "day": tips.day_of(now),
            "leagues": {k: {"title": v["title"], "flag": v["flag"]} for k, v in LEAGUES.items()},
            "upcoming": tips.preview(conn, now), "results": tips.results(conn),
            "credits": {"used": used, "remaining": remaining, "budget": config.ODDS_BUDGET_MONTH},
            "history": {r["league"]: {"n": r["n"], "first": r["first"], "last": r["last"]} for r in
                        conn.execute("SELECT league, COUNT(*) n, MIN(tip) first, MAX(tip) last FROM games WHERE hp IS NOT NULL GROUP BY league")},
            "backtest": json.loads(bt.read_text(encoding="utf-8")) if bt.exists() else None}
    html = config.TEMPLATE.read_text(encoding="utf-8").replace("/*__DATA__*/null", json.dumps(data, ensure_ascii=False))
    config.SITE_DIR.mkdir(parents=True, exist_ok=True)
    (config.SITE_DIR / "index.html").write_text(html, encoding="utf-8")
    (config.SITE_DIR / ".nojekyll").write_text("", encoding="utf-8")
    log.info("Сайтът е построен: %d предстоящи, %d уредени, %d KB", len(data["upcoming"]), data["results"]["settled"], len(html) // 1024)
    return len(html)
