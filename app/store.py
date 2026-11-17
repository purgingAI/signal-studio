"""SQLite store: news, ideas, drafts (with approval status + versions) and About-section drafts."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "studio.db"
DB.parent.mkdir(exist_ok=True)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def init() -> None:
    with conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS news(id INTEGER PRIMARY KEY, url TEXT UNIQUE, title TEXT, source TEXT,
            summary TEXT, published TEXT, fetched_at TEXT);
        CREATE TABLE IF NOT EXISTS ideas(id INTEGER PRIMARY KEY, created TEXT, news_id INTEGER, project_id TEXT,
            data TEXT, status TEXT DEFAULT 'new');
        CREATE TABLE IF NOT EXISTS drafts(id INTEGER PRIMARY KEY, idea_id INTEGER, created TEXT, updated TEXT,
            text TEXT, hashtags TEXT, first_comment TEXT, image_idea TEXT, status TEXT DEFAULT 'pending',
            feedback TEXT, version INTEGER DEFAULT 1);
        CREATE TABLE IF NOT EXISTS about(id INTEGER PRIMARY KEY, created TEXT, label TEXT, headline TEXT,
            text TEXT, status TEXT DEFAULT 'pending');
        CREATE TABLE IF NOT EXISTS runs(id INTEGER PRIMARY KEY, started TEXT, finished TEXT, kind TEXT, note TEXT);
        CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY, v TEXT);
        """)
        cols = {r[1] for r in c.execute("PRAGMA table_info(drafts)")}
        if "post_url" not in cols:
            c.execute("ALTER TABLE drafts ADD COLUMN post_url TEXT")
        if "what_changed" not in {r[1] for r in c.execute("PRAGMA table_info(about)")}:
            c.execute("ALTER TABLE about ADD COLUMN what_changed TEXT")


def get_setting(k: str) -> str | None:
    r = one("SELECT v FROM settings WHERE k=?", k)
    return r["v"] if r and r["v"] else None


def set_setting(k: str, v: str) -> None:
    exec_("INSERT INTO settings(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", k, v)


def rows(sql: str, *args) -> list[dict]:
    with conn() as c:
        return [dict(r) for r in c.execute(sql, args).fetchall()]


def one(sql: str, *args) -> dict | None:
    r = rows(sql, *args)
    return r[0] if r else None


def exec_(sql: str, *args) -> int:
    with conn() as c:
        cur = c.execute(sql, args)
        return cur.lastrowid


def save_news(items: list[dict]) -> int:
    added = 0
    with conn() as c:
        for i in items:
            cur = c.execute("INSERT OR IGNORE INTO news(url,title,source,summary,published,fetched_at) VALUES(?,?,?,?,?,?)",
                            (i["url"], i["title"], i["source"], i["summary"], i["published"], now()))
            added += cur.rowcount
    return added


def idea_row(r: dict) -> dict:
    d = json.loads(r["data"])
    d.update(id=r["id"], created=r["created"], status=r["status"], news_id=r["news_id"], project_id=r["project_id"])
    return d
