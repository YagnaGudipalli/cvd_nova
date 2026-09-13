"""Research planner agent.

Turns a bare city name into a set of dimension-scoped search queries. On the
first round it plans for breadth across every dimension; on later rounds it is
told which dimensions came back empty and plans only for those, which is what
makes the sufficiency loop in the workflow do useful work instead of repeating
itself.

If no model is available the planner falls back to seed terms from the
ontology. The plan is then less adaptive but the pipeline still runs, and the
UI reports which planner produced it.
"""

import json

from ..config import settings
from ..llm import language_model
from ..ontology import DIMENSIONS, DIMENSION_LABELS, PlannedQuery


SYSTEM = """You plan public-internet research about a specific city for a cardiovascular health programme.

Rules:
- Produce search-engine queries, not questions. Short, keyword-dense, no boolean operators.
- Every query must contain the city name so results are city-specific.
- Prefer wording that surfaces government, ministry, municipal, WHO, academic and hospital sources.
- Never invent facts, statistics or organisation names. You are writing queries only.
- Return strict JSON: {"queries": [{"dimension": "<key>", "query": "<text>", "rationale": "<one short sentence>"}]}"""


def _fallback_plan(city: str, country: str | None, dimensions: list[str], round_number: int) -> list[PlannedQuery]:
    location = f"{city} {country}".strip() if country else city
    plan: list[PlannedQuery] = []
    for dimension in DIMENSIONS:
        if dimension.key not in dimensions:
            continue
        for term in dimension.seed_terms[: 2 if round_number > 1 else 1]:
            plan.append(
                PlannedQuery(
                    dimension=dimension.key,
                    query=f"{location} {term}",
                    rationale=f"Seed term for {DIMENSION_LABELS[dimension.key].lower()}",
                    round=round_number,
                )
            )
    return plan


async def plan_research(
    city: str,
    country: str | None,
    *,
    dimensions: list[str],
    round_number: int = 1,
    known_gaps: list[str] | None = None,
    budget: int | None = None,
) -> tuple[list[PlannedQuery], str]:
    """Return (queries, planner_name)."""
    target = ", ".join(dimensions)
    location = f"{city}, {country}" if country else city
    budget = budget or settings.queries_per_round

    if round_number == 1:
        instruction = (
            f"City: {location}\n"
            f"Plan {budget} queries covering these research dimensions: {target}.\n"
            "Dimension meanings:\n"
            + "\n".join(f"- {dimension.key}: {dimension.question}" for dimension in DIMENSIONS if dimension.key in dimensions)
        )
    else:
        gaps = "\n".join(f"- {gap}" for gap in (known_gaps or [])) or "- No evidence was found for these dimensions."
        instruction = (
            f"City: {location}\n"
            f"This is follow-up round {round_number}. The first round found no usable evidence for: {target}.\n"
            f"What is still missing:\n{gaps}\n"
            f"Plan {budget} DIFFERENT queries for those dimensions. Vary the wording: try official body names, "
            "local-language terms for the country, statistical report titles, and WHO or World Bank profile phrasing."
        )

    payload = await language_model.json_call(SYSTEM, instruction, max_tokens=900, purpose="planner", tier="fast")
    if not payload or not isinstance(payload.get("queries"), list):
        return _fallback_plan(city, country, dimensions, round_number), "seed-terms"

    valid_keys = {dimension.key for dimension in DIMENSIONS}
    plan: list[PlannedQuery] = []
    for item in payload["queries"]:
        if not isinstance(item, dict):
            continue
        query = str(item.get("query", "")).strip()
        dimension = str(item.get("dimension", "")).strip()
        if not query or dimension not in valid_keys:
            continue
        if city.lower() not in query.lower():
            query = f"{city} {query}"
        plan.append(
            PlannedQuery(
                dimension=dimension,
                query=query[:180],
                rationale=str(item.get("rationale", ""))[:200],
                round=round_number,
            )
        )
    if not plan:
        return _fallback_plan(city, country, dimensions, round_number), "seed-terms"
    return plan[: budget + 2], f"llm:{settings.llm_model}"
