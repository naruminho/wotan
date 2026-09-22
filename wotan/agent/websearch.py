"""Pluggable web search (SearXNG / Brave / Tavily / generic HTTP) and web fetch
(HTML -> clean markdown). Can be turned off entirely via config. All results are
untrusted data for the agent (prompt-injection defenses apply)."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

import httpx

from ..config import SearchConfig, WebFetchConfig, resolve_secret
from ..logging_setup import get_logger
from ..providers.generic_http import extract_path

log = get_logger("wotan.websearch", component="web")


async def search_web(cfg: SearchConfig, query: str) -> list[dict[str, Any]]:
    if not cfg.enabled or not cfg.base_url:
        return []
    url = cfg.base_url.rstrip("/") + "/" + cfg.endpoint.lstrip("/")
    headers: dict[str, str] = {}
    if cfg.api_key_ref:
        key = resolve_secret(cfg.api_key_ref)
        if cfg.provider == "brave":
            headers["X-Subscription-Token"] = key
        elif cfg.provider == "tavily":
            headers["Authorization"] = f"Bearer {key}"
        else:
            headers["X-API-Key"] = key
    params = {cfg.query_param: query, **cfg.extra_params}
    body = dict(params)
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            if cfg.method.upper() == "GET":
                resp = await client.get(url, params=params, headers=headers)
            else:
                resp = await client.post(url, json=body, headers=headers)
            if resp.status_code >= 400:
                log.warning("search failed", extra={"data": {"status": resp.status_code}})
                return []
            data = resp.json()
        items = extract_path(data, cfg.results_path) or []
        results = []
        for it in items[:10]:
            if not isinstance(it, dict):
                continue
            results.append({
                "title": extract_path(it, cfg.title_path) or it.get("title", ""),
                "url": extract_path(it, cfg.url_path) or it.get("url", ""),
                "snippet": (extract_path(it, cfg.snippet_path) or it.get("content", "") or "")[:300],
            })
        return results
    except Exception as exc:
        log.warning("search error", extra={"data": {"error": str(exc)}})
        return []


_TAG = re.compile(r"<(script|style|nav|footer|header|aside)[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE)
_HTML = re.compile(r"<[^>]+>")
_MD_LINK = re.compile(r'<a\s+href="([^"]+)"[^>]*>(.*?)</a>', re.DOTALL | re.IGNORECASE)
_HEADING = re.compile(r"<h([1-3])[^>]*>(.*?)</h\1>", re.DOTALL | re.IGNORECASE)
_PARA = re.compile(r"<p[^>]*>(.*?)</p>", re.DOTALL | re.IGNORECASE)
_PRE = re.compile(r"<pre[^>]*>(.*?)</pre>", re.DOTALL | re.IGNORECASE)


def html_to_markdown(html: str) -> str:
    """Dependency-free HTML -> clean markdown."""
    text = _TAG.sub("", html)
    text = _MD_LINK.sub(lambda m: f"[{_HTML.sub('', m.group(2)).strip()}]({m.group(1)})", text)
    text = _HEADING.sub(lambda m: "\n" + "#" * int(m.group(1)) + " " + _HTML.sub("", m.group(2)).strip() + "\n", text)
    text = _PRE.sub(lambda m: "\n```\n" + _HTML.sub("", m.group(1)).strip() + "\n```\n", text)
    text = _PARA.sub(lambda m: "\n" + _HTML.sub("", m.group(1)).strip() + "\n", text)
    text = _HTML.sub("", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


async def fetch_markdown(cfg: WebFetchConfig, url: str) -> str:
    if not cfg.enabled:
        raise PermissionError("web fetch is disabled in the configuration")
    host = urlparse(url).hostname or ""
    if cfg.allow_domains:
        ok = any(host == d or host.endswith("." + d) for d in cfg.allow_domains)
        if not ok:
            raise PermissionError(f"domain {host!r} is not in web.allow_domains")
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        resp = await client.get(url, headers={"User-Agent": "Wotan/0.1 (local IDE assistant)"})
        resp.raise_for_status()
        ctype = resp.headers.get("content-type", "")
        if "html" in ctype or resp.text.lstrip().startswith("<"):
            return html_to_markdown(resp.text)
        return resp.text
