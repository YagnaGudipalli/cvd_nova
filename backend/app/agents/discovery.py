"""Source discovery agent.

Executes the planned queries against public sources at request time. Nothing
about any city is stored here: a city the system has never seen behaves exactly
like one it has.

**Why APIs rather than a scraped search engine.** The obvious approach — scrape
a search engine's HTML — fails in practice and fails badly in a demonstration:
the major engines now serve captchas or query-insensitive pages to non-browser
clients. It also sits awkwardly beside a system whose second stage exists to
respect automated-access rules. So discovery runs against providers that publish
an API and permit programmatic use:

* **OpenAlex** and **Europe PMC** — peer-reviewed literature, keyless. These are
  where genuinely city-specific health evidence tends to live.
* **Wikipedia** — keyless context and onward links to official bodies.
* **Tavily / Brave / Serper** — optional, keyed. General web search covering
  government, ministry and policy pages. Configure one of these for the widest
  coverage; the system runs without them at reduced breadth and says so.

Every provider is independently failure-tolerant: one being down or rate-limited
degrades breadth, never the run.
"""

import asyncio
import logging
import os
import re
from typing import Any
from urllib.parse import urlparse

import httpx

from ..config import settings
from ..ontology import PlannedQuery, Source


logger = logging.getLogger("cardio4cities.discovery")

AUTHORITY_PATTERNS = (
    (r"\.gov(\.[a-z]{2})?$|\.gov\.|\.gob\.|\.gouv\.|\.go\.[a-z]{2}$|\.go\.[a-z]{2}\.", 5.0, "government"),
    (r"who\.int|paho\.org|worldbank\.org|un\.org|unicef\.org|europa\.eu|oecd\.org", 5.0, "multilateral"),
    (r"ncbi\.nlm\.nih\.gov|europepmc\.org|thelancet\.com|bmj\.com|nature\.com|biomedcentral\.com|springer|sciencedirect|plos\.org|frontiersin\.org|doi\.org|\.edu$|\.edu\.|\.ac\.[a-z]{2}$", 4.0, "academic"),
    (r"\.org$|\.org\.", 2.0, "organisation"),
    (r"wikipedia\.org", 1.5, "encyclopaedia"),
)

JUNK_HOSTS = (
    "pinterest.", "facebook.com", "x.com", "twitter.com", "instagram.com", "youtube.com",
    "tiktok.com", "reddit.com", "tripadvisor.", "booking.com", "expedia.",
)


def authority(url: str) -> tuple[float, str]:
    host = (urlparse(url).netloc or "").lower()
    for pattern, score, label in AUTHORITY_PATTERNS:
        if re.search(pattern, host):
            return score, label
    return 1.0, "general"


def _mentions(text: str, city: str) -> bool:
    return city.lower() in (text or "").lower()


#: Publisher hosts that reliably refuse automated clients. They stay in the
#: source list — a City Lead can open them by hand — but the extractor spends
#: its budget on sources it can actually read.
PAYWALLED_HOSTS = (
    "thelancet.com", "sciencedirect.com", "elsevier", "wiley.com", "tandfonline.com",
    "jamanetwork.com", "nejm.org", "springer.com", "sagepub.com", "oup.com", "doi.org",
)


def fetch_priority(source: Source) -> float:
    """Rank sources by how likely they are to yield readable evidence."""
    score = authority(source.url)[0]
    if source.fetch_url:
        score += 5.0  # a sanctioned full-text API
    host = (urlparse(source.url).netloc or "").lower()
    if any(blocked in host for blocked in PAYWALLED_HOSTS):
        score -= 4.0
    return score


# --------------------------------------------------------------------------- #
# Providers. Each returns a list of {title, url, snippet} and never raises.
# --------------------------------------------------------------------------- #

async def _openalex(client: httpx.AsyncClient, query: PlannedQuery, city: str) -> list[dict[str, str]]:
    """Scholarly works. Prefers open-access landing pages, which are crawlable."""
    response = await client.get(
        "https://api.openalex.org/works",
        params={"search": query.query, "per-page": 8, "mailto": "cardio4cities-research@example.org"},
    )
    response.raise_for_status()
    results = []
    for work in response.json().get("results", []):
        title = work.get("title") or ""
        location = work.get("primary_location") or {}
        open_access = work.get("open_access") or {}
        url = open_access.get("oa_url") or location.get("landing_page_url")
        if not url or not title:
            continue
        source_name = ((location.get("source") or {}) or {}).get("display_name") or "Scholarly work"
        year = work.get("publication_year")
        results.append(
            {
                "title": f"{title[:150]} ({source_name}, {year})" if year else title[:180],
                "url": url,
                "snippet": f"Peer-reviewed work indexed by OpenAlex. Cited by {work.get('cited_by_count', 0)}.",
                "city_specific": _mentions(title, city),
            }
        )
    # A study naming the city in its title is far more useful than a global review.
    results.sort(key=lambda item: item.pop("city_specific", False), reverse=True)
    return results


async def _europepmc(client: httpx.AsyncClient, query: PlannedQuery, city: str) -> list[dict[str, str]]:
    """Biomedical literature, with open-access full text where available."""
    response = await client.get(
        "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
        params={"query": f'"{city}" AND ({query.query})', "format": "json", "pageSize": 8, "resultType": "core"},
    )
    response.raise_for_status()
    results = []
    for item in response.json().get("resultList", {}).get("result", []):
        title = item.get("title") or ""
        if not title:
            continue
        fetch_url = None
        if item.get("pmcid"):
            url = f"https://europepmc.org/article/PMC/{item['pmcid']}"
            if item.get("isOpenAccess") == "Y":
                # Europe PMC publishes open-access full text through its REST API.
                # Using it is both the sanctioned route and the one that works;
                # the article page itself refuses automated clients.
                fetch_url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/{item['pmcid']}/fullTextXML"
        elif item.get("doi"):
            url = f"https://doi.org/{item['doi']}"
        else:
            continue
        journal = (item.get("journalInfo") or {}).get("journal", {}).get("title", "")
        results.append(
            {
                "title": f"{title[:150]} ({journal}, {item.get('pubYear', '')})".strip(),
                "url": url,
                "fetch_url": fetch_url,
                "snippet": (item.get("abstractText") or "")[:400],
            }
        )
    return results


async def _wikipedia(client: httpx.AsyncClient, query: PlannedQuery, city: str) -> list[dict[str, str]]:
    """Context and onward links to official bodies."""
    response = await client.get(
        "https://en.wikipedia.org/w/api.php",
        params={"action": "query", "list": "search", "srsearch": query.query, "format": "json", "srlimit": 5},
    )
    response.raise_for_status()
    results = []
    for item in response.json().get("query", {}).get("search", []):
        title = item.get("title", "")
        snippet = re.sub(r"<[^>]+>", "", item.get("snippet", ""))
        # Skip biography-shaped hits; this system does not report on individuals.
        if not (_mentions(title, city) or _mentions(snippet, city)):
            continue
        results.append(
            {
                "title": f"{title} (Wikipedia)",
                "url": f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}",
                "snippet": snippet[:300],
            }
        )
    return results[:2]


async def _tavily(client: httpx.AsyncClient, query: PlannedQuery, city: str) -> list[dict[str, str]]:
    key = os.getenv("TAVILY_API_KEY")
    if not key:
        return []
    response = await client.post(
        "https://api.tavily.com/search",
        json={"api_key": key, "query": query.query, "max_results": 6, "search_depth": "basic"},
    )
    response.raise_for_status()
    return [
        {"title": item.get("title", "")[:180], "url": item.get("url", ""), "snippet": (item.get("content") or "")[:400]}
        for item in response.json().get("results", [])
        if item.get("url")
    ]


async def _brave(client: httpx.AsyncClient, query: PlannedQuery, city: str) -> list[dict[str, str]]:
    key = os.getenv("BRAVE_API_KEY")
    if not key:
        return []
    response = await client.get(
        "https://api.search.brave.com/res/v1/web/search",
        params={"q": query.query, "count": 8},
        headers={"X-Subscription-Token": key, "Accept": "application/json"},
    )
    response.raise_for_status()
    return [
        {"title": item.get("title", "")[:180], "url": item.get("url", ""), "snippet": (item.get("description") or "")[:400]}
        for item in response.json().get("web", {}).get("results", [])
        if item.get("url")
    ]


async def _serper(client: httpx.AsyncClient, query: PlannedQuery, city: str) -> list[dict[str, str]]:
    key = os.getenv("SERPER_API_KEY")
    if not key:
        return []
    response = await client.post(
        "https://google.serper.dev/search",
        json={"q": query.query, "num": 8},
        headers={"X-API-KEY": key, "Content-Type": "application/json"},
    )
    response.raise_for_status()
    return [
        {"title": item.get("title", "")[:180], "url": item.get("link", ""), "snippet": (item.get("snippet") or "")[:400]}
        for item in response.json().get("organic", [])
        if item.get("link")
    ]


PROVIDERS = (
    ("tavily", _tavily, True),
    ("brave", _brave, True),
    ("serper", _serper, True),
    ("openalex", _openalex, False),
    ("europepmc", _europepmc, False),
    ("wikipedia", _wikipedia, False),
)


def configured_providers() -> list[dict[str, Any]]:
    """Report which discovery providers this deployment can use."""
    keys = {"tavily": "TAVILY_API_KEY", "brave": "BRAVE_API_KEY", "serper": "SERPER_API_KEY"}
    return [
        {
            "name": name,
            "keyed": keyed,
            "available": bool(os.getenv(keys[name])) if keyed else True,
            "role": "General web search including government and policy pages" if keyed else {
                "openalex": "Scholarly works, open-access landing pages",
                "europepmc": "Biomedical literature and open-access full text",
                "wikipedia": "Background context and links to official bodies",
            }[name],
        }
        for name, _, keyed in PROVIDERS
    ]


# --------------------------------------------------------------------------- #

#: Public APIs rate-limit by client, not by query. One semaphore per provider
#: keeps each within a polite concurrency while providers still run in parallel
#: with one another.
PROVIDER_CONCURRENCY = 2

#: Europe PMC in particular returns an intermittent 404 under load for queries
#: that succeed on a retry. Upstream flakiness should cost breadth at worst, so
#: each provider call gets one more attempt before it is written off as a gap.
RETRY_STATUSES = {404, 429, 500, 502, 503, 504}
CALL_DEADLINE_SECONDS = 60.0


async def _with_retry(handler, client: httpx.AsyncClient, query: PlannedQuery, city: str) -> list[dict[str, str]]:
    """Retry transient upstream errors and dropped connections.

    A momentary loss of connectivity (hotspot, conference Wi-Fi) used to fail
    every provider at once, leaving a run with zero sources. Backing off and
    retrying rides out a blip of a few seconds.
    """
    for attempt in range(3):
        try:
            return await handler(client, query, city)
        except httpx.HTTPStatusError as error:
            if attempt < 2 and error.response.status_code in RETRY_STATUSES:
                await asyncio.sleep(1.5 * (attempt + 1))
                continue
            raise
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout, httpx.RemoteProtocolError):
            if attempt < 2:
                await asyncio.sleep(3.0 * (attempt + 1))
                continue
            raise
    return []


async def discover(
    client: httpx.AsyncClient, queries: list[PlannedQuery], city: str, results_per_query: int | None = None
) -> tuple[list[Source], list[str]]:
    """Run every planned query against every available provider."""
    gates = {name: asyncio.Semaphore(PROVIDER_CONCURRENCY) for name, _, _ in PROVIDERS}

    async def run(name: str, handler, query: PlannedQuery) -> tuple[str, PlannedQuery, list[dict[str, str]]]:
        async with gates[name]:
            try:
                # A hard deadline on top of the HTTP timeouts. A connection left
                # pinned to a vanished network address (switching Wi-Fi mid-run)
                # can wait indefinitely without tripping a read timeout.
                return name, query, await asyncio.wait_for(_with_retry(handler, client, query, city), CALL_DEADLINE_SECONDS)
            except Exception as error:
                logger.warning("discovery_provider_failed provider=%s type=%s", name, type(error).__name__)
                return name, query, []

    tasks = [run(name, handler, query) for query in queries for name, handler, _ in PROVIDERS]
    outcomes = await asyncio.gather(*tasks)

    seen: dict[str, Source] = {}
    per_provider: dict[str, int] = {}
    for name, query, results in outcomes:
        per_provider[name] = per_provider.get(name, 0) + len(results)
        for item in results[: results_per_query or settings.results_per_query]:
            url = (item.get("url") or "").split("#")[0]
            host = (urlparse(url).netloc or "").lower()
            if not url.startswith("http") or any(junk in host for junk in JUNK_HOSTS) or url in seen:
                continue
            seen[url] = Source(
                url=url,
                title=(item.get("title") or url)[:220],
                snippet=(item.get("snippet") or "")[:400],
                fetch_url=item.get("fetch_url"),
                dimension=query.dimension,
                discovered_via=f"{name}: {query.query}",
            )

    notes: list[str] = []
    for name, _, keyed in PROVIDERS:
        if per_provider.get(name):
            continue
        if keyed and not any(provider["available"] for provider in configured_providers() if provider["name"] == name):
            continue
        notes.append(f"Discovery provider '{name}' returned nothing for this city, so its perspective is missing from this brief.")
    if not any(provider["available"] and provider["keyed"] for provider in configured_providers()):
        notes.append(
            "No general web-search provider is configured (TAVILY_API_KEY, BRAVE_API_KEY or SERPER_API_KEY), "
            "so government and municipal policy pages are under-represented in this run."
        )

    ranked = sorted(seen.values(), key=lambda source: authority(source.url)[0], reverse=True)
    return ranked, notes
