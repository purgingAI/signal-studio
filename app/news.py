"""Pull the latest AI / tech news from free public feeds (RSS/Atom + Hacker News). No API keys needed."""
from __future__ import annotations

import asyncio
import html
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx

FEEDS = {
    "TechCrunch AI": "https://techcrunch.com/category/artificial-intelligence/feed/",
    "The Verge AI": "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",
    "VentureBeat AI": "https://venturebeat.com/category/ai/feed/",
    "MIT Tech Review": "https://www.technologyreview.com/topic/artificial-intelligence/feed",
    "Hugging Face": "https://huggingface.co/blog/feed.xml",
    "GitHub Blog": "https://github.blog/feed/",
    "Google AI": "https://blog.google/technology/ai/rss/",
    "OpenAI": "https://openai.com/news/rss.xml",
    "Ars Technica": "https://feeds.arstechnica.com/arstechnica/technology-lab",
}
HN_URL = "https://hn.algolia.com/api/v1/search?tags=front_page&hitsPerPage=30"
UA = {"User-Agent": "linkedin-studio/1.0 (personal news reader)"}
_TAG = re.compile(r"<[^>]+>")


def _clean(s: str | None, n: int = 400) -> str:
    s = html.unescape(_TAG.sub(" ", s or ""))
    return re.sub(r"\s+", " ", s).strip()[:n]


def _date(s: str | None) -> str:
    if not s:
        return datetime.now(timezone.utc).isoformat()
    try:
        return parsedate_to_datetime(s).astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc).isoformat()
    except ValueError:
        return datetime.now(timezone.utc).isoformat()


def _parse(source: str, xml_text: str, limit: int = 12) -> list[dict]:
    root = ET.fromstring(xml_text)
    items: list[dict] = []
    for it in root.iter("item"):  # RSS 2.0
        items.append({"source": source, "title": _clean(it.findtext("title"), 200),
                      "url": (it.findtext("link") or "").strip(),
                      "summary": _clean(it.findtext("description")),
                      "published": _date(it.findtext("pubDate"))})
    ns = {"a": "http://www.w3.org/2005/Atom"}
    for e in root.iter("{http://www.w3.org/2005/Atom}entry"):  # Atom
        link = e.find("a:link[@rel='alternate']", ns)  # Element truthiness is about children, so test for None
        if link is None:
            link = e.find("a:link", ns)
        items.append({"source": source, "title": _clean(e.findtext("a:title", namespaces=ns), 200),
                      "url": link.get("href", "") if link is not None else "",
                      "summary": _clean(e.findtext("a:summary", namespaces=ns) or e.findtext("a:content", namespaces=ns)),
                      "published": _date(e.findtext("a:updated", namespaces=ns) or e.findtext("a:published", namespaces=ns))})
    return [i for i in items if i["title"] and i["url"]][:limit]


async def _feed(client: httpx.AsyncClient, source: str, url: str) -> list[dict]:
    try:
        r = await client.get(url)
        r.raise_for_status()
        return _parse(source, r.text)
    except (httpx.HTTPError, ET.ParseError):
        return []


async def _hn(client: httpx.AsyncClient) -> list[dict]:
    try:
        r = await client.get(HN_URL)
        r.raise_for_status()
    except httpx.HTTPError:
        return []
    out = []
    for h in r.json().get("hits", []):
        url = h.get("url") or f"https://news.ycombinator.com/item?id={h.get('objectID')}"
        out.append({"source": "Hacker News", "title": _clean(h.get("title"), 200), "url": url,
                    "summary": f"{h.get('points', 0)} points, {h.get('num_comments', 0)} comments on HN",
                    "published": _date(h.get("created_at"))})
    return out


async def fetch_all() -> tuple[list[dict], dict[str, int]]:
    """Return (items newest-first, per-source counts)."""
    async with httpx.AsyncClient(timeout=15, follow_redirects=True, headers=UA) as client:
        results = await asyncio.gather(_hn(client), *[_feed(client, s, u) for s, u in FEEDS.items()])
    items, seen, counts = [], set(), {}
    for batch in results:
        for i in batch:
            if i["url"] in seen:
                continue
            seen.add(i["url"])
            items.append(i)
            counts[i["source"]] = counts.get(i["source"], 0) + 1
    items.sort(key=lambda i: i["published"], reverse=True)
    return items, counts
