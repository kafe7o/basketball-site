"""Цените: средна, най-добра (с букмейкъра) и Pinnacle за всеки изход; честният шанс от Pinnacle без маржа (степенно, както във футбола);
„ценови ръб“ = най-добрата цена x честния шанс - 1. Борсите (Betfair, Matchbook, Smarkets) не се броят за „най-добра“ - имат комисионна."""

import math

EXCHANGES = {"betfair_ex_eu", "betfair_ex_uk", "matchbook", "smarkets", "betfair_ex_au"}
EDGE = 0.03                       # залог по цената - само от 3% над честната (футбол: research/price_edge_backtest.py)


def devig(prices):
    """[коефициенти] -> вероятности без маржа (степенно: сбор (1/к)^k = 1)."""
    inv = [1 / p for p in prices]
    if any(p <= 1.0 for p in prices) or sum(inv) < 0.95:
        return None
    lo, hi = 1.0, 8.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if sum(x ** mid for x in inv) > 1:
            lo = mid
        else:
            hi = mid
    return [x ** ((lo + hi) / 2) for x in inv]


def _summary(rows):
    """rows: [(букмейкър, цена)] за един изход -> {avg, best, best_book, pin, n}."""
    if not rows:
        return None
    books = [(b, p) for b, p in rows if b not in EXCHANGES]
    best_b, best_p = max(books or rows, key=lambda x: x[1])
    pin = next((p for b, p in rows if b == "pinnacle"), None)
    return {"avg": round(sum(p for _, p in rows) / len(rows), 3), "best": best_p, "best_book": best_b, "pin": pin, "n": len(rows)}


def event_prices(ev):
    """Събитие от odds API -> {h2h: {home, away}, spread: {line, home, away}, total: {line, over, under}}.
    При хендикапа и сбора се взима линията на Pinnacle (иначе най-честата) и само цените на тази линия."""
    home, away = ev["home_team"], ev["away_team"]
    h2h, sp, tot = {"home": [], "away": []}, {}, {}
    for b in ev.get("bookmakers", []):
        for m in b.get("markets", []):
            for o in m.get("outcomes", []):
                side = "home" if o["name"] == home else "away" if o["name"] == away else o["name"].lower()
                if m["key"] == "h2h" and side in ("home", "away"):
                    h2h[side].append((b["key"], o["price"]))
                elif m["key"] == "spreads" and side in ("home", "away") and o.get("point") is not None:
                    hl = o["point"] if side == "home" else -o["point"]          # линията винаги откъм домакина (-5.5 = домакинът дава 5.5)
                    d = sp.setdefault(hl, {"home": [], "away": [], "books": set()})
                    d[side].append((b["key"], o["price"]))
                    d["books"].add(b["key"])
                elif m["key"] == "totals" and side in ("over", "under") and o.get("point") is not None:
                    d = tot.setdefault(o["point"], {"over": [], "under": [], "books": set()})
                    d[side].append((b["key"], o["price"]))
                    d["books"].add(b["key"])

    def main_line(d):
        if not d:
            return None
        pin = [ln for ln, v in d.items() if "pinnacle" in v["books"]]
        return pin[0] if pin else max(d, key=lambda ln: len(d[ln]["books"]))

    out = {"h2h": {s: _summary(v) for s, v in h2h.items()}}
    ln = main_line(sp)
    if ln is not None:
        out["spread"] = {"line": ln, "home": _summary(sp[ln]["home"]), "away": _summary(sp[ln]["away"])}
    ln = main_line(tot)
    if ln is not None:
        out["total"] = {"line": ln, "over": _summary(tot[ln]["over"]), "under": _summary(tot[ln]["under"])}
    return out


def fair(prices):
    """Честните шансове от Pinnacle (без маржа) за трите пазара: {h2h: [дом, гост], spread: [дом, гост], total: [над, под]}; без Pinnacle - от средната."""
    out = {}
    for mk, sides in (("h2h", ("home", "away")), ("spread", ("home", "away")), ("total", ("over", "under"))):
        m = prices.get(mk)
        if not m or not all(m.get(s) for s in sides):
            continue
        pin = [m[s]["pin"] for s in sides]
        src = "pinnacle" if all(pin) else "avg"
        p = devig(pin if all(pin) else [m[s]["avg"] for s in sides])
        if p:
            out[mk] = {"p": [round(x, 4) for x in p], "src": src}
    return out


def edges(prices, fr):
    """Изходите, при които най-добрата цена е поне 3% над честната по Pinnacle: [{market, side, line, price, book, fair, edge}]."""
    res = []
    for mk, sides in (("h2h", ("home", "away")), ("spread", ("home", "away")), ("total", ("over", "under"))):
        f = fr.get(mk)
        if not f or f["src"] != "pinnacle":
            continue
        for s, p in zip(sides, f["p"]):
            x = prices[mk][s]
            e = x["best"] * p - 1
            if e >= EDGE and x["best_book"] != "pinnacle":
                res.append({"market": mk, "side": s, "line": prices[mk].get("line"), "price": x["best"], "book": x["best_book"],
                            "fair": round(p, 4), "edge": round(e, 4)})
    return res


def phi(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def american_to_decimal(a):
    if a is None:
        return None
    a = float(a)
    return round(1 + a / 100, 4) if a > 0 else round(1 + 100 / -a, 4) if a < 0 else None
