"""SQLite базата: история (резултати и пазарни линии назад), предстоящи мачове с цени, записът (tips, не се мени), служебни стойности."""

import sqlite3

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    id TEXT PRIMARY KEY,            -- лига:източник:номер
    league TEXT NOT NULL,
    tip TEXT NOT NULL,              -- начало, UTC ISO
    home TEXT NOT NULL, away TEXT NOT NULL,
    hp INTEGER, ap INTEGER,         -- точки (NULL - не е изигран)
    neutral INTEGER DEFAULT 0,
    src TEXT,
    ml_home REAL, ml_away REAL,     -- затварящ коефициент за победа (десетичен), ако източникът го дава (ESPN за НБА)
    spread_home REAL, total REAL,   -- затваряща линия: хендикап на домакина и общо точки
    book TEXT
);
CREATE INDEX IF NOT EXISTS games_league_tip ON games(league, tip);
CREATE TABLE IF NOT EXISTS fixtures (
    id TEXT PRIMARY KEY,            -- номерът на събитието в odds API
    league TEXT NOT NULL, tip TEXT NOT NULL, home TEXT NOT NULL, away TEXT NOT NULL,
    prices_json TEXT, updated TEXT
);
CREATE TABLE IF NOT EXISTS tips (
    fixture_id TEXT PRIMARY KEY,
    league TEXT NOT NULL, tip TEXT NOT NULL, home TEXT NOT NULL, away TEXT NOT NULL,
    day TEXT NOT NULL,              -- „денят“ 07:00-07:00 българско време
    locked_at TEXT NOT NULL,
    basis TEXT NOT NULL,            -- model / market (без модел - няма оценка на робота)
    model_json TEXT, market_json TEXT, prices_json TEXT, picks_json TEXT,
    hp INTEGER, ap INTEGER, settled_at TEXT, game_id TEXT
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS credits (month TEXT PRIMARY KEY, used INTEGER NOT NULL DEFAULT 0, remaining INTEGER);
"""


def init(path=None):
    conn = sqlite3.connect(path or config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def get_meta(conn, key):
    r = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return r["value"] if r else None


def set_meta(conn, key, value):
    conn.execute("INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, str(value)))
    conn.commit()
