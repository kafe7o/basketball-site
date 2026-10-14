"""Баскетболният робот.

    python run.py cloud       часовият цикъл (GitHub Actions): история -> цени -> запис -> уреждане -> сайт -> известие
    python run.py local       същото на лаптопа, без известие
    python run.py backfill    историята от 2022 (еднократно, на лаптопа)
    python run.py site        само сайтът
    python run.py status      какво има в базата и колко кредита са похарчени
"""

import logging
import sys
from datetime import datetime, timezone

from hoops import config, db, history, notify, odds_api, site, tips


def setup_logging():
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler(config.LOG_DIR / "robot.log", encoding="utf-8")])
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    return logging.getLogger("run")


def step(log, name, fn, *args):
    """Една стъпка: грешката се лога с подробности и цикълът продължава (не се мълчи)."""
    try:
        res = fn(*args)
        log.info("%s: ок (%s)", name, res)
        return True
    except Exception:                                   # noqa: BLE001 - логва се целият стек, после следващата стъпка
        log.exception("%s: ГРЕШКА", name)
        return False


def cycle(log, send):
    now = datetime.now(timezone.utc)
    conn = db.init()
    ok = [step(log, "1. История (ESPN, Евролига)", history.update, conn, now),
          step(log, "2. Цени (odds API)", tips.refresh_prices, conn, now),
          step(log, "3. Запис преди мача", tips.lock, conn, now),
          step(log, "4. Уреждане", tips.settle, conn, now),
          step(log, "5. Сайтът", site.build, conn, now)]
    if send:
        ok.append(step(log, "6. Сутрешното известие", notify.morning, conn, now))
    used, remaining = odds_api.spent(conn, now)
    log.info("odds API този месец: %d кредита за баскетбола (таван %d), остатък на ключа %s", used, config.ODDS_BUDGET_MONTH, remaining)
    conn.close()
    return all(ok)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    log = setup_logging()
    if cmd == "cloud":
        sys.exit(0 if cycle(log, send=True) else 1)
    if cmd == "local":
        sys.exit(0 if cycle(log, send=False) else 1)
    conn = db.init()
    if cmd == "backfill":
        history.backfill(conn, 2022)
    elif cmd == "site":
        site.build(conn)
    elif cmd == "status":
        for r in conn.execute("SELECT league, COUNT(*), MIN(tip), MAX(tip), SUM(spread_home IS NOT NULL) FROM games GROUP BY league"):
            print("история:", tuple(r))
        print("предстоящи:", conn.execute("SELECT COUNT(*) FROM fixtures").fetchone()[0], "| записани:", conn.execute("SELECT COUNT(*) FROM tips").fetchone()[0],
              "| уредени:", conn.execute("SELECT COUNT(*) FROM tips WHERE hp IS NOT NULL").fetchone()[0])
        print("кредити този месец:", odds_api.spent(conn))
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
