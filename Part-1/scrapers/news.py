"""News + business-blog article scraper — dynamic URL discovery via Google News RSS.

For each query in config/sources.yaml → news_queries:
  1. Hit Google News RSS to discover real article URLs about that topic.
  2. Filter to preferred domains (Indian business media + adjacent).
  3. For each URL, fetch → extract article paragraphs + comment blocks.

Google News RSS is free, no API key, and returns real URLs. The `google_news_url`
returned by GN is a redirect wrapper — we follow it to the actual publisher URL.

Usage:
    python -m scrapers.news --per-query 15 --min-para-chars 120
"""
from __future__ import annotations

import argparse
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from urllib.parse import quote_plus, urlparse

import requests
from bs4 import BeautifulSoup

from pipeline.settings import SOURCES
from pipeline.storage import Snippet, upsert_snippets

log = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-IN,en;q=0.9",
}

_NOISE_PATTERNS = [
    re.compile(r"^(subscribe|newsletter|advertisement|sign up|follow us)", re.I),
    re.compile(r"copyright|all rights reserved", re.I),
    re.compile(r"^(inc42|yourstory|economic times|livemint|moneycontrol)$", re.I),
    re.compile(r"^(read more|click here|share this|read also|also read)", re.I),
]

_COMMENT_SELECTORS = [
    ".comment-body", ".comment-text", ".comment",
    "[itemprop=commentText]",
    ".fb_comment_text", ".disqus-comment",
    ".c-comment__text", ".comment-content",
]


def _clean(t: str) -> str:
    return re.sub(r"\s+", " ", t or "").strip()


def _is_noise(t: str) -> bool:
    if not t or len(t) < 40:
        return True
    for p in _NOISE_PATTERNS:
        if p.search(t):
            return True
    return False


def _extract_paragraphs(soup: BeautifulSoup) -> list[str]:
    candidates = []
    for selector in ("article", ".article-body", ".post-content", ".content-body",
                     ".story-content", "#content-body", ".entry-content",
                     ".gh-content", ".article__body", "main"):
        for node in soup.select(selector):
            candidates.append(node)
    if not candidates:
        candidates = [soup.body] if soup.body else []
    paras, seen = [], set()
    for node in candidates:
        for p in node.find_all(["p", "li", "blockquote"]):
            txt = _clean(p.get_text(" "))
            if _is_noise(txt) or txt in seen:
                continue
            seen.add(txt)
            paras.append(txt)
    return paras


def _extract_comments(soup: BeautifulSoup) -> list[str]:
    out = []
    for sel in _COMMENT_SELECTORS:
        for node in soup.select(sel):
            txt = _clean(node.get_text(" "))
            if not _is_noise(txt):
                out.append(txt)
    return out


def _publisher(url: str) -> str:
    host = urlparse(url).hostname or ""
    return host.replace("www.", "")


def _fetch(url: str, timeout: int = 20) -> str | None:
    try:
        r = requests.get(url, headers=_HEADERS, timeout=timeout, allow_redirects=True)
        if r.status_code != 200:
            log.warning("news[%s]: HTTP %s", url, r.status_code)
            return None
        return r.text
    except Exception as e:
        log.warning("news[%s]: %s", url, str(e)[:200])
        return None


def _google_news_urls(query: str, per_query: int) -> list[dict]:
    """Return list of {url, title, source_hint, date} from Google News RSS."""
    rss_url = (
        f"https://news.google.com/rss/search?q={quote_plus(query)}"
        "&hl=en-IN&gl=IN&ceid=IN:en"
    )
    xml = _fetch(rss_url, timeout=15)
    if not xml:
        return []
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as e:
        log.warning("news[%s]: RSS parse failed: %s", query, e)
        return []
    out = []
    for item in root.iter("item"):
        url_el = item.find("link")
        title_el = item.find("title")
        source_el = item.find("source")
        pub_el = item.find("pubDate")
        if url_el is None or not url_el.text:
            continue
        out.append({
            "gnews_url": url_el.text.strip(),
            "title": (title_el.text if title_el is not None else "") or "",
            "source_hint": (source_el.text if source_el is not None else "") or "",
            "pub_date": (pub_el.text if pub_el is not None else "") or "",
        })
        if len(out) >= per_query:
            break
    log.info("news[query=%s]: RSS returned %d URLs", query[:40], len(out))
    return out


def _resolve_google_news_redirect(gnews_url: str) -> str | None:
    """Google News wraps real URLs behind a JS redirect (2024+). Decode locally."""
    try:
        from googlenewsdecoder import gnewsdecoder
        result = gnewsdecoder(gnews_url, interval=1)
        if isinstance(result, dict) and result.get("status") and result.get("decoded_url"):
            return result["decoded_url"]
    except Exception as e:
        log.debug("news decode failed [%s]: %s", gnews_url, str(e)[:120])
    return None


def _domain_ok(url: str, preferred: set) -> bool:
    host = urlparse(url).hostname or ""
    host = host.replace("www.", "")
    if not preferred:
        return True
    return any(host.endswith(p) for p in preferred)


def _url_to_snippets(url: str, min_para_chars: int, title_hint: str, pub_date: str) -> list[Snippet]:
    html = _fetch(url)
    if not html:
        return []
    soup = BeautifulSoup(html, "lxml")
    publisher = _publisher(url)
    now = datetime.now(timezone.utc)
    snippets: list[Snippet] = []

    for para in _extract_paragraphs(soup):
        if len(para) < min_para_chars:
            continue
        snippets.append(
            Snippet(
                source="news_article",
                brand="zepto",
                text=para,
                source_url=url,
                author=publisher,
                posted_at=now,
                lang="en",
                raw_metadata={
                    "publisher": publisher,
                    "kind": "article_paragraph",
                    "article_title": title_hint,
                    "gnews_pub_date": pub_date,
                },
            )
        )
    for comment in _extract_comments(soup):
        if len(comment) < 30:
            continue
        snippets.append(
            Snippet(
                source="news_comment",
                brand="zepto",
                text=comment,
                source_url=url,
                author=publisher,
                posted_at=now,
                lang="en",
                raw_metadata={"publisher": publisher, "kind": "reader_comment"},
            )
        )
    if snippets:
        log.info("news[%s]: %d snippets from %s", publisher, len(snippets), title_hint[:60])
    return snippets


def run(per_query: int = 15, min_para_chars: int = 120) -> int:
    queries = SOURCES.get("news_queries") or []
    preferred = set(SOURCES.get("news_preferred_domains") or [])
    if not queries:
        log.warning("news: no queries in sources.yaml")
        return 0

    seen_urls: set[str] = set()
    all_snippets: list[Snippet] = []

    for q in queries:
        for candidate in _google_news_urls(q, per_query):
            real_url = _resolve_google_news_redirect(candidate["gnews_url"])
            if not real_url or real_url in seen_urls:
                continue
            if not _domain_ok(real_url, preferred):
                log.debug("news: skipping non-preferred domain %s", _publisher(real_url))
                continue
            seen_urls.add(real_url)
            all_snippets.extend(_url_to_snippets(
                real_url, min_para_chars, candidate["title"], candidate["pub_date"]
            ))

    n = upsert_snippets(all_snippets)
    log.info("news: total captured=%d saved=%d (unique urls=%d)",
             len(all_snippets), n, len(seen_urls))
    return n


def _cli() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-query", type=int, default=15)
    ap.add_argument("--min-para-chars", type=int, default=120)
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args()
    logging.basicConfig(level=args.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    run(args.per_query, args.min_para_chars)


if __name__ == "__main__":
    _cli()
