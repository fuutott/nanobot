"""Web tools: web_search and web_fetch."""

import html
import ipaddress
import json
import os
import random
import re
import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import httpx

from nanobot.agent.tools.base import Tool

# Shared constants
MAX_REDIRECTS = 5  # Limit redirects to prevent DoS attacks
SPOOFED_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Linux; Android 10; SM-M515F) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/87.0.4280.141 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 8.1.0; AX1082) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/83.0.4103.83 Mobile Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/98.0.4758.80 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:97.0) Gecko/20100101 Firefox/97.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/97.0.4692.71 Safari/537.36",
]


def _strip_tags(text: str) -> str:
    """Remove HTML tags and decode entities."""
    text = re.sub(r'<script[\s\S]*?</script>', '', text, flags=re.I)
    text = re.sub(r'<style[\s\S]*?</style>', '', text, flags=re.I)
    text = re.sub(r'<[^>]+>', '', text)
    return html.unescape(text).strip()


def _normalize(text: str) -> str:
    """Normalize whitespace."""
    text = re.sub(r'[ \t]+', ' ', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def _validate_url(url: str) -> tuple[bool, str]:
    """Validate URL: must be http(s) with valid domain."""
    try:
        p = urlparse(url)
        if p.scheme not in ('http', 'https'):
            return False, f"Only http/https allowed, got '{p.scheme or 'none'}'"
        if not p.netloc:
            return False, "Missing domain"
        return True, ""
    except Exception as e:
        return False, str(e)


def _is_private_or_local_ip(ip: ipaddress._BaseAddress) -> bool:
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _validate_public_target(url: str) -> tuple[bool, str]:
    """Block localhost/private IP targets to reduce SSRF risk."""
    try:
        hostname = (urlparse(url).hostname or "").strip().lower()
        if not hostname:
            return False, "Missing domain"

        if hostname in {"localhost", "localhost.localdomain"} or hostname.endswith(
            (".localhost", ".local", ".internal")
        ):
            return False, f"Blocked local hostname: {hostname}"

        try:
            addr = ipaddress.ip_address(hostname)
            if _is_private_or_local_ip(addr):
                return False, f"Blocked private IP: {hostname}"
            return True, ""
        except ValueError:
            pass

        try:
            infos = socket.getaddrinfo(hostname, None)
            for info in infos:
                resolved_ip = ipaddress.ip_address(info[4][0])
                if _is_private_or_local_ip(resolved_ip):
                    return False, f"Blocked private DNS target: {hostname} -> {resolved_ip}"
        except socket.gaierror:
            # DNS resolution issues will be handled by the actual request path.
            pass

        return True, ""
    except Exception as e:
        return False, str(e)


def _spoof_headers(url: str) -> dict[str, str]:
    return {
        "User-Agent": random.choice(SPOOFED_USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Referer": "https://duckduckgo.com/",
        "Origin": "https://duckduckgo.com",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-User": "?1",
        "Cache-Control": "max-age=0",
    }


def _extract_ddg_url(href: str) -> str:
    if href.startswith("http://") or href.startswith("https://"):
        return href
    parsed = urlparse(href)
    uddg = parse_qs(parsed.query).get("uddg", [""])[0]
    return unquote(uddg) if uddg else ""


def _compact_text(text: str) -> str:
    return re.sub(r"\s+", " ", _strip_tags(text)).strip()


def _is_valid_result_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            return False
        host = (parsed.hostname or "").lower()
        if not host:
            return False
        if host.endswith("duckduckgo.com"):
            return False
        return True
    except Exception:
        return False


class WebSearchTool(Tool):
    """Search the web using DuckDuckGo."""
    
    name = "web_search"
    description = "Search the web. Returns titles, URLs, and snippets."
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query"},
            "count": {"type": "integer", "description": "Results (1-10)", "minimum": 1, "maximum": 10}
        },
        "required": ["query"]
    }
    
    def __init__(
        self,
        api_key: str | None = None,
        max_results: int = 5,
        workspace: str | Path | None = None,
    ):
        self.api_key = api_key or os.environ.get("BRAVE_API_KEY", "")
        self.max_results = max_results
        self.workspace = Path(workspace).expanduser() if workspace else None
        self.search_log_path = (
            self.workspace / "logs" / "web_search.log.jsonl" if self.workspace else None
        )

    def _duckduckgo_params(self, query: str, n: int, page: int, safe_search: str) -> dict[str, str]:
        params: dict[str, str] = {"q": query}
        if safe_search == "strict":
            params["p"] = "-1"
        elif safe_search == "off":
            params["p"] = "1"
        if page > 1:
            params["s"] = str(max((page - 1) * n, 0))
        return params

    def _append_search_log(self, entry: dict[str, Any]) -> None:
        if not self.search_log_path:
            return

        try:
            self.search_log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.search_log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def _extract_results_regex(self, html_body: str, n: int) -> list[dict[str, str]]:
        pattern = re.compile(r'\shref="[^"]*(https?[^?&"]+)[^>]*>([^<]*)', flags=re.I)
        results: list[dict[str, str]] = []
        seen: set[str] = set()

        for raw_url, raw_label in pattern.findall(html_body):
            resolved = unquote(raw_url)
            if resolved in seen:
                continue
            if not _is_valid_result_url(resolved):
                continue

            title = _compact_text(raw_label) or resolved
            seen.add(resolved)
            results.append(
                {
                    "title": title,
                    "url": resolved,
                    "description": "",
                }
            )
            if len(results) >= n:
                break

        return results

    async def _search_duckduckgo(
        self,
        query: str,
        n: int,
        page: int,
        safe_search: str,
    ) -> list[dict[str, str]]:
        url = "https://duckduckgo.com/html/"
        params = self._duckduckgo_params(query, n, page, safe_search)
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            r = await client.get(url, params=params, headers=_spoof_headers(url))
            r.raise_for_status()

        return self._extract_results_regex(r.text, n)
    
    async def execute(
        self,
        query: str,
        count: int | None = None,
        page: int = 1,
        safeSearch: str = "moderate",
        **kwargs: Any,
    ) -> str:
        timestamp = datetime.now(timezone.utc).isoformat()
        provider = ""
        request_payload: dict[str, Any] = {}

        try:
            n = min(max(count or self.max_results, 1), 10)
            p = max(page, 1)
            safe = safeSearch if safeSearch in {"strict", "moderate", "off"} else "moderate"

            results: list[dict[str, str]] = []
            provider = "duckduckgo_html"
            request_payload = {
                "method": "GET",
                "url": "https://duckduckgo.com/html/",
                "params": self._duckduckgo_params(query, n, p, safe),
                "headers": "spoofed browser headers",
            }
            results = await self._search_duckduckgo(query, n, p, safe)

            self._append_search_log(
                {
                    "timestamp": timestamp,
                    "tool": self.name,
                    "query": query,
                    "count": n,
                    "page": p,
                    "safeSearch": safe,
                    "provider": provider,
                    "request": request_payload,
                    "response": {
                        "result_count": len(results),
                        "results": results,
                    },
                }
            )

            if not results:
                return f"No results for: {query}"
            
            lines = [f"Results for: {query}\n"]
            for i, item in enumerate(results[:n], 1):
                lines.append(f"{i}. {item.get('title', '')}\n   {item.get('url', '')}")
                if desc := item.get("description", ""):
                    lines.append(f"   {desc}")
            return "\n".join(lines)
        except Exception as e:
            self._append_search_log(
                {
                    "timestamp": timestamp,
                    "tool": self.name,
                    "query": query,
                    "count": count,
                    "page": page,
                    "safeSearch": safeSearch,
                    "provider": provider,
                    "request": request_payload,
                    "error": str(e),
                }
            )
            return f"Error: {e}"


class WebFetchTool(Tool):
    """Fetch and extract content from a URL using Readability."""
    
    name = "web_fetch"
    description = "Fetch URL and extract readable content (HTML → markdown/text)."
    parameters = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "URL to fetch"},
            "extractMode": {"type": "string", "enum": ["markdown", "text"], "default": "markdown"},
            "maxChars": {"type": "integer", "minimum": 100},
            "maxLinks": {"type": "integer", "minimum": 0, "maximum": 100, "default": 40},
            "findInPage": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional terms to prioritize excerpt around",
            },
            "startIndex": {"type": "integer", "minimum": 0, "default": 0},
        },
        "required": ["url"]
    }
    
    def __init__(self, max_chars: int = 50000):
        self.max_chars = max_chars
    
    async def _safe_get(self, url: str) -> httpx.Response:
        current_url = url
        async with httpx.AsyncClient(timeout=30.0) as client:
            for _ in range(MAX_REDIRECTS + 1):
                is_valid, error_msg = _validate_public_target(current_url)
                if not is_valid:
                    raise ValueError(error_msg)

                r = await client.get(
                    current_url,
                    headers=_spoof_headers(current_url),
                    follow_redirects=False,
                )

                if r.status_code in {301, 302, 303, 307, 308} and "location" in r.headers:
                    current_url = urljoin(str(r.url), r.headers["location"])
                    continue

                r.raise_for_status()
                return r

        raise ValueError(f"Too many redirects (>{MAX_REDIRECTS})")

    def _extract_links(
        self,
        body_html: str,
        page_url: str,
        max_links: int,
        search_terms: list[str] | None,
    ) -> list[list[str]]:
        links: list[tuple[str, str, float]] = []
        pattern = re.compile(r'<a\s+[^>]*?href=["\']([^"\']+)["\'][^>]*>([\s\S]*?)</a>', flags=re.I)
        for index, (raw_href, raw_label) in enumerate(pattern.findall(body_html)):
            href = urljoin(page_url, raw_href)
            if not href.startswith(("http://", "https://")):
                continue
            label = _compact_text(raw_label)
            if not label and not href:
                continue

            ratio = 1 / min(1, max(len(re.findall(r"\d", href)), 1))
            score = ratio * (100 - (len(label) + len(href) + (20 * index / max(1, len(body_html)))))
            score += (1 - ratio) * max(1, len(label.split()))
            for term in search_terms or []:
                if term and term.lower() in label.lower():
                    score += 1000

            links.append((label, href, score))

        links.sort(key=lambda x: x[2], reverse=True)
        unique: list[list[str]] = []
        seen: set[str] = set()
        for label, href, _ in links:
            if href in seen:
                continue
            seen.add(href)
            unique.append([label, href])
            if len(unique) >= max_links:
                break
        return unique

    def _extract_term_snippets(self, content: str, terms: list[str], max_chars: int) -> str:
        if not terms or max_chars <= 0 or max_chars >= len(content):
            return content[:max_chars] if max_chars > 0 else content

        pad = max(30, max_chars // max(2, len(terms) * 2))
        snippets: list[tuple[int, int]] = []
        lowered = content.lower()

        for term in terms:
            term = term.strip()
            if not term:
                continue
            idx = lowered.find(term.lower())
            if idx >= 0:
                start = max(0, idx - pad)
                end = min(len(content), idx + len(term) + pad)
                snippets.append((start, end))

        if not snippets:
            return content[:max_chars]

        snippets.sort()
        merged: list[tuple[int, int]] = [snippets[0]]
        for start, end in snippets[1:]:
            last_start, last_end = merged[-1]
            if start <= last_end:
                merged[-1] = (last_start, max(last_end, end))
            else:
                merged.append((start, end))

        out = " ... ".join(content[s:e] for s, e in merged)
        return out[:max_chars]

    async def execute(
        self,
        url: str,
        extractMode: str = "markdown",
        maxChars: int | None = None,
        maxLinks: int = 40,
        findInPage: list[str] | None = None,
        startIndex: int = 0,
        **kwargs: Any,
    ) -> str:
        from readability import Document

        max_chars = maxChars or self.max_chars

        # Validate URL before fetching
        is_valid, error_msg = _validate_url(url)
        if not is_valid:
            return json.dumps({"error": f"URL validation failed: {error_msg}", "url": url})
        is_valid, error_msg = _validate_public_target(url)
        if not is_valid:
            return json.dumps({"error": f"URL blocked: {error_msg}", "url": url})

        try:
            r = await self._safe_get(url)
            
            ctype = r.headers.get("content-type", "")
            title = ""
            h1 = ""
            h2 = ""
            h3 = ""
            links: list[list[str]] = []
            
            # JSON
            if "application/json" in ctype:
                text, extractor = json.dumps(r.json(), indent=2), "json"
            # HTML
            elif "text/html" in ctype or r.text[:256].lower().startswith(("<!doctype", "<html")):
                doc = Document(r.text)
                body_match = re.search(r"<body[^>]*>([\s\S]*?)</body>", r.text, flags=re.I)
                body_html = body_match.group(1) if body_match else r.text
                summary_html = doc.summary() or ""
                body_text = _normalize(_strip_tags(body_html))
                summary_text = _normalize(_strip_tags(summary_html))

                title = _compact_text(doc.title() or "")
                h1_match = re.search(r"<h1[^>]*>([\s\S]*?)</h1>", body_html, flags=re.I)
                h2_match = re.search(r"<h2[^>]*>([\s\S]*?)</h2>", body_html, flags=re.I)
                h3_match = re.search(r"<h3[^>]*>([\s\S]*?)</h3>", body_html, flags=re.I)
                h1 = _compact_text(h1_match.group(1)) if h1_match else ""
                h2 = _compact_text(h2_match.group(1)) if h2_match else ""
                h3 = _compact_text(h3_match.group(1)) if h3_match else ""

                if extractMode == "markdown":
                    extract_html = summary_html if len(summary_text) > 120 else body_html
                    text = self._to_markdown(extract_html)
                    extractor = "readability"
                    if len(text) < 200 and len(body_text) > (len(text) * 2):
                        text = body_text
                        extractor = "body-text-fallback"
                else:
                    # Prefer readability when it yields a substantial extraction,
                    # otherwise fall back to full body text for dynamic/news pages.
                    if len(summary_text) >= 200 and len(summary_text) >= int(0.2 * max(1, len(body_text))):
                        text = summary_text
                        extractor = "readability"
                    else:
                        text = body_text
                        extractor = "body"

                links = self._extract_links(body_html, str(r.url), max(maxLinks, 0), findInPage)

                if title and extractMode == "markdown":
                    text = f"# {title}\n\n{text}"
            else:
                text, extractor = r.text, "raw"

            source_text = text
            if findInPage:
                text = self._extract_term_snippets(source_text, findInPage, max_chars)
                truncated = max_chars > 0 and len(text) < len(source_text)
            else:
                start = max(startIndex, 0)
                if max_chars > 0:
                    end = start + max_chars
                    truncated = end < len(source_text)
                    text = source_text[start:end]
                else:
                    text = source_text[start:]
                    truncated = False
            
            payload: dict[str, Any] = {
                "url": url,
                "finalUrl": str(r.url),
                "status": r.status_code,
                "extractor": extractor,
                "truncated": truncated,
                "length": len(text),
                "text": text,
                "content": text,
                "title": title,
                "h1": h1,
                "h2": h2,
                "h3": h3,
            }
            if links:
                payload["links"] = links
            return json.dumps(payload)
        except Exception as e:
            return json.dumps({"error": str(e), "url": url})
    
    def _to_markdown(self, html: str) -> str:
        """Convert HTML to markdown."""
        # Convert links, headings, lists before stripping tags
        text = re.sub(r'<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*>([\s\S]*?)</a>',
                      lambda m: f'[{_strip_tags(m[2])}]({m[1]})', html, flags=re.I)
        text = re.sub(r'<h([1-6])[^>]*>([\s\S]*?)</h\1>',
                      lambda m: f'\n{"#" * int(m[1])} {_strip_tags(m[2])}\n', text, flags=re.I)
        text = re.sub(r'<li[^>]*>([\s\S]*?)</li>', lambda m: f'\n- {_strip_tags(m[1])}', text, flags=re.I)
        text = re.sub(r'</(p|div|section|article)>', '\n\n', text, flags=re.I)
        text = re.sub(r'<(br|hr)\s*/?>', '\n', text, flags=re.I)
        return _normalize(_strip_tags(text))
