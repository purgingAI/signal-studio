"""LinkedIn Studio API + daily scheduler. Run: ./run.sh  → http://localhost:8099"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import secrets
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import agent, linkedin, news, store

WEB = Path(__file__).resolve().parent.parent / "web"
DAILY_HOUR = int(os.environ.get("STUDIO_DAILY_HOUR", "9"))      # local time; -1 disables the daily run
AUTO_DRAFTS = int(os.environ.get("STUDIO_AUTO_DRAFTS", "2"))     # best ideas auto-drafted per daily run

app = FastAPI(title="LinkedIn Studio")

# ---------- login (a simple password page; replaces the browser's basic-auth popup) ----------
PASSWORD = os.environ.get("STUDIO_PASSWORD", "")
SECRET = (os.environ.get("STUDIO_SECRET") or PASSWORD or secrets.token_hex(16)).encode()
COOKIE = "studio_session"
OPEN_PATHS = {"/login", "/favicon.ico"}


def _session_ok(request: Request) -> bool:
    want = hmac.new(SECRET, b"signal-studio", hashlib.sha256).hexdigest()
    return hmac.compare_digest(request.cookies.get(COOKIE, ""), want)


@app.middleware("http")
async def require_login(request: Request, call_next):
    if not PASSWORD or request.url.path in OPEN_PATHS or _session_ok(request):
        return await call_next(request)
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": "Signed out, reload the page"}, status_code=401)
    return RedirectResponse("/login")


LOGIN_HTML = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Signal Studio</title><style>
:root{--bg:#07080d;--t:#e9ecf5;--m:#8b91a7}*{box-sizing:border-box}
body{margin:0;min-height:100vh;display:grid;place-items:center;background:var(--bg);color:var(--t);font:15px Inter,system-ui,sans-serif;padding:16px;
background-image:radial-gradient(40% 35% at 20% 15%,rgba(124,92,255,.25),transparent 70%),radial-gradient(35% 30% at 80% 20%,rgba(34,211,238,.18),transparent 70%)}
form{width:100%;max-width:360px;background:rgba(20,22,34,.8);border:1px solid rgba(255,255,255,.1);border-radius:18px;padding:28px}
.l{width:44px;height:44px;border-radius:12px;background:linear-gradient(135deg,#7c5cff,#22d3ee);display:grid;place-items:center;font-size:20px;margin-bottom:14px}
h1{margin:0 0 4px;font-size:22px}p{margin:0 0 18px;color:var(--m);font-size:13px}
input{width:100%;font:inherit;font-size:16px;color:var(--t);background:#11131e;border:1px solid rgba(255,255,255,.15);border-radius:12px;padding:12px;margin-bottom:12px}
button{width:100%;font:inherit;font-weight:600;color:#fff;border:0;border-radius:12px;padding:12px;background:linear-gradient(135deg,#7c5cff,#22d3ee);cursor:pointer}
.e{color:#f87171;font-size:13px;margin-bottom:10px}</style></head><body>
<form method="post" action="/login"><div class="l">◆</div><h1>Signal Studio</h1><p>Your LinkedIn agent</p>
__ERR__<input type="password" name="password" placeholder="Password" autofocus autocomplete="current-password">
<button>Sign in</button></form></body></html>"""


@app.get("/login")
def login_page(e: int = 0):
    return HTMLResponse(LOGIN_HTML.replace("__ERR__", '<div class="e">Wrong password</div>' if e else ""))


@app.post("/login")
async def login(request: Request):
    form = (await request.body()).decode()
    from urllib.parse import parse_qs
    pw = (parse_qs(form).get("password") or [""])[0]
    if not PASSWORD or not hmac.compare_digest(pw, PASSWORD):
        await asyncio.sleep(1)  # slow down guessing
        return RedirectResponse("/login?e=1", status_code=303)
    r = RedirectResponse("/", status_code=303)
    r.set_cookie(COOKIE, hmac.new(SECRET, b"signal-studio", hashlib.sha256).hexdigest(), max_age=60 * 60 * 24 * 90,
                 httponly=True, secure=request.headers.get("x-forwarded-proto") == "https", samesite="lax")
    return r
app.mount("/static", StaticFiles(directory=WEB), name="static")
LOCK = asyncio.Lock()
STATUS = {"busy": False, "step": "", "error": ""}


# ---------- pipeline ----------
async def refresh(auto_draft: int = 0) -> dict:
    if LOCK.locked():
        raise HTTPException(409, "A run is already in progress")
    async with LOCK:
        STATUS.update(busy=True, error="", step="Reading the news")
        run_id = store.exec_("INSERT INTO runs(started,kind) VALUES(?,?)", store.now(), "refresh")
        try:
            items, counts = await news.fetch_all()
            added = store.save_news(items)
            fresh = store.rows("SELECT * FROM news ORDER BY published DESC LIMIT 60")
            recent = [r["title"] for r in store.rows("SELECT data FROM ideas ORDER BY id DESC LIMIT 30")
                      for r in [json.loads(r["data"])]]
            STATUS["step"] = f"Matching {len(fresh)} stories to your projects"
            ideas = await asyncio.to_thread(agent.generate_ideas, fresh, recent)
            ideas.sort(key=lambda i: i["fit"], reverse=True)
            ids = []
            for i in ideas:
                n = fresh[i["news_index"]]
                i["news"] = {k: n[k] for k in ("title", "url", "source", "published")}
                ids.append(store.exec_("INSERT INTO ideas(created,news_id,project_id,data) VALUES(?,?,?,?)",
                                       store.now(), n["id"], i["project_id"], json.dumps(i)))
            for k, idea_id in enumerate(ids[:auto_draft]):
                STATUS["step"] = f"Drafting post {k + 1} of {min(auto_draft, len(ids))}"
                await _draft(idea_id)
            note = f"{added} new stories from {len(counts)} sources, {len(ideas)} ideas"
            store.exec_("UPDATE runs SET finished=?, note=? WHERE id=?", store.now(), note, run_id)
            return {"added": added, "sources": counts, "ideas": len(ideas)}
        except Exception as e:  # surface any failure in the dashboard instead of failing silently
            STATUS["error"] = str(e)
            store.exec_("UPDATE runs SET finished=?, note=? WHERE id=?", store.now(), f"failed: {e}", run_id)
            raise HTTPException(500, str(e))
        finally:
            STATUS.update(busy=False, step="")


async def _draft(idea_id: int, feedback: str | None = None, previous: dict | None = None) -> dict:
    r = store.one("SELECT * FROM ideas WHERE id=?", idea_id)
    if not r:
        raise HTTPException(404, "idea not found")
    idea = store.idea_row(r)
    n = store.one("SELECT title,url,source,summary,published FROM news WHERE id=?", r["news_id"])
    d = await asyncio.to_thread(agent.draft_post, idea, n, feedback, previous["text"] if previous else None)
    tags = " ".join("#" + t.lstrip("#").replace(" ", "") for t in d["hashtags"])
    if previous:
        store.exec_("""UPDATE drafts SET text=?,hashtags=?,first_comment=?,image_idea=?,feedback=?,status='pending',
                       version=version+1,updated=? WHERE id=?""",
                    d["post"], tags, d["first_comment"], d["image_idea"], feedback, store.now(), previous["id"])
        did = previous["id"]
    else:
        did = store.exec_("""INSERT INTO drafts(idea_id,created,updated,text,hashtags,first_comment,image_idea)
                             VALUES(?,?,?,?,?,?,?)""", idea_id, store.now(), store.now(), d["post"], tags,
                          d["first_comment"], d["image_idea"])
    store.exec_("UPDATE ideas SET status='drafted' WHERE id=?", idea_id)
    return store.one("SELECT * FROM drafts WHERE id=?", did)


async def guarded(coro):
    if LOCK.locked():
        raise HTTPException(409, "A run is already in progress")
    async with LOCK:
        STATUS.update(busy=True, error="", step="Writing with Claude")
        try:
            return await coro
        except HTTPException:
            raise
        except Exception as e:
            STATUS["error"] = str(e)
            raise HTTPException(500, str(e))
        finally:
            STATUS.update(busy=False, step="")


# ---------- API ----------
class Feedback(BaseModel):
    feedback: str = ""


class Status(BaseModel):
    status: str


class Text(BaseModel):
    text: str
    headline: str | None = None


class Notes(BaseModel):
    notes: str = ""


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


@app.get("/api/state")
def state():
    drafts = store.rows("""SELECT d.*, i.data AS idea FROM drafts d LEFT JOIN ideas i ON i.id=d.idea_id
                           ORDER BY d.updated DESC""")
    for d in drafts:
        d["idea"] = json.loads(d["idea"]) if d["idea"] else {}
    ideas = [store.idea_row(r) for r in store.rows("SELECT * FROM ideas WHERE status!='dismissed' ORDER BY id DESC LIMIT 24")]
    count = lambda s: sum(1 for d in drafts if d["status"] == s)  # noqa: E731
    return {
        "status": STATUS,
        "stats": {"stories": store.one("SELECT COUNT(*) c FROM news")["c"], "ideas": len(ideas),
                  "pending": count("pending"), "approved": count("approved"), "posted": count("posted")},
        "news": store.rows("SELECT * FROM news ORDER BY published DESC LIMIT 40"),
        "ideas": ideas, "drafts": drafts,
        "about": store.rows("SELECT * FROM about ORDER BY id DESC LIMIT 9"),
        "runs": store.rows("SELECT * FROM runs ORDER BY id DESC LIMIT 5"),
        "next_run": next_run().isoformat(timespec="minutes") if DAILY_HOUR >= 0 else None,
        "profile_name": agent.profile()["name"], "model": agent.MODEL,
        "linkedin": linkedin_state(),
    }


def linkedin_state() -> dict:
    acct = linkedin.account()
    li = agent.profile().get("linkedin") or {}
    return {"configured": linkedin.configured(),
            "account": {k: acct.get(k) for k in ("name", "picture", "connected")} if acct else None,
            "profile": {"source": li.get("source"), "imported": li.get("imported"), "headline": li.get("headline"),
                        "positions": len(li.get("positions", [])), "posts": len(li.get("past_posts", [])),
                        "skills": len(li.get("skills", []))} if li else None}


def _save_linkedin_profile(data: dict) -> dict:
    p = agent.profile()
    p["linkedin"] = data
    if data.get("headline"):
        p["headline_now"] = data["headline"]
    agent.PROFILE_PATH.write_text(json.dumps(p, indent=2))
    return linkedin_state()


@app.post("/api/linkedin/import")
async def api_li_import(request: Request):
    """Body = the LinkedIn data-export .zip (raw bytes)."""
    blob = await request.body()
    if len(blob) > 200 * 1024 * 1024:
        raise HTTPException(413, "Zip too large (max 200 MB). The Basic archive is enough.")
    try:
        return _save_linkedin_profile(linkedin.parse_export(blob))
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.put("/api/linkedin/profile")
def api_li_profile(body: dict):
    """Profile read from the user's own browser session (headline/about/positions/skills/past_posts)."""
    if not (body.get("headline") or body.get("about") or body.get("positions")):
        raise HTTPException(400, "Need at least headline, about or positions")
    body.setdefault("source", "browser")
    body["imported"] = store.now()
    return _save_linkedin_profile(body)


@app.get("/auth/linkedin")
def auth_start():
    if not linkedin.configured():
        raise HTTPException(400, "Set LINKEDIN_CLIENT_ID and LINKEDIN_CLIENT_SECRET first")
    return RedirectResponse(linkedin.auth_url())


@app.get("/auth/linkedin/callback")
async def auth_cb(code: str = "", state: str = "", error: str = "", error_description: str = ""):
    if error:
        return RedirectResponse(f"/?li_error={error_description or error}")
    try:
        await linkedin.finish_auth(code, state)
    except (ValueError, Exception) as e:  # show the reason in the dashboard
        return RedirectResponse(f"/?li_error={str(e)[:200]}")
    return RedirectResponse("/?li=connected")


@app.post("/api/linkedin/disconnect")
def api_li_disconnect():
    store.set_setting("linkedin_account", "")
    return {"ok": True}


@app.post("/api/drafts/{draft_id}/publish")
async def api_publish(draft_id: int):
    d = store.one("SELECT * FROM drafts WHERE id=?", draft_id)
    if not d:
        raise HTTPException(404, "draft not found")
    if d["status"] != "approved":
        raise HTTPException(400, "Approve the draft before posting it")
    try:
        r = await linkedin.publish(d["text"], d["hashtags"] or "", d["first_comment"])
    except ValueError as e:
        raise HTTPException(400, str(e))
    store.exec_("UPDATE drafts SET status='posted', post_url=?, updated=? WHERE id=?", r["url"], store.now(), draft_id)
    return r


@app.post("/api/refresh")
async def api_refresh():
    return await refresh(auto_draft=0)


@app.post("/api/ideas/{idea_id}/draft")
async def api_draft(idea_id: int):
    return await guarded(_draft(idea_id))


@app.post("/api/ideas/{idea_id}/dismiss")
def api_dismiss(idea_id: int):
    store.exec_("UPDATE ideas SET status='dismissed' WHERE id=?", idea_id)
    return {"ok": True}


@app.post("/api/drafts/{draft_id}/regenerate")
async def api_regen(draft_id: int, body: Feedback):
    prev = store.one("SELECT * FROM drafts WHERE id=?", draft_id)
    if not prev:
        raise HTTPException(404, "draft not found")
    return await guarded(_draft(prev["idea_id"], body.feedback or "Make it sharper.", prev))


@app.post("/api/drafts/{draft_id}/text")
def api_edit(draft_id: int, body: Text):
    store.exec_("UPDATE drafts SET text=?, updated=? WHERE id=?", body.text, store.now(), draft_id)
    return {"ok": True}


@app.post("/api/drafts/{draft_id}/status")
def api_status(draft_id: int, body: Status):
    if body.status not in {"pending", "approved", "rejected", "posted"}:
        raise HTTPException(400, "bad status")
    store.exec_("UPDATE drafts SET status=?, updated=? WHERE id=?", body.status, store.now(), draft_id)
    return {"ok": True}


@app.post("/api/about/generate")
async def api_about(body: Notes):
    async def go():
        variants = await asyncio.to_thread(agent.draft_about, body.notes or None)
        for v in variants:
            store.exec_("INSERT INTO about(created,label,headline,text,what_changed) VALUES(?,?,?,?,?)",
                        store.now(), v["label"], v["headline"], v["about"], v["what_changed"])
        return variants
    return await guarded(go())


@app.post("/api/about/{about_id}/text")
def api_about_edit(about_id: int, body: Text):
    store.exec_("UPDATE about SET text=?, headline=COALESCE(?, headline) WHERE id=?", body.text, body.headline, about_id)
    return {"ok": True}


@app.post("/api/about/{about_id}/status")
def api_about_status(about_id: int, body: Status):
    if body.status == "approved":  # only one approved About at a time
        store.exec_("UPDATE about SET status='pending' WHERE status='approved'")
    store.exec_("UPDATE about SET status=? WHERE id=?", body.status, about_id)
    return {"ok": True}


@app.get("/api/profile")
def get_profile():
    return agent.profile()


@app.put("/api/profile")
def put_profile(body: dict):
    if "projects" not in body or "name" not in body:
        raise HTTPException(400, "profile needs at least 'name' and 'projects'")
    agent.PROFILE_PATH.write_text(json.dumps(body, indent=2))
    return {"ok": True}


# ---------- daily scheduler ----------
def next_run() -> datetime:
    t = datetime.now().replace(hour=max(DAILY_HOUR, 0), minute=0, second=0, microsecond=0)
    return t if t > datetime.now() else t + timedelta(days=1)


async def daily_loop():
    while True:
        await asyncio.sleep((next_run() - datetime.now()).total_seconds())
        try:
            await refresh(auto_draft=AUTO_DRAFTS)
        except HTTPException:
            pass  # recorded in runs + STATUS
        await asyncio.sleep(60)


@app.on_event("startup")
async def startup():
    store.init()
    if DAILY_HOUR >= 0:
        asyncio.create_task(daily_loop())
