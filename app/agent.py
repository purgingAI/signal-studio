"""The LinkedIn agent: Claude reads today's news + your real projects and proposes post ideas,
drafts posts in your voice, and drafts your About section. Nothing is ever posted automatically."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from .linkedin import as_text

BASE = Path(__file__).resolve().parent
# Runs through the Claude Code CLI (`claude -p`) on your Claude subscription, so no API key is needed.
# Locally it uses your `claude` login; on a server set CLAUDE_CODE_OAUTH_TOKEN (from `claude setup-token`).
MODEL = os.environ.get("STUDIO_MODEL", "opus")
CLAUDE_BIN = os.environ.get("CLAUDE_BIN", "claude")
TIMEOUT = int(os.environ.get("STUDIO_TIMEOUT", "600"))
# The live profile lives in data/ (persistent volume) and is seeded once from the bundled app/profile.json.
PROFILE_PATH = BASE.parent / "data" / "profile.json"
if not PROFILE_PATH.exists():
    PROFILE_PATH.parent.mkdir(exist_ok=True)
    PROFILE_PATH.write_text((BASE / "profile.json").read_text())


def profile() -> dict:
    return json.loads(PROFILE_PATH.read_text())


STYLE = """How to write a LinkedIn post that works:
- The first two lines are the hook (they show before "...see more"). Make them specific and surprising, never generic.
- Short lines, generous white space, 120-250 words. One idea per post.
- Tie the news to something the author ACTUALLY built (from the profile): what they did, what broke, what they learned.
- Concrete beats clever: real numbers, real components, real mistakes. Show the system.
- End with one genuine question or takeaway that invites comments. No "Thoughts?" alone.
- No hype words (game-changer, revolutionary, delve, unleash, in today's fast-paced world), no emoji walls (0-3 emoji max),
  no em-dash chains, no hashtags inside the body.
- HONESTY RULE: never invent facts, numbers, employers, clients or results that are not in the profile. If a detail
  would help but is unknown, write it as [ADD: what to add] so the author fills it in before approving.
- Never reveal confidential company details: no client names, internal hostnames, credentials or unreleased plans."""


def _call(system: str, user: str, schema: dict, effort: str = "medium") -> dict:
    cmd = [CLAUDE_BIN, "-p", "--model", MODEL, "--effort", effort, "--output-format", "json",
           "--no-session-persistence", "--tools", "", "--system-prompt", system, "--json-schema", json.dumps(schema)]
    try:
        p = subprocess.run(cmd, input=user, capture_output=True, text=True, timeout=TIMEOUT, cwd="/tmp")
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"Claude took longer than {TIMEOUT}s; try again.")
    except FileNotFoundError:
        raise RuntimeError(f"Claude CLI not found ({CLAUDE_BIN}). Install Claude Code or set CLAUDE_BIN.")
    try:
        out = json.loads(p.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(f"Claude CLI failed: {(p.stderr or p.stdout).strip()[:300]}")
    if out.get("is_error") or out.get("structured_output") is None:
        raise RuntimeError(f"Claude CLI error: {str(out.get('result') or out.get('subtype'))[:300]}")
    return out["structured_output"]


def _obj(props: dict) -> dict:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


S = {"type": "string"}
IDEAS_SCHEMA = _obj({"ideas": {"type": "array", "items": _obj({
    "news_index": {"type": "integer"}, "project_id": S, "title": S, "hook": S, "angle": S,
    "story_beat": S, "why_now": S,
    "format": {"type": "string", "enum": ["story", "lesson", "hot-take", "how-to", "behind-the-build", "myth-vs-reality"]},
    "fit": {"type": "integer"}})}})
DRAFT_SCHEMA = _obj({"post": S, "hashtags": {"type": "array", "items": S}, "first_comment": S, "image_idea": S})
ABOUT_SCHEMA = _obj({"variants": {"type": "array", "items": _obj({"label": S, "headline": S, "about": S, "what_changed": S})}})


def generate_ideas(news: list[dict], recent_titles: list[str], n: int = 6) -> list[dict]:
    p = profile()
    news_block = "\n".join(f"[{i}] ({n_['source']}, {n_['published'][:10]}) {n_['title']} :: {n_['summary'][:220]}"
                           for i, n_ in enumerate(news))
    user = f"""AUTHOR PROFILE (the only source of truth about what they built):
{json.dumps({k: v for k, v in p.items() if k != "linkedin"}, indent=1)}

TODAY'S NEWS (index in brackets):
{news_block}

POSTS ALREADY DRAFTED RECENTLY (don't repeat these angles):
{json.dumps(recent_titles[:30] + [x["text"][:90] for x in ((p.get("linkedin") or {}).get("past_posts") or [])[:10]])}

Propose {n} LinkedIn post ideas. Each must connect ONE news item (news_index) to ONE of the author's projects
(project_id from the profile) in a way that is genuinely insightful: "X just happened; here's what I learned building Y".
Prefer news that is fresh, widely discussed, and relevant to AI agents, developer tools, fintech or engineering.
Spread ideas across different projects and formats. `fit` = 1-10, how strong and timely the pairing is
(an editorial judgement, not a probability). `hook` = the literal first line of the post."""
    out = _call("You are a sharp LinkedIn ghostwriter and tech editor for a software engineer who builds AI systems.\n" + STYLE,
                user, IDEAS_SCHEMA)
    return [i for i in out["ideas"] if 0 <= i["news_index"] < len(news)]


def _past_posts(p: dict) -> str:
    posts = (p.get("linkedin") or {}).get("past_posts") or []
    if not posts:
        return ""
    sample = "\n---\n".join(x["text"][:700] for x in posts[:5])
    return f"THE AUTHOR'S RECENT REAL POSTS (match this voice, don't repeat these topics):\n{sample}\n"


def draft_post(idea: dict, news: dict | None, feedback: str | None = None, previous: str | None = None) -> dict:
    p = profile()
    proj = next((x for x in p["projects"] if x["id"] == idea.get("project_id")), None)
    user = f"""AUTHOR: {p['name']} — {p['role']}
VOICE: {p['voice']}
AUDIENCE: {p['audience']}
{_past_posts(p)}

PROJECT TO DRAW THE STORY FROM:
{json.dumps(proj, indent=1)}

NEWS HOOK: {json.dumps(news) if news else "(none — evergreen post)"}

IDEA: {json.dumps({k: idea.get(k) for k in ("title", "hook", "angle", "story_beat", "why_now", "format")}, indent=1)}
"""
    if previous:
        user += f"\nPREVIOUS DRAFT:\n{previous}\n"
    if feedback:
        user += f"\nAUTHOR FEEDBACK ON THE PREVIOUS DRAFT (apply it):\n{feedback}\n"
    user += """
Write the post in first person. `hashtags`: 3-5 relevant tags without '#'. `first_comment`: a short comment the author
posts under their own post, carrying the news link (links in the body reduce reach). `image_idea`: one line describing a
simple visual (diagram/screenshot) that would make the post stop the scroll."""
    return _call("You ghostwrite LinkedIn posts for a software engineer who builds AI systems.\n" + STYLE, user, DRAFT_SCHEMA, "high")


def draft_about(notes: str | None = None) -> list[dict]:
    p = profile()
    li = p.get("linkedin") or {}
    current = as_text(li)
    user = f"""PROFILE:
{json.dumps({k: v for k, v in p.items() if k != "linkedin"}, indent=1)}

{('EXTRA NOTES FROM THE AUTHOR: ' + notes) if notes else ''}

CURRENT LINKEDIN PROFILE (imported from LinkedIn; headline, About, experience):
{current or "(not imported yet: write fresh from the profile above)"}

If the current profile is provided: treat it as the factual base. Keep true facts from it (employer names, titles,
years, education, achievements) and improve it: stronger opening, add the AI work from the profile, cut filler.
`what_changed`: 2-4 short bullet lines saying what you kept, what you added and what you cut versus the current
version (or "Written fresh: LinkedIn profile not imported yet." if none was provided).
"""
    user += f"""
Write 3 alternative LinkedIn "About" sections plus a matching headline for each:
1. label "Builder story": narrative, opens with a vivid line about what they build.
2. label "Recruiter-ready": crisp; what they do, impact, stack, what roles they want ([ADD: target roles]).
3. label "Short & punchy": under 900 characters.
Rules: first person; the first 2-3 lines must work alone (LinkedIn folds the rest); 1,200-2,000 characters for 1 and 2;
short paragraphs; a scannable "What I build" list using simple • bullets; end with how to reach them / what they're open to.
Headline: under 220 characters, specific (e.g. "Building AI agents for payments • GraphRAG, multi-agent systems • Java/Go/Python").
Same honesty rule: no invented numbers, employers or titles; use [ADD: ...] placeholders where needed."""
    return _call("You are an expert LinkedIn profile writer for software engineers.\n" + STYLE, user, ABOUT_SCHEMA, "high")["variants"]
