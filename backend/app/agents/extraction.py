"""Evidence extraction agent.

Fetches the sources the crawlability agent approved and reduces them to clean
readable text. Every document keeps its URL, title and retrieval timestamp,
because a quote without a retrieval time cannot be re-checked later.

Three content types are handled: HTML, XML (full-text APIs) and **PDF**. PDF
matters disproportionately here — municipal health strategies, NCD action plans
and national statistics reports are almost always published as PDFs, so a system
that reads only HTML systematically misses the most city-specific and most
authoritative evidence available to it.

Fetches are grouped by host and serialised within a host so that a crawl-delay
is actually respected rather than merely reported.
"""

import asyncio
import io
import logging
import re
import warnings
from collections import defaultdict
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

from ..config import settings
from ..ontology import Document, Source


logger = logging.getLogger("cardio4cities.extraction")

# Full-text APIs return XML. One parser handles both well enough for text
# extraction, and the warning about it is noise rather than signal here.
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

STRIP_TAGS = ("script", "style", "noscript", "nav", "footer", "header", "form", "aside", "iframe", "svg")
MAIN_SELECTORS = ("main", "article", "[role=main]", "#content", ".content", "#main", ".main")
MIN_USEFUL_CHARS = 400

#: PDF limits. A national statistics yearbook can run to hundreds of pages and
#: tens of megabytes; neither the extractor nor the demo budget benefits from
#: reading all of it, and an unbounded download is a denial-of-service on
#: ourselves.
MAX_PDF_BYTES = 12 * 1024 * 1024
MAX_PDF_PAGES = 60


def clean_pdf(payload: bytes) -> str:
    """Extract text from a PDF. Returns an empty string for scanned image PDFs."""
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(payload))
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception:
            return ""
    pages = []
    for page in reader.pages[:MAX_PDF_PAGES]:
        try:
            pages.append(page.extract_text() or "")
        except Exception:
            continue
    return re.sub(r"\s+", " ", " ".join(pages)).strip()


def clean_html(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for element in soup(list(STRIP_TAGS)):
        element.decompose()
    container = None
    for selector in MAIN_SELECTORS:
        candidate = soup.select_one(selector)
        if candidate and len(candidate.get_text(" ", strip=True)) > MIN_USEFUL_CHARS:
            container = candidate
            break
    text = (container or soup).get_text(" ", strip=True)
    return re.sub(r"\s+", " ", text)


async def _fetch_one(client: httpx.AsyncClient, source: Source) -> tuple[Source, Document | None, str | None]:
    target = source.fetch_url or source.url
    try:
        response = await client.get(
            target,
            headers={
                "User-Agent": settings.user_agent,
                # Full-text APIs serve XML and many official reports are PDFs;
                # a narrow Accept header gets a 406 from the first and skips the second.
                "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9,application/xml;q=0.9,text/plain;q=0.8,*/*;q=0.5",
                "Accept-Language": "en",
            },
            follow_redirects=True,
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as error:
        return source, None, f"Returned HTTP {error.response.status_code}"
    except httpx.HTTPError as error:
        return source, None, f"Could not be retrieved ({type(error).__name__})"

    content_type = response.headers.get("content-type", "").lower()
    looks_like_pdf = "pdf" in content_type or response.content[:5] == b"%PDF-"

    if looks_like_pdf:
        if len(response.content) > MAX_PDF_BYTES:
            return source, None, f"PDF is {len(response.content) // (1024 * 1024)} MB, above the {MAX_PDF_BYTES // (1024 * 1024)} MB read limit"
        try:
            # pypdf is synchronous and CPU-bound; keep it off the event loop so
            # one large report cannot stall every other fetch in the round.
            text = await asyncio.to_thread(clean_pdf, response.content)
        except Exception as error:
            return source, None, f"PDF could not be parsed ({type(error).__name__})"
        source.content_type = "pdf"
        if len(text) < MIN_USEFUL_CHARS:
            return source, None, "PDF carried no extractable text layer; it is probably a scan and needs OCR"
    elif "html" in content_type or "xml" in content_type:
        text = clean_html(response.text)
        source.content_type = "html"
        if len(text) < MIN_USEFUL_CHARS:
            return source, None, "Page carried too little readable text to support a claim"
    else:
        return source, None, f"Unsupported content type '{content_type.split(';')[0] or 'unknown'}'"

    retrieved_at = datetime.now(timezone.utc).isoformat()
    source.fetched = True
    source.retrieved_at = retrieved_at
    return (
        source,
        Document(
            url=source.url,
            title=source.title,
            dimension=source.dimension,
            text=text[: settings.document_char_budget],
            retrieved_at=retrieved_at,
        ),
        None,
    )


async def extract(
    client: httpx.AsyncClient, sources: list[Source], limit: int | None = None
) -> tuple[list[Document], list[tuple[Source, str]]]:
    """Fetch approved sources. Returns (documents, [(source, failure reason)])."""
    by_host: dict[str, list[Source]] = defaultdict(list)
    for source in sources[: limit or settings.max_fetches_per_round]:
        by_host[(urlparse(source.fetch_url or source.url).netloc or "").lower()].append(source)

    documents: list[Document] = []
    failures: list[tuple[Source, str]] = []

    async def fetch(source: Source) -> None:
        try:
            _, document, failure = await asyncio.wait_for(_fetch_one(client, source), 60.0)
        except asyncio.TimeoutError:
            document, failure = None, "Retrieval exceeded its 60 second deadline"
        if document:
            documents.append(document)
        else:
            failures.append((source, failure or "Unknown extraction failure"))

    async def drain(host_sources: list[Source]) -> None:
        # A host that publishes a crawl-delay is fetched one page at a time with
        # that delay. Otherwise a few pages go in parallel: fetching strictly
        # serially made reading ~60 s per round when most sources share one host.
        if any(source.crawl_delay for source in host_sources):
            for index, source in enumerate(host_sources):
                if index:
                    await asyncio.sleep(max(source.crawl_delay or 0, 0))
                await fetch(source)
            return
        gate = asyncio.Semaphore(max(1, settings.per_host_concurrency))

        async def bounded(source: Source) -> None:
            async with gate:
                await fetch(source)

        await asyncio.gather(*[bounded(source) for source in host_sources])

    await asyncio.gather(*[drain(group) for group in by_host.values()])
    return documents, failures
