"""Моделът за точки: рейтинг на атаката (o) и защитата (d) на всеки отбор, предимство на домакина (h), умора при два мача в два поредни дни.

Точки на домакина = m + h/2 + o_дом - d_гост - g1*умора_дом + g2*умора_гост;  на госта - симетрично (без h на неутрален терен).
Претеглени най-малки квадрати с ridge (lam) върху o и d; по-старите мачове тежат по-малко (полуразпад half дни). Разликата и сборът точки
се приемат за нормално разпределени с разсейване от остатъците. Изборът на half и lam - research/model_backtest.py (по протокол).
"""

import math
from collections import defaultdict
from datetime import datetime, timedelta

import numpy as np

from .market import phi

HALF, LAM, WINDOW, MIN_W = 120.0, 5.0, 500, 4.0       # полуразпад в дни, ridge, прозорец в дни, най-малко претеглени мачове на отбор
B2B_HOURS = 30


def _dt(s):
    return datetime.fromisoformat(s)


def fatigue(games):
    """{номер на мача: (умора на домакина, умора на госта)} - отборът е играл в предните 30 часа."""
    last, out = {}, {}
    for g in sorted(games, key=lambda g: g["tip"]):
        t = _dt(g["tip"])
        fh = g["home"] in last and (t - last[g["home"]]) <= timedelta(hours=B2B_HOURS)
        fa = g["away"] in last and (t - last[g["away"]]) <= timedelta(hours=B2B_HOURS)
        out[g["id"]] = (int(fh), int(fa))
        last[g["home"]] = last[g["away"]] = t
    return out


class Ratings:
    def __init__(self, teams, beta, sig_m, sig_t, weight, asof):
        self.teams, self.beta, self.sig_m, self.sig_t, self.weight, self.asof = teams, beta, sig_m, sig_t, weight, asof

    def known(self, team):
        return team in self.teams and self.weight.get(team, 0) >= MIN_W

    def predict(self, home, away, neutral=False, fh=0, fa=0):
        """-> (очаквана разлика дом-гост, очакван сбор) или None за непознат отбор."""
        if not (self.known(home) and self.known(away)):
            return None
        b, ix = self.beta, self.teams
        n = len(ix)
        m, h, g1, g2 = b[0], b[1] * (0 if neutral else 1), b[2], b[3]
        oh, oa, dh, da = b[4 + ix[home]], b[4 + ix[away]], b[4 + n + ix[home]], b[4 + n + ix[away]]
        ph = m + h / 2 + oh - da - g1 * fh + g2 * fa
        pa = m - h / 2 + oa - dh - g1 * fa + g2 * fh
        return ph - pa, ph + pa

    def probs(self, home, away, neutral=False, fh=0, fa=0, spread_line=None, total_line=None):
        pr = self.predict(home, away, neutral, fh, fa)
        if pr is None:
            return None
        mg, tot = pr
        p_home = phi(mg / self.sig_m)
        out = {"margin": round(mg, 2), "total": round(tot, 1), "sig_m": round(self.sig_m, 2), "sig_t": round(self.sig_t, 2),
               "h2h": [round(p_home, 4), round(1 - p_home, 4)]}
        if spread_line is not None:
            ps = phi((mg + spread_line) / self.sig_m)
            out["spread"] = {"line": spread_line, "p": [round(ps, 4), round(1 - ps, 4)]}
        if total_line is not None:
            po = 1 - phi((total_line - tot) / self.sig_t)
            out["total_p"] = {"line": total_line, "p": [round(po, 4), round(1 - po, 4)]}
        return out


def fit(games, asof, half=HALF, lam=LAM, window=WINDOW):
    """games: [{id, tip, home, away, hp, ap, neutral}] (изиграни) -> Ratings само от мачове ПРЕДИ asof, или None при малко данни."""
    asof = asof if isinstance(asof, datetime) else _dt(asof)
    lo = asof - timedelta(days=window)
    gs = [g for g in games if g["hp"] is not None and lo <= _dt(g["tip"]) < asof]
    if len(gs) < 60:
        return None
    fat = fatigue(gs)
    teams = sorted({g["home"] for g in gs} | {g["away"] for g in gs})
    ix = {t: i for i, t in enumerate(teams)}
    n, p = len(teams), 4 + 2 * len(teams)
    X = np.zeros((2 * len(gs), p))
    y = np.zeros(2 * len(gs))
    w = np.zeros(2 * len(gs))
    tw = defaultdict(float)
    for k, g in enumerate(gs):
        age = (asof - _dt(g["tip"])).total_seconds() / 86400
        wk = 0.5 ** (age / half)
        fh, fa = fat[g["id"]]
        hm = 0 if g.get("neutral") else 1
        r1, r2 = 2 * k, 2 * k + 1
        X[r1, [0, 1, 2, 3]] = [1, hm / 2, -fh, fa]
        X[r1, 4 + ix[g["home"]]] = 1
        X[r1, 4 + n + ix[g["away"]]] = -1
        X[r2, [0, 1, 2, 3]] = [1, -hm / 2, -fa, fh]
        X[r2, 4 + ix[g["away"]]] = 1
        X[r2, 4 + n + ix[g["home"]]] = -1
        y[r1], y[r2] = g["hp"], g["ap"]
        w[r1] = w[r2] = wk
        tw[g["home"]] += wk
        tw[g["away"]] += wk
    reg = np.full(p, 1e-6)                 # нищожно - само за стабилност (Евролигата почти няма мачове в два поредни дни -> нулева колона)
    reg[4:] = lam
    A = (X * w[:, None]).T @ X + np.diag(reg)
    beta = np.linalg.solve(A, (X * w[:, None]).T @ y)
    pred = X @ beta
    mg_res = (y[0::2] - y[1::2]) - (pred[0::2] - pred[1::2])
    tt_res = (y[0::2] + y[1::2]) - (pred[0::2] + pred[1::2])
    ww = w[0::2]
    sig_m = math.sqrt(float(np.sum(ww * mg_res ** 2) / np.sum(ww)))
    sig_t = math.sqrt(float(np.sum(ww * tt_res ** 2) / np.sum(ww)))
    return Ratings(ix, beta, sig_m, sig_t, dict(tw), asof)


def league_games(conn, league):
    return [dict(r) for r in conn.execute("SELECT id, tip, home, away, hp, ap, neutral FROM games WHERE league = ? AND hp IS NOT NULL ORDER BY tip", (league,))]
