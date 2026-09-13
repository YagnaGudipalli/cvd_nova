"""The LangGraph research workflow.

Each stage is its own node with one responsibility, so the graph that runs is
the graph you can walk through in a demonstration. ``/api/workflow`` renders
this structure straight from the compiled graph, which means the diagram cannot
drift away from the code.

    intake → plan → discover → crawl_gate → extract → claim_extraction
                ↑                                            ↓
                └────────── sufficiency ←──────────── fact_check
                                 ↓
                              index → report

The edge back from ``sufficiency`` to ``plan`` is the consequence the case study
asks for: when the fact checker withholds too much and the research does not
cover enough dimensions, the system re-plans against the gaps instead of
publishing a thin brief. It is bounded by ``RESEARCH_MAX_ROUNDS`` so a run
always terminates.
"""

import asyncio
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any, TypedDict

import httpx
from langgraph.graph import END, START, StateGraph

from .agents.claims import extract_claims
from .agents.crawlability import CrawlabilityAgent
from .agents.discovery import authority, discover, fetch_priority
from .agents.extraction import extract
from .agents.factcheck import check_claim
from .agents.planner import plan_research
from .config import settings
from .knowledge import knowledge_layer
from .profiles import ResearchProfile, get_profile
from .ontology import DIMENSION_KEYS, DIMENSION_LABELS, Claim, Document, Gap, PlannedQuery, Source, VerifiedFact
from .storage import research_store


logger = logging.getLogger("cardio4cities.workflow")

# Live progress for the UI, keyed by lowercase city.
ACTIVE_PROGRESS: dict[str, dict[str, Any]] = {}

NAME_PATTERN = re.compile(r"^[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ0-9 .'\-]{1,118}$")


class ResearchState(TypedDict, total=False):
    depth: str
    city: str
    country: str | None
    round: int
    started_at: str
    started_clock: float
    query_plan: list[PlannedQuery]
    planner: str
    target_dimensions: list[str]
    sources: list[Source]
    documents: list[Document]
    candidates: list[Claim]
    facts: list[VerifiedFact]
    gaps: list[Gap]
    coverage: dict[str, int]
    workflow: list[dict[str, Any]]
    trace: list[dict[str, Any]]
    guardrails: list[dict[str, Any]]
    stores: dict[str, dict[str, Any]]
    metrics: dict[str, Any]
    decision: str
    status: str
    completed_at: str


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _progress(city: str, stage: str, message: str, percent: int, round_number: int = 1) -> None:
    """Report progress for the UI.

    The bar never moves backwards. A follow-up research round re-enters planning,
    discovery and reading, whose natural percentages are lower than where the
    first round ended; shown raw, the bar jumped from 66% back to 12%. Follow-up
    stages are mapped into the 70-88% band and labelled with their round.
    """
    key = city.lower()
    if round_number > 1 and percent < 90:
        percent = 70 + round((max(percent, 12) - 12) * 18 / 66)
        stage = f"Round {round_number}: {stage[0].lower()}{stage[1:]}"
    previous = ACTIVE_PROGRESS.get(key, {})
    if percent > 5 and previous.get("percent", 0) > percent:
        percent = previous["percent"]
    logger.info("stage city=%s stage=%r percent=%d", city, stage, percent)
    ACTIVE_PROGRESS[key] = {
        "city": city,
        "stage": stage,
        "message": message,
        "percent": percent,
        "round": round_number,
        "updated_at": _now(),
    }


def _stage(state: ResearchState, name: str, status: str, detail: str, started: float, summary: str = "", **facts: Any) -> None:
    """Record one workflow stage in both the user-facing list and the trace.

    A stage that runs again in a follow-up round keeps one entry in the list,
    but each round's result is kept under ``rounds``. Overwriting it instead hid
    the first round entirely, so a run that looped looked as if it had not.
    ``summary`` is the short plain-language line shown in the list; ``detail``
    keeps the full technical account.
    """
    round_number = state.get("round", 1)
    record = {"round": round_number, "status": status, "summary": summary or detail, "detail": detail, **facts}
    steps: list[dict[str, Any]] = state.setdefault("workflow", [])
    entry = next((existing for existing in steps if existing["name"] == name), None)
    if entry is None:
        entry = {"name": name}
        steps.append(entry)
    history = [item for item in entry.get("rounds", []) if item["round"] != round_number] + [record]
    entry.update(record, rounds=history)
    state.setdefault("trace", []).append(
        {
            "timestamp": _now(),
            "stage": name,
            "status": status,
            "detail": detail,
            "round": state.get("round", 1),
            "duration_ms": round((time.perf_counter() - started) * 1000, 2),
        }
    )


def _profile(state: ResearchState) -> ResearchProfile:
    return get_profile(state.get("depth"))


def _round_budget(full: int, state: ResearchState) -> int:
    """Follow-up rounds get half the budget.

    They only chase dimensions the first round left uncovered, and a full second
    round measured ~250 s — more than half of a run — while often adding little.
    """
    return full if state.get("round", 1) <= 1 else max(2, -(-full // 2))


def _guardrail(state: ResearchState, name: str, status: str, detail: str) -> None:
    state.setdefault("guardrails", []).append({"name": name, "status": status, "detail": detail, "timestamp": _now()})


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(settings.http_timeout_seconds, connect=10.0),
        limits=httpx.Limits(max_connections=12),
        headers={"User-Agent": settings.user_agent},
    )


# --------------------------------------------------------------------------- #
# Nodes
# --------------------------------------------------------------------------- #

async def intake_node(state: ResearchState) -> dict[str, Any]:
    """Validate the target and declare the rules the run will be held to."""
    started = time.perf_counter()
    city, country = state["city"], state.get("country")
    _progress(city, "Checking the request", "Validating the city and setting the research scope.", 5)

    if not NAME_PATTERN.match(city):
        _guardrail(state, "Target validation", "blocked", "City must be a human-readable place name.")
        raise ValueError("City name failed target validation")
    if country and not NAME_PATTERN.match(country):
        _guardrail(state, "Target validation", "blocked", "Country must be a human-readable place name.")
        raise ValueError("Country name failed target validation")

    _guardrail(state, "Target validation", "passed", f"Research target resolved to {city}{f', {country}' if country else ''}.")
    _guardrail(
        state,
        "Geographic disambiguation",
        "passed" if country else "warning",
        "Country supplied, so city and national evidence can be told apart reliably."
        if country
        else "No country supplied. City names are ambiguous worldwide, so scope flags carry more weight in this run.",
    )
    _guardrail(state, "Live research only", "passed", "No city data is pre-seeded. Every source is discovered at request time.")
    _guardrail(state, "Evidence gate", "passed", "A claim needs a quote located in its source and an independent verdict before publication.")
    _guardrail(state, "Fabrication guard", "passed", "Figures absent from the quoted evidence are rejected automatically.")
    _guardrail(state, "Named individuals", "passed", "The extractor is instructed never to characterise a person's views or intentions.")

    _stage(
        state, "Intake and guardrails", "completed" if country else "warning",
        f"{len(state.get('guardrails', []))} guardrails evaluated for {city}.", started,
        summary=f"{city} accepted; {len(state.get('guardrails', []))} rules set for the run"
        + ("" if country else ". No country given, so the city name may be ambiguous"),
    )
    return {
        "guardrails": state.get("guardrails", []),
        "workflow": state.get("workflow", []),
        "trace": state.get("trace", []),
        "round": 1,
        "target_dimensions": list(DIMENSION_KEYS),
        "coverage": {key: 0 for key in DIMENSION_KEYS},
        "facts": [],
        "gaps": [],
        "sources": [],
        "documents": [],
        "candidates": [],
    }


async def plan_node(state: ResearchState) -> dict[str, Any]:
    """Expand the city into dimension-scoped search queries."""
    started = time.perf_counter()
    city, round_number = state["city"], state.get("round", 1)
    dimensions = state.get("target_dimensions") or list(DIMENSION_KEYS)
    _progress(city, "Planning the research", f"Deciding what to look for across {len(dimensions)} areas of city health.", 12, state.get("round", 1))

    known_gaps = [gap.reason for gap in state.get("gaps", [])][-6:]
    plan, planner = await plan_research(
        city, state.get("country"), dimensions=dimensions, round_number=round_number, known_gaps=known_gaps,
        budget=_round_budget(_profile(state).queries_per_round, state),
    )
    covered = ", ".join(DIMENSION_LABELS[key] for key in dimensions)
    _stage(
        state,
        "Research planning",
        "completed",
        f"Round {round_number}: {len(plan)} queries planned by {planner} across {covered}.",
        started,
        summary=f"{len(plan)} searches planned" + (f" for the {len(dimensions)} areas still missing" if round_number > 1 else f" across {len(dimensions)} areas"),
        queries=[{"dimension": item.dimension, "query": item.query, "rationale": item.rationale} for item in plan],
    )
    return {"query_plan": plan, "planner": planner, "workflow": state["workflow"], "trace": state["trace"]}


async def discover_node(state: ResearchState) -> dict[str, Any]:
    """Query the public internet at request time."""
    started = time.perf_counter()
    city = state["city"]
    _progress(city, "Finding public sources", "Searching government, health and research websites.", 25, state.get("round", 1))

    async with _client() as client:
        found, notes = await discover(client, state.get("query_plan", []), city, results_per_query=_profile(state).results_per_query)

    existing = {source.url for source in state.get("sources", [])}
    fresh = [source for source in found if source.url not in existing]
    sources = state.get("sources", []) + fresh
    gaps = state.get("gaps", []) + [
        Gap(topic="Source discovery", dimension="", reason=note, kind="missing") for note in notes
    ]
    by_authority: dict[str, int] = {}
    for source in fresh:
        by_authority[authority(source.url)[1]] = by_authority.get(authority(source.url)[1], 0) + 1

    _stage(
        state,
        "Live source discovery",
        "completed" if fresh else "warning",
        f"Round {state.get('round', 1)}: {len(fresh)} new candidate sources ({', '.join(f'{count} {label}' for label, count in sorted(by_authority.items())) or 'none'}).",
        started,
        summary=f"{len(fresh)} new sources found" if fresh else "No new sources found",
        authority_mix=by_authority,
    )
    return {"sources": sources, "gaps": gaps, "workflow": state["workflow"], "trace": state["trace"]}


async def crawl_gate_node(state: ResearchState) -> dict[str, Any]:
    """Decide what may be crawled, before anything is crawled."""
    started = time.perf_counter()
    city = state["city"]
    _progress(city, "Checking source permissions", "Reading robots.txt to see which sites allow automated access.", 38, state.get("round", 1))

    pending = [source for source in state.get("sources", []) if source.crawl_allowed is None]
    agent = CrawlabilityAgent()
    async with _client() as client:
        await agent.assess_all(client, pending)

    allowed = [source for source in state.get("sources", []) if source.crawl_allowed]
    blocked = [source for source in pending if source.crawl_allowed is False]
    gaps = state.get("gaps", []) + [
        Gap(
            topic=f"Source not crawled: {source.title[:70]}",
            dimension=source.dimension,
            reason=source.crawlability_reason,
            source_url=source.url,
            kind="blocked",
        )
        for source in blocked
    ]
    _stage(
        state,
        "Crawlability gate",
        "completed",
        f"{len(pending)} sources assessed before any fetch: {len(pending) - len(blocked)} permitted, {len(blocked)} refused and left uncrawled.",
        started,
        summary=f"{len(pending) - len(blocked)} sites allow reading, {len(blocked)} refused",
        permitted=len(pending) - len(blocked),
        blocked=len(blocked),
    )
    return {"sources": state["sources"], "gaps": gaps, "workflow": state["workflow"], "trace": state["trace"]}


async def extract_node(state: ResearchState) -> dict[str, Any]:
    """Fetch permitted sources and reduce them to clean text."""
    started = time.perf_counter()
    city = state["city"]
    # A page that failed in an earlier round is not retried: it already has a gap.
    unreadable = {gap.source_url for gap in state.get("gaps", []) if gap.kind == "unreachable"}
    queue = [
        source for source in state.get("sources", [])
        if source.crawl_allowed and not source.fetched and source.url not in unreadable
    ]
    queue.sort(key=fetch_priority, reverse=True)
    fetch_budget = _profile(state).source_budget(state.get("round", 1), len(state.get("documents", [])))
    _progress(city, "Reading sources", f"Reading {min(len(queue), fetch_budget)} permitted public sources.", 52, state.get("round", 1))

    if fetch_budget:
        async with _client() as client:
            documents, failures = await extract(client, queue, limit=fetch_budget)
    else:
        documents, failures = [], []

    gaps = state.get("gaps", []) + [
        Gap(
            topic=f"Source unreadable: {source.title[:70]}",
            dimension=source.dimension,
            reason=reason,
            source_url=source.url,
            kind="unreachable",
        )
        for source, reason in failures
    ]
    _stage(
        state,
        "Evidence extraction",
        "completed" if documents else "warning",
        f"{len(documents)} pages read and preserved with their retrieval timestamps; {len(failures)} could not be read.",
        started,
        summary=f"{len(documents)} pages read" + (f", {len(failures)} failed to load" if failures else ""),
        characters=sum(len(document.text) for document in documents),
        read=len(documents),
        failed=len(failures),
        budget=fetch_budget,
        waiting=max(0, len(queue) - fetch_budget),
    )
    return {
        "documents": state.get("documents", []) + documents,
        "sources": state["sources"],
        "gaps": gaps,
        "workflow": state["workflow"],
        "trace": state["trace"],
    }


async def claim_node(state: ResearchState) -> dict[str, Any]:
    """Propose specific, quote-anchored claims from each document."""
    started = time.perf_counter()
    city, country = state["city"], state.get("country")
    documents = [document for document in state.get("documents", []) if not document.processed]
    _progress(city, "Pulling out findings", "Extracting specific statements and locating the exact wording behind each one.", 66, state.get("round", 1))

    done = 0

    async def extract_one(index: int, document: Document):
        nonlocal done
        try:
            profile = _profile(state)
            return await extract_claims(
                document, city, country,
                limit=profile.max_claims_per_source, document_chars=profile.document_chars,
            )
        finally:
            done += 1
            # Extraction is the longest stage; a count shows it is moving.
            _progress(
                city,
                f"Pulling out findings ({done} of {len(documents)} sources)",
                "Extracting specific statements and locating the exact wording behind each one.",
                66 + round(11 * done / max(1, len(documents))),
                state.get("round", 1),
            )

    results = await asyncio.gather(*[extract_one(index, document) for index, document in enumerate(documents)], return_exceptions=True)
    candidates: list[Claim] = []
    ungrounded = 0
    for document, result in zip(documents, results):
        document.processed = True
        if isinstance(result, BaseException):
            logger.warning("claim_extraction_failed url=%s type=%s", document.url, type(result).__name__)
            continue
        claims, dropped = result
        candidates.extend(claims)
        ungrounded += len(dropped)

    gaps = list(state.get("gaps", []))
    if ungrounded:
        gaps.append(
            Gap(
                topic="Ungrounded statements discarded",
                reason=f"{ungrounded} proposed statement(s) cited wording that could not be located in the source document and were discarded before verification.",
                kind="withheld",
            )
        )
    extractor = candidates[0].extractor if candidates else ("llm" if settings.llm_configured else "heuristic-extractive")
    _stage(
        state,
        "Claim extraction",
        "completed" if candidates else "warning",
        f"{len(candidates)} candidate findings proposed by {extractor}; {ungrounded} discarded for citing wording absent from the source.",
        started,
        summary=f"{len(candidates)} possible findings pulled out" + (f", {ungrounded} discarded for misquoting" if ungrounded else ""),
        candidates=len(candidates),
        ungrounded=ungrounded,
        extractor=extractor,
    )
    return {
        "candidates": state.get("candidates", []) + candidates,
        "documents": state.get("documents", []),
        "gaps": gaps,
        "workflow": state["workflow"],
        "trace": state["trace"],
    }


async def fact_check_node(state: ResearchState) -> dict[str, Any]:
    """Independently decide which candidates may be published."""
    started = time.perf_counter()
    city, country = state["city"], state.get("country")
    pending = [claim for claim in state.get("candidates", []) if not claim.checked]
    _progress(city, "Checking the evidence", "An independent check decides what is supported and what is withheld.", 78, state.get("round", 1))

    text_by_url = {document.url: document.text for document in state.get("documents", [])}
    results = await asyncio.gather(
        *[check_claim(claim, city, country, text_by_url.get(claim.source_url, claim.evidence_quote)) for claim in pending],
        return_exceptions=True,
    )

    facts = list(state.get("facts", []))
    gaps = list(state.get("gaps", []))
    published_before = len(facts)
    withheld = 0
    for claim, result in zip(pending, results):
        claim.checked = True
        if isinstance(result, BaseException):
            logger.warning("fact_check_failed type=%s", type(result).__name__)
            gaps.append(
                Gap(
                    topic=f"Unchecked claim in {DIMENSION_LABELS.get(claim.dimension, claim.dimension)}",
                    dimension=claim.dimension,
                    reason="The independent checker could not reach a verdict, so the claim was withheld.",
                    source_url=claim.source_url,
                    kind="withheld",
                )
            )
            withheld += 1
            continue
        fact, gap = result
        if fact:
            facts.append(fact)
        elif gap:
            gaps.append(Gap(**gap))
            withheld += 1

    coverage = {key: 0 for key in DIMENSION_KEYS}
    for fact in facts:
        if fact.geographic_scope == "city" and fact.verification_status in {"SUPPORTED", "PARTIALLY_SUPPORTED"}:
            coverage[fact.dimension] = coverage.get(fact.dimension, 0) + 1

    flagged = sum(1 for fact in facts if fact.scope_flag)
    by_model = sum(1 for fact in facts if fact.verified_by.startswith("llm:"))
    by_rules = len(facts) - by_model
    if by_rules and settings.llm_configured:
        # A configured model that did not verify some facts is a degraded run.
        # Say so, rather than let rule-based checks pass as model verification.
        existing = [item for item in state.get("guardrails", []) if item["name"] == "Verification fallback"]
        detail = (
            f"{by_rules} of {len(facts)} published facts were checked by rules, not the model, "
            "because the model was throttled or unavailable. Treat those verdicts as weaker."
        )
        if existing:
            existing[0].update(status="warning", detail=detail)
        else:
            _guardrail(state, "Verification fallback", "warning", detail)
    _stage(
        state,
        "Independent fact check",
        "completed",
        f"Round {state.get('round', 1)}: {len(pending)} candidates checked, {len(facts) - published_before} published, "
        f"{withheld} withheld as unsupported. Brief now holds {len(facts)} findings, {flagged} carrying a scope warning; "
        f"{by_model} checked by the model, {by_rules} by rules.",
        started,
        summary=f"{len(facts) - published_before} of {len(pending)} passed, {withheld} withheld",
        checked=len(pending),
        published=len(facts) - published_before,
        withheld=withheld,
        scope_flagged=flagged,
        checked_by_model=by_model,
        checked_by_rules=by_rules,
    )
    return {
        "facts": facts,
        "gaps": gaps,
        "coverage": coverage,
        "candidates": state.get("candidates", []),
        "guardrails": state.get("guardrails", []),
        "workflow": state["workflow"],
        "trace": state["trace"],
    }


async def sufficiency_node(state: ResearchState) -> dict[str, Any]:
    """Decide whether the research is good enough to publish."""
    started = time.perf_counter()
    coverage = state.get("coverage", {})
    covered = [key for key, count in coverage.items() if count]
    uncovered = [key for key in DIMENSION_KEYS if not coverage.get(key)]
    round_number = state.get("round", 1)
    exhausted = round_number >= _profile(state).max_rounds

    if len(covered) >= settings.sufficiency_threshold or exhausted:
        decision = "finalise"
        detail = (
            f"{len(covered)} of {len(DIMENSION_KEYS)} dimensions have city-level verified evidence"
            f"{'; research budget exhausted, publishing with gaps declared' if exhausted and len(covered) < settings.sufficiency_threshold else ', which meets the publication threshold'}."
        )
    else:
        decision = "continue"
        detail = f"Only {len(covered)} of {len(DIMENSION_KEYS)} dimensions are covered. Re-planning round {round_number + 1} against {len(uncovered)} uncovered dimensions."
        _progress(state["city"], "Filling the gaps", "Not enough was found. Planning a second, more targeted round of research.", 70)

    gaps = list(state.get("gaps", []))
    if decision == "finalise":
        gaps.extend(
            Gap(
                topic=f"No city-level evidence: {DIMENSION_LABELS[key]}",
                dimension=key,
                reason="Research finished without a verified, city-specific finding for this dimension. Treat it as an open question for the meeting.",
                kind="missing",
            )
            for key in uncovered
        )

    outcome = (
        "searching again" if decision == "continue"
        else "enough to publish" if len(covered) >= settings.sufficiency_threshold
        else "out of rounds, publishing with gaps"
    )
    _stage(
        state, "Sufficiency review", "completed", detail, started,
        summary=f"{len(covered)} of {len(DIMENSION_KEYS)} areas covered: {outcome}",
        covered=covered, uncovered=uncovered, decision=decision,
        threshold=settings.sufficiency_threshold, total=len(DIMENSION_KEYS),
    )
    return {
        "decision": decision,
        "gaps": gaps,
        "round": round_number + (1 if decision == "continue" else 0),
        "target_dimensions": uncovered or list(DIMENSION_KEYS),
        "workflow": state["workflow"],
        "trace": state["trace"],
    }


async def index_node(state: ResearchState) -> dict[str, Any]:
    """Write the run into all three stores."""
    started = time.perf_counter()
    city = state["city"]
    _progress(city, "Saving the brief", "Recording evidence and updating the knowledge stores.", 90)

    facts = [fact.model_dump() for fact in state.get("facts", [])]
    stores = await knowledge_layer.index_run(
        city, state.get("country"), facts, len(state.get("sources", [])),
        on_graph_finish=research_store.update_graph_status,
    )
    degraded = any(store.get("status") in {"error", "degraded"} for store in stores.values())
    _stage(
        state,
        "Knowledge indexing",
        "warning" if degraded else "completed",
        " ".join(store.get("detail", "") for store in stores.values()).strip(),
        started,
        summary=f"{len(facts)} findings saved" + ("; a store had a problem" if degraded else ""),
        stores=stores,
    )
    return {"stores": stores, "workflow": state["workflow"], "trace": state["trace"]}


async def report_node(state: ResearchState) -> dict[str, Any]:
    """Assemble the brief and its quality metrics."""
    started = time.perf_counter()
    city = state["city"]
    facts = state.get("facts", [])
    gaps = state.get("gaps", [])
    sources = state.get("sources", [])
    candidates = state.get("candidates", [])
    coverage = state.get("coverage", {})

    city_scoped = [fact for fact in facts if fact.geographic_scope == "city"]
    covered = [key for key, count in coverage.items() if count]
    status = (
        "completed" if len(covered) >= settings.sufficiency_threshold and facts
        else "completed_with_gaps" if facts
        else "no_verified_evidence"
    )

    metrics = {
        "total_duration_ms": round((time.perf_counter() - state["started_clock"]) * 1000, 2),
        "research_rounds": state.get("round", 1),
        "sources_discovered": len(sources),
        "sources_crawl_approved": sum(1 for source in sources if source.crawl_allowed),
        "sources_crawl_refused": sum(1 for source in sources if source.crawl_allowed is False),
        "sources_extracted": len(state.get("documents", [])),
        "candidate_claims": len(candidates),
        "verified_facts": len(facts),
        "withheld_claims": sum(1 for gap in gaps if gap.kind == "withheld"),
        "scope_flagged_facts": sum(1 for fact in facts if fact.scope_flag),
        "facts_checked_by_model": sum(1 for fact in facts if fact.verified_by.startswith("llm:")),
        "facts_checked_by_rules": sum(1 for fact in facts if not fact.verified_by.startswith("llm:")),
        "claims_extracted_by_model": sum(1 for claim in candidates if claim.extractor.startswith("llm:")),
        "claims_extracted_heuristically": sum(1 for claim in candidates if not claim.extractor.startswith("llm:")),
        "knowledge_gaps": len(gaps),
        "dimensions_covered": len(covered),
        "dimensions_total": len(DIMENSION_KEYS),
        "evidence_coverage_percent": round(len(facts) / len(candidates) * 100, 1) if candidates else 0.0,
        "city_scope_quality_percent": round(len(city_scoped) / len(facts) * 100, 1) if facts else 0.0,
        "mean_confidence": round(sum(fact.confidence for fact in facts) / len(facts), 2) if facts else 0.0,
        "provider_errors": sum(1 for store in state.get("stores", {}).values() if store.get("status") == "error"),
        "guardrails_evaluated": len(state.get("guardrails", [])),
        "guardrails_passed": sum(1 for item in state.get("guardrails", []) if item["status"] == "passed"),
        "guardrail_warnings": sum(1 for item in state.get("guardrails", []) if item["status"] == "warning"),
    }

    _stage(
        state,
        "Brief assembly",
        "completed",
        f"{len(facts)} verified findings, {len(gaps)} declared gaps, {len(covered)} of {len(DIMENSION_KEYS)} dimensions covered.",
        started,
        summary=f"Brief ready: {len(facts)} findings, {len(gaps)} open questions",
    )
    _progress(city, "Ready", "Your city brief is ready to explore.", 100)
    return {
        "status": status,
        "metrics": metrics,
        "completed_at": _now(),
        "workflow": state["workflow"],
        "trace": state["trace"],
    }


def route_after_sufficiency(state: ResearchState) -> str:
    return "plan" if state.get("decision") == "continue" else "index"


# --------------------------------------------------------------------------- #
# Graph assembly
# --------------------------------------------------------------------------- #

def build_graph() -> StateGraph:
    graph = StateGraph(ResearchState)
    graph.add_node("intake", intake_node)
    graph.add_node("plan", plan_node)
    graph.add_node("discover", discover_node)
    graph.add_node("crawl_gate", crawl_gate_node)
    graph.add_node("extract", extract_node)
    graph.add_node("claim_extraction", claim_node)
    graph.add_node("fact_check", fact_check_node)
    graph.add_node("sufficiency", sufficiency_node)
    graph.add_node("index", index_node)
    graph.add_node("report", report_node)

    graph.add_edge(START, "intake")
    graph.add_edge("intake", "plan")
    graph.add_edge("plan", "discover")
    graph.add_edge("discover", "crawl_gate")
    graph.add_edge("crawl_gate", "extract")
    graph.add_edge("extract", "claim_extraction")
    graph.add_edge("claim_extraction", "fact_check")
    graph.add_edge("fact_check", "sufficiency")
    graph.add_conditional_edges("sufficiency", route_after_sufficiency, {"plan": "plan", "index": "index"})
    graph.add_edge("index", "report")
    graph.add_edge("report", END)
    return graph


compiled_research_graph = build_graph().compile()


NODE_RESPONSIBILITIES = {
    "intake": ("Intake and guardrails", "Validates the target and states the rules the run is held to."),
    "plan": ("Research planner", "Expands the city into dimension-scoped search queries."),
    "discover": ("Source discovery", "Queries the public internet at request time and ranks by authority."),
    "crawl_gate": ("Crawlability gate", "Reads robots.txt and decides what may be fetched, before any fetch."),
    "extract": ("Evidence extraction", "Retrieves permitted pages and preserves clean text with timestamps."),
    "claim_extraction": ("Claim extraction", "Proposes specific findings, each anchored to a located quote."),
    "fact_check": ("Independent fact check", "Separately decides what is supported, scope-flagged or withheld."),
    "sufficiency": ("Sufficiency review", "Measures dimension coverage and decides to re-plan or publish."),
    "index": ("Knowledge indexing", "Writes the run to the relational, vector and graph stores."),
    "report": ("Brief assembly", "Assembles findings, gaps and quality metrics."),
}


def workflow_structure() -> dict[str, Any]:
    """Describe the compiled graph, read from the graph itself."""
    drawable = compiled_research_graph.get_graph()
    nodes = [
        {
            "id": node_id,
            "name": NODE_RESPONSIBILITIES.get(node_id, (node_id, ""))[0],
            "responsibility": NODE_RESPONSIBILITIES.get(node_id, (node_id, ""))[1],
            "is_gate": node_id in {"crawl_gate", "fact_check", "sufficiency"},
        }
        for node_id in drawable.nodes
        if node_id not in {"__start__", "__end__"}
    ]
    edges = [
        {"source": edge.source, "target": edge.target, "conditional": bool(edge.conditional), "label": edge.data or ""}
        for edge in drawable.edges
    ]
    try:
        mermaid = drawable.draw_mermaid()
    except Exception:  # drawing is a convenience, never a hard dependency
        mermaid = ""
    return {
        "framework": "LangGraph StateGraph",
        "nodes": nodes,
        "edges": edges,
        "mermaid": mermaid,
        "cycle": {
            "from": "sufficiency",
            "to": "plan",
            "condition": f"Fewer than {settings.sufficiency_threshold} of {len(DIMENSION_KEYS)} dimensions have city-level verified evidence",
            "bounded_by": f"RESEARCH_MAX_ROUNDS={settings.max_rounds}",
        },
    }


async def run_research(city: str, country: str | None, depth: str | None = None) -> dict[str, Any]:
    """Execute the graph and return a serialisable run record."""
    profile = get_profile(depth)
    initial: ResearchState = {
        "depth": profile.key,
        "city": city,
        "country": country,
        "started_at": _now(),
        "started_clock": time.perf_counter(),
        "workflow": [],
        "trace": [],
        "guardrails": [],
    }
    final = await compiled_research_graph.ainvoke(initial, {"recursion_limit": 60})

    run = {
        "city": city,
        "country": country,
        "status": final.get("status", "completed_with_gaps"),
        "started_at": final["started_at"],
        "completed_at": final.get("completed_at", _now()),
        "rounds": final.get("round", 1),
        "depth": profile.key,
        "profile": profile.as_dict(),
        "planner": final.get("planner", "unknown"),
        "sources": [source.model_dump() for source in final.get("sources", [])],
        "facts": [fact.model_dump() for fact in final.get("facts", [])],
        "gaps": [gap.model_dump() for gap in final.get("gaps", [])],
        "coverage": final.get("coverage", {}),
        "dimensions": [{"key": key, "label": DIMENSION_LABELS[key], "verified_facts": final.get("coverage", {}).get(key, 0)} for key in DIMENSION_KEYS],
        "workflow": final.get("workflow", []),
        "trace": final.get("trace", []),
        "guardrails": final.get("guardrails", []),
        "stores": final.get("stores", {}),
        "metrics": final.get("metrics", {}),
    }
    research_store.save_run(run)
    return run
