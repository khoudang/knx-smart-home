from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request
from html import unescape
from dataclasses import dataclass


class WebSearchError(RuntimeError):
    pass


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str


class DuckDuckGoInstantAnswerClient:
    def __init__(self, timeout_seconds: int = 5, cache_ttl_seconds: int = 300) -> None:
        self.timeout_seconds = timeout_seconds
        self.cache_ttl_seconds = cache_ttl_seconds
        self._cache: dict[str, tuple[float, list[SearchResult]]] = {}

    def search(self, query: str, limit: int = 3) -> list[SearchResult]:
        normalized = " ".join(str(query or "").split())
        if not normalized:
            return []
        key = normalized.lower()
        now = time.time()
        cached = self._cache.get(key)
        if cached and now - cached[0] <= self.cache_ttl_seconds:
            return cached[1][:limit]

        url = "https://api.duckduckgo.com/?" + urllib.parse.urlencode(
            {
                "q": normalized,
                "format": "json",
                "no_html": "1",
                "no_redirect": "1",
                "skip_disambig": "1",
            }
        )
        request = urllib.request.Request(url, headers={"User-Agent": "AICONTROL/1.0"})
        last_error: Exception | None = None
        for _ in range(2):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                results = _parse_results(payload)
                if not results:
                    results = self._html_search(normalized)
                if not results:
                    results = self._wikipedia_search(normalized)
                self._cache[key] = (now, results)
                return results[:limit]
            except Exception as exc:  # pragma: no cover - exact urllib errors vary by platform
                last_error = exc
        raise WebSearchError(str(last_error or "web search failed"))

    def _html_search(self, query: str) -> list[SearchResult]:
        url = "https://duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query})
        request = urllib.request.Request(url, headers={"User-Agent": "AICONTROL/1.0"})
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
            html = response.read().decode("utf-8", errors="replace")
        results: list[SearchResult] = []
        blocks = re.findall(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>.*?<a[^>]+class="result__snippet"[^>]*>(.*?)</a>', html, re.S)
        for href, title_html, snippet_html in blocks:
            title = _strip_html(title_html)
            snippet = _strip_html(snippet_html)
            url = _clean_duckduckgo_url(unescape(href))
            if title and url:
                results.append(SearchResult(title=title, url=url, snippet=snippet))
        return results

    def _wikipedia_search(self, query: str) -> list[SearchResult]:
        cleaned = _clean_question_query(query)
        languages = ("en", "vi") if cleaned.isupper() and len(cleaned) <= 8 else ("vi", "en")
        for language in languages:
            url = f"https://{language}.wikipedia.org/w/api.php?" + urllib.parse.urlencode(
                {
                    "action": "opensearch",
                    "search": cleaned,
                    "limit": "3",
                    "namespace": "0",
                    "format": "json",
                }
            )
            request = urllib.request.Request(url, headers={"User-Agent": "AICONTROL/1.0"})
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
            titles = payload[1] if len(payload) > 1 else []
            snippets = payload[2] if len(payload) > 2 else []
            urls = payload[3] if len(payload) > 3 else []
            results = [
                SearchResult(str(title), str(page_url), str(snippets[index] if index < len(snippets) else ""))
                for index, (title, page_url) in enumerate(zip(titles, urls))
                if title and page_url
            ]
            if cleaned.isupper() and len(cleaned) <= 8:
                exact = [result for result in results if result.title.upper() == cleaned]
                if exact:
                    return exact
                continue
            if results:
                return results
        return []


def _parse_results(payload: dict) -> list[SearchResult]:
    results: list[SearchResult] = []
    heading = str(payload.get("Heading") or "").strip()
    abstract = str(payload.get("AbstractText") or "").strip()
    abstract_url = str(payload.get("AbstractURL") or "").strip()
    if heading and abstract and abstract_url:
        results.append(SearchResult(heading, abstract_url, abstract))
    for topic in payload.get("RelatedTopics") or []:
        _collect_topic(topic, results)
    deduped: list[SearchResult] = []
    seen: set[str] = set()
    for result in results:
        if not result.url or result.url in seen:
            continue
        seen.add(result.url)
        deduped.append(result)
    return deduped


def _collect_topic(topic: dict, results: list[SearchResult]) -> None:
    if "Topics" in topic:
        for child in topic.get("Topics") or []:
            _collect_topic(child, results)
        return
    title = str(topic.get("Text") or "").strip()
    url = str(topic.get("FirstURL") or "").strip()
    if not title or not url:
        return
    snippet = title
    if " - " in title:
        title, snippet = title.split(" - ", 1)
    results.append(SearchResult(title=title.strip(), url=url, snippet=snippet.strip()))


def _strip_html(value: str) -> str:
    text = re.sub(r"<[^>]+>", "", value or "")
    return " ".join(unescape(text).split())


def _clean_duckduckgo_url(value: str) -> str:
    if value.startswith("//"):
        value = "https:" + value
    parsed = urllib.parse.urlparse(value)
    query = urllib.parse.parse_qs(parsed.query)
    if "uddg" in query and query["uddg"]:
        return query["uddg"][0]
    return value


def _clean_question_query(query: str) -> str:
    cleaned = re.sub(r"\b(la gi|là gì|nghia la gi|nghĩa là gì)\b", " ", query, flags=re.I)
    return " ".join(cleaned.split()) or query
