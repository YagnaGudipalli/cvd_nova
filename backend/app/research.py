import asyncio
import re
import time
from datetime import datetime, timezone
from typing import Any, TypedDict
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field
from urllib.robotparser import RobotFileParser

from .storage import research_store
from .knowledge import knowledge_layer


router = APIRouter(tags=["research"])
ACTIVE_PROGRESS: dict[str, dict[str, Any]] = {}


class ResearchRequest(BaseModel):
    city: str = Field(min_length=2, max_length=120)
    country: str | None = Field(default=None, max_length=120)


class ResearchRun(BaseModel):
    city: str
    country: str | None
    status: str
    started_at: str
    completed_at: str
    sources: list[dict[str, Any]]
    facts: list[dict[str, Any]]
    gaps: list[dict[str, Any]]
    workflow: list[dict[str, Any]]
    stores: dict[str, dict[str, Any]] = Field(default_factory=dict)
    metrics: dict[str, Any] = Field(default_factory=dict)
    trace: list[dict[str, Any]] = Field(default_factory=list)
    guardrails: list[dict[str, Any]] = Field(default_factory=list)


RESEARCH_TOPICS = [
    "cardiovascular health hypertension diabetes public health program",
    "health policy healthcare access hospitals government",
    "cardiovascular disease statistics prevention strategy",
]


def _result_url(raw_url: str) -> str:
    """Resolve DuckDuckGo result redirects before crawlability is evaluated."""
    candidate = raw_url
    if candidate.startswith("//"):
        candidate = f"https:{candidate}"
    parsed = urlparse(candidate)
    redirected = parse_qs(parsed.query).get("uddg", [])
    if redirected:
        candidate = unquote(redirected[0])
    return candidate


def _workflow_step(name: str, status: str, detail: str) -> dict[str, str]:
    return {"name": name, "status": status, "detail": detail}


async def _search(client: httpx.AsyncClient, query: str) -> list[dict[str, str]]:
    response = await client.get(
        "https://html.duckduckgo.com/html/",
        params={"q": query},
        headers={"User-Agent": "CARDIO4CitiesResearch/0.1"},
    )
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    results: list[dict[str, str]] = []
    for result in soup.select(".result")[:4]:
        link = result.select_one(".result__a")
        snippet = result.select_one(".result__snippet")
        if link and link.get("href"):
            results.append(
                {
                    "title": link.get_text(" ", strip=True),
                    "url": _result_url(link["href"]),
                    "snippet": snippet.get_text(" ", strip=True) if snippet else "",
                }
            )
    return results


async def _crawlability(client: httpx.AsyncClient, url: str) -> tuple[bool, str]:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False, "Unsupported URL scheme"
    robots_url = urljoin(f"{parsed.scheme}://{parsed.netloc}", "/robots.txt")
    robots = RobotFileParser()
    try:
        robots_response = await client.get(robots_url)
        robots.parse(robots_response.text.splitlines())
        if not robots.can_fetch("CARDIO4CitiesResearch/0.1", url):
            return False, "robots.txt disallows automated access"
    except httpx.HTTPError:
        return False, "Could not verify robots.txt"
    return True, "Public page and robots.txt permits automated access"


async def _extract(client: httpx.AsyncClient, source: dict[str, str]) -> dict[str, Any]:
    response = await client.get(
        source["url"],
        headers={"User-Agent": "CARDIO4CitiesResearch/0.1"},
        follow_redirects=True,
    )
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    for element in soup(["script", "style", "noscript"]):
        element.decompose()
    text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
    return {**source, "text": text[:12000], "retrieved_at": datetime.now(timezone.utc).isoformat()}


def _facts(city: str, extracted: list[dict[str, Any]]) -> list[dict[str, Any]]:
    facts = []
    for source in extracted:
        text = source.get("text", "")
        if not text:
            continue
        evidence = source.get("snippet") or text[:300]
        facts.append(
            {
                "claim": "A retrieved public source discusses cardiovascular or public-health information relevant to the research plan.",
                "category": "source_evidence",
                "geographic_scope": "source-level; city scope requires verification",
                "evidence_quote": evidence,
                "source_title": source["title"],
                "source_url": source["url"],
                "research_city": city,
                "verification_status": "INSUFFICIENT_EVIDENCE",
                "confidence": 0.25,
            }
        )
    return facts


def _fact_check(city: str, candidates: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Independently check evidence before a candidate can enter the report."""
    verified: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for candidate in candidates:
        evidence = candidate["evidence_quote"].strip().lower()
        quote_is_present = bool(evidence) and len(evidence) >= 40
        health_context = any(term in evidence for term in ("cardio", "heart", "hypertension", "diabetes", "health", "disease", "hospital"))
        source_title = candidate.get("source_title", "")
        obvious_city_match = re.search(r"^([A-Z][a-z]+)\s*[:,-]", source_title)
        source_is_for_another_city = bool(obvious_city_match and obvious_city_match.group(1).lower() != city.lower())
        if quote_is_present and health_context and not source_is_for_another_city:
            verified.append(
                {
                    **candidate,
                    "verification_status": "PARTIALLY_SUPPORTED",
                    "confidence": 0.68,
                    "verification_note": f"Evidence supports the source-level topic; city-level scope for {city} is not asserted.",
                }
            )
        else:
            rejected.append(
                {
                    "topic": candidate["category"],
                    "reason": "Claim was withheld because the evidence quote was too short or lacked health context.",
                    "source_url": candidate["source_url"],
                }
            )
    return verified, rejected


async def _run_research_direct(city: str, country: str | None) -> ResearchRun:
    started = datetime.now(timezone.utc)
    started_clock = time.perf_counter()
    trace: list[dict[str, Any]] = []
    extraction_clock = started_clock
    guardrails: list[dict[str, Any]] = []

    def guardrail(name: str, status: str, detail: str) -> None:
        guardrails.append({"name": name, "status": status, "detail": detail, "stage": "City disambiguation", "timestamp": datetime.now(timezone.utc).isoformat()})

    if not re.fullmatch(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ .'-]{1,118}", city):
        guardrail("Target validation", "blocked", "City must contain only a human-readable name.")
        raise HTTPException(status_code=422, detail="City name failed target validation")
    if country and not re.fullmatch(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ .'-]{1,118}", country):
        guardrail("Country validation", "blocked", "Country must contain only a human-readable name.")
        raise HTTPException(status_code=422, detail="Country name failed target validation")
    guardrail("Target validation", "passed", "City and country inputs are valid research identifiers.")
    guardrail("Geographic scope", "warning" if not country else "passed", "Country supplied; city-level and national evidence will remain distinct." if country else "Country not supplied; findings will carry an unresolved geographic-scope warning.")
    guardrail("Live research", "passed", "No city facts are pre-seeded; sources are collected at request time.")
    guardrail("Evidence gate", "passed", "Claims require a source quote and verification decision before publication.")

    def trace_stage(name: str, status: str, detail: str, duration_ms: float) -> None:
        trace.append({"timestamp": datetime.now(timezone.utc).isoformat(), "stage": name, "status": status, "detail": detail, "duration_ms": round(duration_ms, 2)})

    location = f"{city}, {country}" if country else city
    ACTIVE_PROGRESS[city.lower()] = {"city": city, "stage": "City check", "message": "Checking the city name and research scope.", "percent": 8, "updated_at": datetime.now(timezone.utc).isoformat()}
    workflow = [
        _workflow_step("City disambiguation", "completed", f"Research target: {location}"),
        _workflow_step("Research planning", "completed", f"Generated {len(RESEARCH_TOPICS)} topic queries"),
        _workflow_step("Live web discovery", "running", "Querying public search results"),
    ]
    trace_stage("City disambiguation", "completed", f"Research target: {location}; {len(guardrails)} guardrails evaluated", 0)
    trace_stage("Research planning", "completed", f"Generated {len(RESEARCH_TOPICS)} topic queries", 0)
    sources: list[dict[str, Any]] = []
    extracted: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            ACTIVE_PROGRESS[city.lower()] = {"city": city, "stage": "Finding public sources", "message": "Searching government, health, and research websites.", "percent": 25, "updated_at": datetime.now(timezone.utc).isoformat()}
            discovery_clock = time.perf_counter()
            result_groups = await asyncio.gather(
                *[_search(client, f"{location} {topic}") for topic in RESEARCH_TOPICS]
            )
            seen: set[str] = set()
            for group in result_groups:
                for source in group:
                    if source["url"] not in seen:
                        seen.add(source["url"])
                        sources.append(source)
            workflow[2] = _workflow_step("Live web discovery", "completed", f"Found {len(sources)} candidate sources")
            trace_stage("Live web discovery", "completed", f"Found {len(sources)} candidate sources", (time.perf_counter() - discovery_clock) * 1000)
            approved = []
            ACTIVE_PROGRESS[city.lower()] = {"city": city, "stage": "Checking source access", "message": "Checking which websites allow automated reading.", "percent": 42, "updated_at": datetime.now(timezone.utc).isoformat()}
            crawl_clock = time.perf_counter()
            for source in sources[:8]:
                allowed, reason = await _crawlability(client, source["url"])
                source["crawl_allowed"] = allowed
                source["crawlability_reason"] = reason
                if allowed:
                    approved.append(source)
            workflow.append(_workflow_step("Crawlability detection", "completed", f"Approved {len(approved)} sources"))
            trace_stage("Crawlability detection", "completed", f"Approved {len(approved)} of {len(sources)} sources", (time.perf_counter() - crawl_clock) * 1000)
            extraction_clock = time.perf_counter()
            ACTIVE_PROGRESS[city.lower()] = {"city": city, "stage": "Reading sources", "message": f"Reading {len(approved)} approved public sources.", "percent": 60, "updated_at": datetime.now(timezone.utc).isoformat()}
            for source in approved[:5]:
                try:
                    extracted.append(await _extract(client, source))
                except httpx.HTTPError:
                    gaps.append({"topic": "source extraction", "reason": f"Could not retrieve {source['url']}"})
    except httpx.HTTPError as error:
        raise HTTPException(status_code=502, detail=f"Live research failed: {error}") from error

    workflow.append(_workflow_step("Evidence extraction", "completed", f"Read {len(extracted)} sources"))
    trace_stage("Evidence extraction", "completed", f"Read {len(extracted)} sources", (time.perf_counter() - extraction_clock) * 1000)
    if not extracted:
        gaps.append(
            {
                "topic": "source extraction",
                "reason": "Sources were discovered, but no page content was available to support a claim.",
            }
        )
    candidates = _facts(city, extracted)
    fact_check_clock = time.perf_counter()
    ACTIVE_PROGRESS[city.lower()] = {"city": city, "stage": "Checking evidence", "message": "Separating supported findings from claims that need more proof.", "percent": 75, "updated_at": datetime.now(timezone.utc).isoformat()}
    facts, rejected = _fact_check(city, candidates)
    gaps.extend(rejected)
    if not candidates:
        gaps.append(
            {
                "topic": "fact extraction",
                "reason": "No candidate facts were generated from the extractable evidence.",
            }
        )
    workflow.append(_workflow_step("Independent fact checking", "completed", f"Checked {len(candidates)} candidates; published {len(facts)}, withheld {len(rejected)}"))
    trace_stage("Independent fact checking", "completed", f"Published {len(facts)}; withheld {len(rejected)}", (time.perf_counter() - fact_check_clock) * 1000)
    indexing_clock = time.perf_counter()
    ACTIVE_PROGRESS[city.lower()] = {"city": city, "stage": "Saving the brief", "message": "Saving evidence and updating the knowledge stores.", "percent": 90, "updated_at": datetime.now(timezone.utc).isoformat()}
    stores = await knowledge_layer.index_run({"city": city, "sources": sources, "facts": facts})
    trace_stage("Knowledge graph and stores", "completed", "Completed provider indexing attempt", (time.perf_counter() - indexing_clock) * 1000)
    workflow.append(_workflow_step("Knowledge graph and stores", "completed", "Recorded relational evidence and reported vector/graph provider readiness"))
    workflow.append(_workflow_step("Report generation", "completed", "Generated evidence-first research workspace"))
    trace_stage("Report generation", "completed", "Generated evidence-first research workspace", 0)
    completed = datetime.now(timezone.utc)
    total_duration_ms = (time.perf_counter() - started_clock) * 1000
    metrics = {
        "total_duration_ms": round(total_duration_ms, 2),
        "sources_discovered": len(sources),
        "sources_crawl_approved": sum(1 for source in sources if source.get("crawl_allowed")),
        "sources_extracted": len(extracted),
        "candidate_facts": len(candidates),
        "verified_facts": len(facts),
        "withheld_facts": len(rejected),
        "knowledge_gaps": len(gaps),
        "evidence_coverage_percent": round((len(facts) / len(candidates)) * 100, 1) if candidates else 0,
        "city_scope_quality_percent": round(sum(1 for fact in facts if fact.get("geographic_scope") == "city") / len(facts) * 100, 1) if facts else 0,
        "provider_errors": sum(1 for store in stores.values() if store.get("status") == "error"),
        "guardrails_evaluated": len(guardrails),
        "guardrails_passed": sum(1 for item in guardrails if item["status"] == "passed"),
        "guardrail_warnings": sum(1 for item in guardrails if item["status"] == "warning"),
        "guardrails_blocked": sum(1 for item in guardrails if item["status"] == "blocked"),
    }
    result = ResearchRun(
        city=city,
        country=country,
        status="completed_with_gaps",
        started_at=started.isoformat(),
        completed_at=completed.isoformat(),
        sources=sources,
        facts=facts,
        gaps=gaps,
        workflow=workflow,
        stores=stores,
        metrics=metrics,
        trace=trace,
        guardrails=guardrails,
    )
    research_store.save_run(result.model_dump())
    ACTIVE_PROGRESS[city.lower()] = {"city": city, "stage": "Ready", "message": "Your city brief is ready to explore.", "percent": 100, "updated_at": datetime.now(timezone.utc).isoformat()}
    return result


class ResearchGraphState(TypedDict):
    city: str
    country: str | None
    result: ResearchRun | None


async def _research_node(state: ResearchGraphState) -> dict[str, ResearchRun]:
    result = await _run_research_direct(state["city"], state.get("country"))
    return {"result": result}


async def _report_node(state: ResearchGraphState) -> dict[str, ResearchRun]:
    result = state["result"]
    if result is None:
        raise RuntimeError("Research node did not produce a result")
    return {"result": result}


research_graph = StateGraph(ResearchGraphState)
research_graph.add_node("research", _research_node)
research_graph.add_node("report", _report_node)
research_graph.add_edge(START, "research")
research_graph.add_edge("research", "report")
research_graph.add_edge("report", END)
compiled_research_graph = research_graph.compile()


async def run_research(city: str, country: str | None) -> ResearchRun:
    state = await compiled_research_graph.ainvoke({"city": city, "country": country, "result": None})
    result = state.get("result")
    if result is None:
        raise HTTPException(status_code=500, detail="Research graph produced no report")
    return result


@router.post("/research", response_model=ResearchRun)
async def research(request: ResearchRequest) -> ResearchRun:
    return await run_research(request.city.strip(), request.country.strip() if request.country else None)


@router.get("/progress/{city}")
def progress(city: str) -> dict[str, Any]:
    return ACTIVE_PROGRESS.get(city.strip().lower(), {"city": city, "stage": "Waiting", "message": "Ready to begin research.", "percent": 0})


class QuestionRequest(BaseModel):
    city: str = Field(min_length=2, max_length=120)
    question: str = Field(min_length=3, max_length=500)


@router.get("/stores")
def stores() -> dict[str, dict[str, Any]]:
    return knowledge_layer.status()


@router.get("/graph/{city}")
def graph(city: str) -> dict[str, list[dict[str, Any]]]:
    return knowledge_layer.graph(city)


@router.get("/configuration")
def configuration() -> dict[str, Any]:
    return knowledge_layer.configuration()


@router.get("/history")
def history() -> list[dict[str, Any]]:
    return research_store.list_runs()


def _report_html(run: dict[str, Any]) -> str:
    def esc(value: Any) -> str:
        return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")

    facts = "".join(
        f"<article><h3>{esc(fact['claim'])}</h3><p><strong>Evidence:</strong> {esc(fact['evidence_quote'])}</p><p><a href='{esc(fact['source_url'])}'>{esc(fact['source_title'])}</a></p></article>"
        for fact in run.get("facts", [])
    ) or "<p>No facts passed the independent evidence check.</p>"
    gaps = "".join(f"<li><strong>{esc(gap['topic'])}:</strong> {esc(gap['reason'])}</li>" for gap in run.get("gaps", [])) or "<li>No gaps recorded.</li>"
    sources = "".join(f"<li><a href='{esc(source['url'])}'>{esc(source['title'])}</a> ({'crawl approved' if source.get('crawl_allowed') else 'not crawled'})</li>" for source in run.get("sources", []))
    return f"""<!doctype html><html><head><meta charset='utf-8'><title>{esc(run['city'])} CARDIO4Cities brief</title><style>body{{font:16px Georgia,serif;max-width:880px;margin:48px auto;color:#172222;line-height:1.55}}h1{{font:42px Arial,sans-serif;line-height:1.05}}h2{{font:22px Arial,sans-serif;border-bottom:1px solid #d8e0dc;padding-bottom:8px;margin-top:40px}}h3{{font:18px Arial,sans-serif}}.meta{{color:#687575;font:12px monospace}}article{{border-left:3px solid #1b7654;padding:4px 18px;margin:20px 0;background:#f5f7f2}}a{{color:#1b7654}}</style></head><body><p class='meta'>CARDIO4Cities / evidence-first field intelligence</p><h1>{esc(run['city'])} intelligence brief</h1><p class='meta'>Generated {esc(run['completed_at'])} · Status: {esc(run['status'])}</p><h2>Verified findings</h2>{facts}<h2>Knowledge gaps</h2><ul>{gaps}</ul><h2>Sources</h2><ul>{sources}</ul><h2>Method</h2><p>Sources were discovered at request time, checked for crawlability before extraction, and passed through an independent evidence gate. Unsupported claims are excluded.</p></body></html>"""


@router.get("/report/{city}", response_class=HTMLResponse)
def report(city: str) -> HTMLResponse:
    run = research_store.latest_run(city)
    if not run:
        raise HTTPException(status_code=404, detail="No research run exists for this city")
    response = HTMLResponse(_report_html(run))
    response.headers["Content-Disposition"] = f'attachment; filename="{city.lower().replace(" ", "-")}-cardio4cities-brief.html"'
    return response


@router.post("/ask")
def ask(request: QuestionRequest) -> dict[str, Any]:
    run = research_store.latest_run(request.city.strip())
    if not run:
        raise HTTPException(status_code=404, detail="No research run exists for this city")
    return {
        "city": request.city,
        "question": request.question,
        **knowledge_layer.answer_from_run(request.question, run),
        "graph": knowledge_layer.graph(request.city.strip()),
        "stores": knowledge_layer.status(),
    }