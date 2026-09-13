"""Crawlability agent.

Decides whether a source permits automated extraction *before* anything is
fetched from it. This runs as its own workflow stage rather than as a check
inside the fetcher, so that a refusal is a recorded decision with a reason the
user can read, not an invisible skip.

Semantics follow RFC 9309:

* robots.txt returns 2xx  -> obey the rules it contains
* robots.txt returns 4xx  -> no rules published, crawling is allowed
* robots.txt returns 5xx  -> service unavailable, treat as full disallow
* robots.txt unreachable  -> conservative disallow, recorded as unverified
"""

import asyncio
import logging
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx

from ..config import settings
from ..ontology import Source


logger = logging.getLogger("cardio4cities.crawlability")

UA_TOKEN = "CARDIO4CitiesResearchBot"

BLOCKED_SCHEMES_REASON = "Only http and https sources are fetched"

#: Formats this build cannot read. PDF is deliberately absent: government health
#: reports are overwhelmingly PDFs, so refusing them threw away the most
#: city-specific evidence available. robots.txt still governs whether we fetch one.
UNREADABLE_SUFFIXES = (".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".zip", ".csv")


class CrawlabilityAgent:
    """Per-run robots.txt cache and decision log."""

    def __init__(self) -> None:
        self._rules: dict[str, tuple[RobotFileParser | None, str]] = {}
        self._lock = asyncio.Lock()

    async def _rules_for(self, client: httpx.AsyncClient, origin: str) -> tuple[RobotFileParser | None, str]:
        async with self._lock:
            if origin in self._rules:
                return self._rules[origin]
        parser: RobotFileParser | None = None
        note = ""
        try:
            response = await client.get(
                urljoin(origin, "/robots.txt"),
                headers={"User-Agent": settings.user_agent},
                follow_redirects=True,
                timeout=8.0,
            )
            if response.status_code >= 500:
                note = f"robots.txt returned {response.status_code}; RFC 9309 treats this as a full disallow"
            elif response.status_code >= 400:
                parser = RobotFileParser()
                parser.parse([])
                note = "No robots.txt published, so automated access is permitted by default"
            else:
                parser = RobotFileParser()
                parser.parse(response.text.splitlines())
                note = "robots.txt fetched and parsed"
        except httpx.HTTPError as error:
            note = f"robots.txt could not be retrieved ({type(error).__name__}); access withheld to stay polite"
        async with self._lock:
            self._rules[origin] = (parser, note)
        return parser, note

    async def assess(self, client: httpx.AsyncClient, source: Source) -> Source:
        target = source.fetch_url or source.url
        parsed = urlparse(target)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            source.crawl_allowed = False
            source.crawlability_reason = BLOCKED_SCHEMES_REASON
            source.crawl_rule = "scheme"
            return source
        if parsed.path.lower().endswith(UNREADABLE_SUFFIXES):
            source.crawl_allowed = False
            source.crawlability_reason = "Office or archive format, which this build cannot read; the link is kept for manual review"
            source.crawl_rule = "content-type"
            return source

        origin = f"{parsed.scheme}://{parsed.netloc}"
        parser, note = await self._rules_for(client, origin)
        if parser is None:
            source.crawl_allowed = False
            source.crawlability_reason = note
            source.crawl_rule = "robots-unavailable"
            return source

        allowed = parser.can_fetch(UA_TOKEN, target) and parser.can_fetch("*", target)
        source.crawl_allowed = allowed
        source.crawl_rule = "robots.txt"
        source.crawlability_reason = (
            f"{note}; the path is permitted for {UA_TOKEN}" if allowed else f"{note}; the path is disallowed for automated agents"
        )
        delay = parser.crawl_delay(UA_TOKEN) or parser.crawl_delay("*")
        if delay:
            try:
                source.crawl_delay = min(float(delay), 5.0)
            except (TypeError, ValueError):
                source.crawl_delay = None
        if allowed and source.crawl_delay:
            source.crawlability_reason += f"; crawl-delay {source.crawl_delay}s honoured"
        return source

    async def assess_all(self, client: httpx.AsyncClient, sources: list[Source]) -> list[Source]:
        async def bounded(source: Source) -> Source:
            try:
                return await asyncio.wait_for(self.assess(client, source), 30.0)
            except asyncio.TimeoutError:
                # Undecided is not permitted: a source we could not assess is not crawled.
                source.crawl_allowed = False
                source.crawl_rule = "robots-timeout"
                source.crawlability_reason = "robots.txt could not be checked in time; access withheld to stay polite"
                return source

        return list(await asyncio.gather(*[bounded(source) for source in sources]))
