"""Имената на отборите: odds API пише имената по свой начин, ESPN и Евролигата - по свой. Сравнение по думи; преводът се помни в meta."""

import re
import unicodedata

from . import db

NOISE = {"bc", "kk", "fc", "cb", "sk", "bk", "basket", "basketball", "club", "the", "s.a.d", "sad", "bk.", "mozzart", "bet", "beko", "istanbul"}


def norm(name):
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return [w for w in s.split() if w and w not in NOISE]


def similar(a, b):
    """Дял на общите думи от по-краткото име (0..1)."""
    wa, wb = set(norm(a)), set(norm(b))
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / min(len(wa), len(wb))


def match(conn, league, name, candidates):
    """Името от odds API -> името в историята (единственото най-добро, поне 0.5), или None. Запомня превода."""
    key = f"team:{league}:{name}"
    known = db.get_meta(conn, key)
    if known and known in candidates:
        return known
    if name in candidates:
        return name
    scored = sorted(((similar(name, c), c) for c in candidates), reverse=True)
    if scored and scored[0][0] >= 0.5 and (len(scored) == 1 or scored[0][0] > scored[1][0]):
        db.set_meta(conn, key, scored[0][1])
        return scored[0][1]
    return None
