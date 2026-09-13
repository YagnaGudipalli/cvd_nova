"""HTTP surface for the research system."""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .agents.synthesis import synthesise
from .config import settings
from .knowledge import knowledge_layer
from .ontology import DIMENSIONS
from .profiles import DEFAULT_DEPTH, PROFILES
from .reporting import render_report
from .storage import research_store
from .workflow import ACTIVE_PROGRESS, NAME_PATTERN, run_research, workflow_structure


logger = logging.getLogger("cardio4cities.api")
router = APIRouter(tags=["research"])


class ResearchRequest(BaseModel):
    city: str = Field(min_length=2, max_length=120)
    country: str | None = Field(default=None, max_length=120)
    depth: Literal["quick", "balanced", "thorough"] = DEFAULT_DEPTH


class QuestionRequest(BaseModel):
    city: str = Field(min_length=2, max_length=120)
    question: str = Field(min_length=3, max_length=500)


#: Research runs in the background. A run takes minutes; holding one HTTP request
#: open for all of it meant any interruption — a network blip, a sleeping laptop,
#: a proxy timeout — lost the result in the browser even though the server went
#: on to finish and save it.
RESEARCH_JOBS: dict[str, dict[str, Any]] = {}
_RESEARCH_TASKS: dict[str, asyncio.Task] = {}


async def _execute_research(city: str, country: str | None, job: dict[str, Any]) -> None:
    try:
        await run_research(city, country, job["depth"])
        job["status"] = "completed"
    except ValueError as error:
        job.update(status="failed", error=str(error))
    except Exception as error:
        logger.exception("research_run_failed city=%s type=%s", city, type(error).__name__)
        job.update(status="failed", error=f"Research run failed: {type(error).__name__}")
    finally:
        job["finished_at"] = datetime.now(timezone.utc).isoformat()


@router.post("/research", status_code=202)
async def research(request: ResearchRequest, wait: bool = False) -> dict[str, Any]:
    """Start research for a city and return immediately.

    Poll ``GET /api/research/{city}`` for the outcome and ``/api/progress/{city}``
    for live stage updates; fetch ``/api/run/{city}`` once completed. Pass
    ``?wait=true`` to block until the run finishes (scripts and tests).
    """
    city = request.city.strip()
    country = request.country.strip() if request.country else None
    if not NAME_PATTERN.match(city) or (country and not NAME_PATTERN.match(country)):
        raise HTTPException(status_code=422, detail="City and country must be human-readable place names.")

    key = city.lower()
    existing = RESEARCH_JOBS.get(key)
    if existing and existing["status"] == "running":
        job = existing  # a second click joins the run already in progress
    else:
        job = {"city": city, "country": country, "depth": request.depth, "status": "running", "error": None,
               "started_at": datetime.now(timezone.utc).isoformat(), "finished_at": None}
        RESEARCH_JOBS[key] = job
        _RESEARCH_TASKS[key] = asyncio.create_task(_execute_research(city, country, job))

    if wait:
        await asyncio.shield(_RESEARCH_TASKS[key])
        if job["status"] == "failed":
            raise HTTPException(status_code=502, detail=job["error"])
        return research_store.latest_run(city) or job
    return job


@router.get("/profiles")
def profiles() -> dict[str, Any]:
    """Research depth options, for the speed-versus-thoroughness choice in the UI."""
    return {"default": DEFAULT_DEPTH, "profiles": [profile.as_dict() for profile in PROFILES.values()]}


@router.get("/research/{city}")
def research_status(city: str) -> dict[str, Any]:
    job = RESEARCH_JOBS.get(city.strip().lower())
    if job:
        return job
    record = research_store.latest_run(city.strip())
    if record:  # finished before this process started, e.g. after a restart
        return {"city": record["city"], "status": "completed", "error": None,
                "started_at": record.get("started_at"), "finished_at": record.get("completed_at")}
    raise HTTPException(status_code=404, detail="No research has been started for this city.")


@router.get("/progress/{city}")
def progress(city: str) -> dict[str, Any]:
    return ACTIVE_PROGRESS.get(
        city.strip().lower(),
        {"city": city, "stage": "Waiting", "message": "Ready to begin research.", "percent": 0},
    )


@router.post("/ask")
async def ask(request: QuestionRequest) -> dict[str, Any]:
    """Answer a question from stored evidence, across all three stores.

    Retrieval is deliberately layered: the vector store for semantic recall, the
    knowledge graph for relationships, and the relational ledger as a fallback
    so the feature degrades instead of disappearing. Every cited item is
    returned with its source so the answer can be traced.
    """
    city = request.city.strip()
    question = request.question.strip()
    run = research_store.latest_run(city)
    if not run:
        raise HTTPException(status_code=404, detail=f"No research run exists for {city}. Research the city first.")

    vector_hits, graph_hits = await asyncio.gather(
        knowledge_layer.search_vectors(question, city),
        knowledge_layer.search_graph(question, city),
    )

    evidence: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in [*vector_hits, *graph_hits]:
        marker = (item.get("claim") or "")[:160]
        if marker and marker not in seen:
            seen.add(marker)
            evidence.append(item)
    if len(evidence) < 3:
        for item in research_store.search_facts(city, question):
            marker = (item.get("claim") or "")[:160]
            if marker not in seen:
                seen.add(marker)
                evidence.append(item)

    evidence = evidence[:8]
    answer = await synthesise(question, city, evidence)
    cited = [evidence[index - 1] for index in answer["cited"] if 1 <= index <= len(evidence)]

    relevant_gaps = [
        gap for gap in run.get("gaps", [])
        if any(term in gap["reason"].lower() or term in gap["topic"].lower() for term in question.lower().split() if len(term) > 4)
    ][:3]

    return {
        "city": city,
        "question": question,
        **answer,
        "evidence": evidence,
        "cited_evidence": cited,
        "retrieval": {
            "vector_hits": len(vector_hits),
            "graph_hits": len(graph_hits),
            "ledger_fallback": len(evidence) - len(vector_hits) - len(graph_hits) > 0,
            "stores_queried": ["vector", "graph", "relational"],
        },
        "related_gaps": relevant_gaps,
    }


@router.get("/workflow")
def workflow() -> dict[str, Any]:
    """The compiled LangGraph structure, read from the graph itself."""
    return workflow_structure()


@router.get("/dimensions")
def dimensions() -> list[dict[str, Any]]:
    return [dimension.model_dump() for dimension in DIMENSIONS]


@router.get("/runtime")
def runtime() -> dict[str, Any]:
    """Actual capability right now, including graceful-degradation state."""
    return knowledge_layer.runtime()


@router.get("/stores")
def stores() -> dict[str, dict[str, Any]]:
    return knowledge_layer.status()


@router.get("/configuration")
def configuration() -> dict[str, Any]:
    return knowledge_layer.configuration()


@router.get("/provider-check")
async def provider_check() -> dict[str, Any]:
    return await knowledge_layer.provider_check()


@router.get("/graph/{city}")
def graph(city: str) -> dict[str, Any]:
    view = knowledge_layer.graph_view(city.strip())
    view["entities_from_ledger"] = research_store.entities(city.strip())
    view["build"] = _graph_build(city)
    return view


@router.get("/graph-status/{city}")
def graph_status(city: str) -> dict[str, Any]:
    """Progress of the background graph build, for polling."""
    return _graph_build(city)


def _graph_build(city: str) -> dict[str, Any]:
    job = knowledge_layer.graph_job(city.strip())
    if job:
        return job
    # No job in this process (for example after a restart): report what the ledger recorded.
    record = research_store.latest_run(city.strip()) or {}
    graph = record.get("stores", {}).get("graph", {})
    return {"city": city, "status": graph.get("status", "unknown"), "detail": graph.get("detail", ""),
            "episodes_written": graph.get("indexed_records", 0), "episodes_total": graph.get("episodes_total", 0)}


@router.get("/history")
def history() -> list[dict[str, Any]]:
    return research_store.list_runs()


@router.get("/history/{city}")
def city_history(city: str) -> list[dict[str, Any]]:
    return research_store.city_history(city.strip())


@router.get("/run/{city}")
def run(city: str) -> dict[str, Any]:
    record = research_store.latest_run(city.strip())
    if not record:
        raise HTTPException(status_code=404, detail="No research run exists for this city")
    return record


@router.get("/report/{city}", response_class=HTMLResponse)
def report(city: str) -> HTMLResponse:
    record = research_store.latest_run(city.strip())
    if not record:
        raise HTTPException(status_code=404, detail="No research run exists for this city")
    response = HTMLResponse(render_report(record))
    slug = city.strip().lower().replace(" ", "-")
    response.headers["Content-Disposition"] = f'attachment; filename="{slug}-cardio4cities-brief.html"'
    return response
