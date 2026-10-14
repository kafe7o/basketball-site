"""
Моделът назад - знае ли нещо срещу пазара (2026-10-14).

ПРОТОКОЛ (записан ПРЕДИ пускането):
Данни: robot.db - НБА (ESPN, с затварящите линии на ESPN BET: коефициенти за победа, хендикап, общо точки), сезони 2022/23-2026/27; Евролига
(официалният сайт, без линии), същите сезони.
Walk-forward: моделът (hoops/model.py) се обучава на 1-во число на всеки месец САМО на мачовете преди това, прогноза за мачовете на месеца.
Варианти: полуразпад {60, 120, 240} дни x ridge {1, 5, 20}.
ИЗБОР: сезоните 2022/23 и 2023/24 - вариантът с най-малък Brier за победителя в НБА. ЧИСТА ПРОВЕРКА: 2024/25 нататък - само избраният.
Мери се (НБА): Brier за победителя - модел срещу пазара (коефициентите на ESPN BET без маржа, степенно); хендикап - колко често страната на модела
(по-вероятната спрямо линията) печели, без връщанията (на равна цена 1.91 печалбата иска над 52.4%); общо точки - същото; и разликата модел-пазар като филтър
(само мачовете, където моделът се разминава с пазара с над 5 пункта за победителя). Евролига: Brier на модела срещу „домакинът печели колкото досега“.
ПРЕДИМСТВО се приема само ако е в една посока и над 2 грешки в избора и в чистата; иначе - няма. Ако моделът бие пазара - първо се търси грешка.
Резултатът: data/backtest.json (и кратко резюме за сайта).
"""

import json
import math
import sys
from datetime import date, datetime, timezone
from itertools import product
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hoops import db, market, model                    # noqa: E402

OUT = ROOT / "data" / "backtest.json"
SELECT_END = "2024-07-01"


def months(first, last):
    y, m = int(first[:4]), int(first[5:7])
    while f"{y:04d}-{m:02d}" <= last[:7]:
        yield f"{y:04d}-{m:02d}-01"
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def walk(games, half, lam):
    """-> [(мач, вероятности на модела)] за всички мачове от 2022-11 нататък (първият месец е само за учене)."""
    fat = model.fatigue(games)
    out = []
    for m0 in months("2022-11-01", games[-1]["tip"]):
        y, mo = int(m0[:4]), int(m0[5:7])
        m1 = f"{y + (mo == 12):04d}-{(mo % 12) + 1:02d}-01"
        target = [g for g in games if m0 <= g["tip"][:10] < m1]
        if not target:
            continue
        r = model.fit(games, datetime.fromisoformat(m0).replace(tzinfo=timezone.utc), half, lam)
        if r is None:
            continue
        for g in target:
            fh, fa = fat[g["id"]]
            p = r.probs(g["home"], g["away"], bool(g.get("neutral")), fh, fa, g.get("spread_home"), g.get("total"))
            if p:
                out.append((g, p))
    return out


def se_mean(xs):
    n = len(xs)
    if n < 2:
        return 0.0
    m = sum(xs) / n
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1) / n)


def evaluate(pairs, with_market):
    res = {}
    for name, keep in (("избор", lambda g: g["tip"] < SELECT_END), ("чиста", lambda g: g["tip"] >= SELECT_END)):
        ps = [(g, p) for g, p in pairs if keep(g)]
        if not ps:
            continue
        bm, bk, diff = [], [], []
        ats, tot, ats_dis = [], [], []
        base_home = []
        for g, p in ps:
            y = 1 if g["hp"] > g["ap"] else 0
            bm.append((p["h2h"][0] - y) ** 2)
            if with_market and g.get("ml_home") and g.get("ml_away"):
                f = market.devig([g["ml_home"], g["ml_away"]])
                if f:
                    bk.append((f[0] - y) ** 2)
                    diff.append((p["h2h"][0] - y) ** 2 - (f[0] - y) ** 2)
                    if abs(p["h2h"][0] - f[0]) >= 0.05 and p.get("spread"):
                        v = g["hp"] + g["spread_home"] - g["ap"]
                        if v != 0:
                            ats_dis.append(int((v > 0) == (p["spread"]["p"][0] > 0.5)))
            if with_market and p.get("spread") and g.get("spread_home") is not None:
                v = g["hp"] + g["spread_home"] - g["ap"]
                if v != 0:
                    ats.append(int((v > 0) == (p["spread"]["p"][0] > 0.5)))
            if with_market and p.get("total_p") and g.get("total") is not None:
                v = g["hp"] + g["ap"] - g["total"]
                if v != 0:
                    tot.append(int((v > 0) == (p["total_p"]["p"][0] > 0.5)))
        r = {"n": len(ps), "brier_model": sum(bm) / len(bm)}
        if bk:
            r.update({"n_market": len(bk), "brier_market": sum(bk) / len(bk), "diff": sum(diff) / len(diff), "diff_se": se_mean(diff)})
        for k, xs in (("ats", ats), ("totals", tot), ("ats_disagree", ats_dis)):
            if xs:
                r[k] = {"n": len(xs), "hit": sum(xs) / len(xs), "se": math.sqrt(sum(xs) / len(xs) * (1 - sum(xs) / len(xs)) / len(xs))}
        res[name] = r
    return res


def baseline_el(games):
    """Евролига: „домакинът печели колкото досега“ - Brier на базата по същите месеци."""
    out = {}
    for name, keep in (("избор", lambda g: g["tip"] < SELECT_END), ("чиста", lambda g: g["tip"] >= SELECT_END)):
        bs = []
        for g in games:
            if g["tip"] < "2022-11-01" or not keep(g):
                continue
            prior = [x for x in games if x["tip"] < g["tip"][:7] + "-01"]
            rate = sum(1 for x in prior if x["hp"] > x["ap"]) / len(prior) if prior else 0.6
            bs.append((rate - (1 if g["hp"] > g["ap"] else 0)) ** 2)
        if bs:
            out[name] = sum(bs) / len(bs)
    return out


def main():
    conn = db.init()
    nba = [dict(r) for r in conn.execute("SELECT * FROM games WHERE league = 'NBA' AND hp IS NOT NULL ORDER BY tip")]
    el = [dict(r) for r in conn.execute("SELECT * FROM games WHERE league = 'EL' AND hp IS NOT NULL ORDER BY tip")]
    print(f"НБА: {len(nba)} мача ({sum(1 for g in nba if g['ml_home'])} с коефициенти), Евролига: {len(el)}")
    variants = {}
    for half, lam in product((60, 120, 240), (1, 5, 20)):
        variants[(half, lam)] = evaluate(walk(nba, half, lam), True)
        s = variants[(half, lam)].get("избор", {})
        print(f"  НБА полуразпад {half}, ridge {lam}: избор Brier {s.get('brier_model', 0):.4f}")
    chosen = min(variants, key=lambda k: variants[k]["избор"]["brier_model"])
    res = variants[chosen]
    print(f"\nИЗБРАН: полуразпад {chosen[0]}, ridge {chosen[1]}")
    for per, r in res.items():
        line = f"  {per}: {r['n']} мача, Brier модел {r['brier_model']:.4f}"
        if "brier_market" in r:
            line += f" | пазар {r['brier_market']:.4f} (разлика {r['diff']:+.4f} ± {r['diff_se']:.4f}, {r['diff'] / r['diff_se']:+.1f} грешки)"
        for k, lab in (("ats", "хендикап"), ("totals", "общо точки"), ("ats_disagree", "хендикап при разминаване 5+")):
            if k in r:
                line += f" | {lab}: {r[k]['hit']:.1%} ± {r[k]['se']:.1%} (n {r[k]['n']})"
        print(line)
    el_res = evaluate(walk(el, *chosen), False) if el else {}
    el_base = baseline_el(el) if el else {}
    for per, r in el_res.items():
        print(f"  Евролига {per}: {r['n']} мача, Brier модел {r['brier_model']:.4f} | база (домакинът колкото досега) {el_base.get(per, float('nan')):.4f}")
    edge = any(per in res and "diff" in res[per] and res[per]["diff"] < -2 * res[per]["diff_se"] for per in ("избор",)) and \
        all(per in res and "diff" in res[per] and res[per]["diff"] < -2 * res[per]["diff_se"] for per in ("избор", "чиста"))
    c = res.get("чиста", {})
    summary = (f"НБА, walk-forward по месеци, избор 2022/23-2023/24, чиста проверка 2024/25 нататък (полуразпад {chosen[0]} дни, ridge {chosen[1]}). "
               f"Чиста проверка: {c.get('n', 0)} мача; грешка за победителя - роботът {c.get('brier_model', 0):.3f}, пазарът {c.get('brier_market', 0):.3f}"
               + (f"; хендикап по робота - {c['ats']['hit']:.1%} ± {c['ats']['se']:.1%} (при равна цена 1.91 печалбата иска над 52.4%)" if "ats" in c else "")
               + (f"; общо точки - {c['totals']['hit']:.1%} ± {c['totals']['se']:.1%}" if "totals" in c else "")
               + (". ПРЕДИМСТВО НАД ПАЗАРА: няма." if not edge else ". Роботът изглежда по-точен от пазара - първо се търси грешка."))
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps({"generated": date.today().isoformat(), "chosen": {"half": chosen[0], "lam": chosen[1]},
                               "nba": {f"{k[0]}_{k[1]}": v for k, v in variants.items()}, "nba_chosen": res, "el": el_res, "el_base": el_base,
                               "edge": edge, "summary_html": summary}, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n" + summary)
    print("записано:", OUT)


if __name__ == "__main__":
    main()
