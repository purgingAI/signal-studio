"""LinkedIn integration.

1. Profile import from LinkedIn's official data export (Settings → Data privacy → Get a copy of your data).
   LinkedIn's API does not give personal apps your About/experience, so the export is the sanctioned way.
2. OAuth "Connect LinkedIn" (OpenID Connect + w_member_social) to publish approved posts to your feed.
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import secrets
import zipfile
from urllib.parse import quote, urlencode

import httpx

from . import store

CLIENT_ID = os.environ.get("LINKEDIN_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("LINKEDIN_CLIENT_SECRET", "")
PUBLIC_URL = os.environ.get("PUBLIC_URL", "http://localhost:8099").rstrip("/")
API_VERSION = os.environ.get("LINKEDIN_VERSION", "202510")  # LinkedIn-Version header (YYYYMM)
REDIRECT = f"{PUBLIC_URL}/auth/linkedin/callback"
SCOPES = "openid profile email w_member_social"


# ---------------- data-export import ----------------
def _csv(z: zipfile.ZipFile, name: str) -> list[dict]:
    """Find <name>.csv anywhere in the export (LinkedIn nests some files) and parse it."""
    match = next((n for n in z.namelist() if n.lower().endswith("/" + name.lower()) or n.lower() == name.lower()), None)
    if not match:
        return []
    text = z.read(match).decode("utf-8-sig", errors="replace")
    lines = text.splitlines()
    # Some exports start with a "Notes:" preamble before the header row.
    while lines and (not lines[0].strip() or lines[0].lower().startswith("notes")):
        lines.pop(0)
    return [{k.strip(): (v or "").strip() for k, v in r.items() if k} for r in csv.DictReader(io.StringIO("\n".join(lines)))]


def parse_export(blob: bytes) -> dict:
    try:
        z = zipfile.ZipFile(io.BytesIO(blob))
    except zipfile.BadZipFile:
        raise ValueError("That isn't a zip. Upload the .zip LinkedIn emailed you (Basic or Complete archive).")
    prof = (_csv(z, "Profile.csv") or [{}])[0]
    positions = [{"company": p.get("Company Name"), "title": p.get("Title"), "description": p.get("Description", "")[:600],
                  "location": p.get("Location"), "from": p.get("Started On"), "to": p.get("Finished On") or "Present"}
                 for p in _csv(z, "Positions.csv")]
    education = [{"school": e.get("School Name"), "degree": e.get("Degree Name"), "from": e.get("Start Date"),
                  "to": e.get("End Date")} for e in _csv(z, "Education.csv")]
    skills = [s.get("Name") for s in _csv(z, "Skills.csv") if s.get("Name")]
    certs = [c.get("Name") for c in _csv(z, "Certifications.csv") if c.get("Name")]
    shares = [s for s in _csv(z, "Shares.csv") if s.get("ShareCommentary")]
    shares.sort(key=lambda s: s.get("Date", ""), reverse=True)
    posts = [{"date": s.get("Date", "")[:10], "text": s["ShareCommentary"][:1500]} for s in shares[:15]]
    if not prof and not positions:
        raise ValueError("Couldn't find Profile.csv or Positions.csv in that zip. Is it the LinkedIn data export?")
    return {"source": "data-export", "imported": store.now(),
            "name": " ".join(x for x in (prof.get("First Name"), prof.get("Last Name")) if x),
            "headline": prof.get("Headline", ""), "about": prof.get("Summary", ""),
            "industry": prof.get("Industry", ""), "location": prof.get("Geo Location", ""),
            "positions": positions, "education": education, "skills": skills, "certifications": certs,
            "past_posts": posts}


def as_text(li: dict) -> str:
    """Readable block of the real profile for prompts."""
    if not li:
        return ""
    lines = [f"Headline: {li.get('headline', '')}", f"About:\n{li.get('about', '')}", "Experience:"]
    lines += [f"- {p['title']} @ {p['company']} ({p['from']} – {p['to']}) {p.get('location') or ''}\n  {p.get('description') or ''}"
              for p in li.get("positions", [])]
    if li.get("education"):
        lines.append("Education: " + "; ".join(f"{e['degree'] or ''} {e['school']} ({e['from']}–{e['to']})" for e in li["education"]))
    if li.get("skills"):
        lines.append("Skills: " + ", ".join(li["skills"][:40]))
    if li.get("certifications"):
        lines.append("Certifications: " + ", ".join(li["certifications"]))
    return "\n".join(lines)


# ---------------- OAuth + posting ----------------
def configured() -> bool:
    return bool(CLIENT_ID and CLIENT_SECRET)


def auth_url() -> str:
    state = secrets.token_urlsafe(24)
    store.set_setting("oauth_state", state)
    return "https://www.linkedin.com/oauth/v2/authorization?" + urlencode(
        {"response_type": "code", "client_id": CLIENT_ID, "redirect_uri": REDIRECT, "state": state, "scope": SCOPES})


async def finish_auth(code: str, state: str) -> dict:
    if not state or state != store.get_setting("oauth_state"):
        raise ValueError("Login state didn't match; start Connect LinkedIn again.")
    async with httpx.AsyncClient(timeout=20) as c:
        t = await c.post("https://www.linkedin.com/oauth/v2/accessToken", data={
            "grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT,
            "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET})
        if t.status_code != 200:
            raise ValueError(f"LinkedIn token exchange failed: {t.text[:200]}")
        tok = t.json()
        me = await c.get("https://api.linkedin.com/v2/userinfo", headers={"Authorization": f"Bearer {tok['access_token']}"})
        me.raise_for_status()
    info = me.json()
    acct = {"sub": info["sub"], "name": info.get("name"), "picture": info.get("picture"), "email": info.get("email"),
            "token": tok["access_token"], "expires_in": tok.get("expires_in"), "connected": store.now()}
    store.set_setting("linkedin_account", json.dumps(acct))
    store.set_setting("oauth_state", "")
    return acct


def account() -> dict | None:
    raw = store.get_setting("linkedin_account")
    return json.loads(raw) if raw else None


_RESERVED = re.compile(r"([\\|{}@\[\]()<>#*_~])")


def little_text(post: str, hashtags: str) -> str:
    """LinkedIn 'little text' format: reserved characters must be escaped, hashtags use a template."""
    body = _RESERVED.sub(r"\\\1", post.strip())
    tags = " ".join("{hashtag|\\#|%s}" % re.sub(r"\W", "", t) for t in hashtags.split() if re.sub(r"\W", "", t))
    return body + ("\n\n" + tags if tags else "")


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "LinkedIn-Version": API_VERSION,
            "X-Restli-Protocol-Version": "2.0.0", "Content-Type": "application/json"}


async def publish(text: str, hashtags: str, first_comment: str | None) -> dict:
    acct = account()
    if not acct:
        raise ValueError("Connect LinkedIn first.")
    author = f"urn:li:person:{acct['sub']}"
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post("https://api.linkedin.com/rest/posts", headers=_headers(acct["token"]), json={
            "author": author, "commentary": little_text(text, hashtags), "visibility": "PUBLIC",
            "distribution": {"feedDistribution": "MAIN_FEED", "targetEntities": [], "thirdPartyDistributionChannels": []},
            "lifecycleState": "PUBLISHED", "isReshareDisabledByAuthor": False})
        if r.status_code == 401:
            store.set_setting("linkedin_account", "")
            raise ValueError("LinkedIn login expired. Connect LinkedIn again.")
        if r.status_code not in (200, 201):
            raise ValueError(f"LinkedIn rejected the post ({r.status_code}): {r.text[:300]}")
        urn = r.headers.get("x-restli-id", "")
        comment_ok = None
        if first_comment and urn:
            cr = await c.post(f"https://api.linkedin.com/rest/socialActions/{quote(urn, safe='')}/comments",
                              headers=_headers(acct["token"]),
                              json={"actor": author, "object": urn, "message": {"text": first_comment}})
            comment_ok = cr.status_code in (200, 201)
    return {"urn": urn, "url": f"https://www.linkedin.com/feed/update/{urn}/" if urn else None, "comment_posted": comment_ok}
