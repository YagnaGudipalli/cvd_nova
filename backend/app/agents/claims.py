"""Claim extraction agent.

Reads one document and proposes specific, checkable statements about the target
city. It is the only agent allowed to phrase a claim, and it is never allowed to
decide whether that claim may be published.

The anti-fabrication guarantee is mechanical rather than promised. Whatever the
model returns as an evidence quote is looked up in the source text, and the span
that is stored is the one found **in the document**, not the one produced by the
model. A quote that cannot be located is dropped and recorded as a withheld
claim, so a hallucinated citation cannot reach the ledger even if the model
produces one.
"""

import json
import re

from ..config import settings
from ..llm import language_model
from ..ontology import DIMENSIONS, Claim, Document


SYSTEM = """You extract factual claims about a specific city from one source document for a cardiovascular health programme.

Hard rules:
- Only state what the document states. Never add outside knowledge, never estimate, never round, never generalise.
- evidence_quote MUST be copied character-for-character from the document. Do not paraphrase, trim mid-word, fix typos or translate it.
- If the document is about a different place, or contains nothing relevant, return {"claims": []}. An empty answer is correct and expected.
- Mark geographic_scope honestly: "city" only if the statement is about the target city itself; "national" for country-level figures; "regional"; "international"; or "unknown".
- Never describe a named individual's opinions, intentions or attitudes. Organisations and their published positions are fine.
- Prefer statements carrying numbers, programme names, policy names, institutions and dates.

Return strict JSON:
{"claims": [{"claim": "<one sentence, self-contained>", "dimension": "<one of the given keys>", "evidence_quote": "<verbatim span from the document>", "geographic_scope": "city|regional|national|international|unknown", "entities": [{"name": "<entity>", "type": "Organization|Programme|Policy|HealthIndicator|Place"}]}]}"""

HEALTH_TERMS = (
    "cardiovascular", "heart", "hypertension", "blood pressure", "diabetes", "cholesterol", "dyslipid",
    "stroke", "ncd", "noncommunicable", "non-communicable", "mortality", "prevalence", "screening",
    "primary care", "clinic", "hospital", "health", "obesity", "tobacco", "salt", "physical activity",
)
VALID_SCOPES = {"city", "regional", "national", "international", "unknown"}
VALID_TYPES = {"Organization", "Programme", "Policy", "HealthIndicator", "Place"}


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def ground_quote(quote: str, document_text: str) -> str | None:
    """Return the verbatim span from ``document_text`` matching ``quote``.

    Returns ``None`` when the quote cannot be located, which is how a fabricated
    citation is caught.
    """
    quote = _normalise(quote)
    if len(quote) < 25:
        return None
    haystack = _normalise(document_text)
    index = haystack.find(quote)
    if index >= 0:
        return haystack[index : index + len(quote)]

    lowered = haystack.lower()
    index = lowered.find(quote.lower())
    if index >= 0:
        return haystack[index : index + len(quote)]

    # Tolerate a model that trimmed, extended or paraphrased part of the span.
    # Anchor on its opening words and return the sentence that actually contains
    # them. Shorter anchors are tried in turn because the paraphrase may begin
    # early in the quote; whatever is returned still comes from the document.
    words = quote.split()
    for length in (8, 6, 5, 4):
        if len(words) < length:
            continue
        anchor = " ".join(words[:length]).lower()
        if len(anchor) < 20:
            continue
        index = lowered.find(anchor)
        if index < 0:
            continue
        start = max(0, haystack.rfind(". ", 0, index) + 1)
        end = haystack.find(". ", index + len(anchor))
        span = haystack[start : (end + 1) if end > 0 else min(len(haystack), index + 400)].strip()
        if span:
            return span
    return None


def relevant_window(text: str, city: str, country: str | None, budget: int) -> str:
    """Select the passages of ``text`` most likely to hold claims about ``city``.

    Sending a whole document costs tokens and dilutes attention; sending its
    first N characters usually sends front matter. Instead, sentences are scored
    by city mention, health vocabulary and figures, the best are kept together
    with their neighbours for context, and they are returned in document order.

    This cannot introduce fabrication risk: it only removes text, and quotes are
    still grounded against the **full** document afterwards.
    """
    if len(text) <= budget:
        return text
    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", text) if part.strip()]
    city_lower, country_lower = city.lower(), (country or "").lower()

    def score(sentence: str) -> float:
        lowered = sentence.lower()
        value = sum(1 for term in HEALTH_TERMS if term in lowered)
        if city_lower in lowered:
            value += 5
        if country_lower and country_lower in lowered:
            value += 1
        if re.search(r"\d", sentence):
            value += 1
        return value

    ranked = sorted(range(len(sentences)), key=lambda index: score(sentences[index]), reverse=True)
    chosen: set[int] = set()
    used = 0
    for index in ranked:
        value = score(sentences[index])
        if value < 2:
            break
        # Only sentences naming the city earn their neighbours as context;
        # a generic health mention is not worth spending budget around.
        span = (index - 1, index, index + 1) if city_lower in sentences[index].lower() else (index,)
        for neighbour in span:
            if 0 <= neighbour < len(sentences) and neighbour not in chosen:
                cost = len(sentences[neighbour]) + 1
                if used + cost > budget:
                    continue
                chosen.add(neighbour)
                used += cost
        if used >= budget:
            break
    if not chosen:
        return text[:budget]

    # Paragraph breaks between non-adjacent runs discourage a model from
    # stitching a quote across a gap, which grounding would then reject.
    ordered = sorted(chosen)
    parts, run = [], [sentences[ordered[0]]]
    for previous, current in zip(ordered, ordered[1:]):
        if current == previous + 1:
            run.append(sentences[current])
        else:
            parts.append(" ".join(run))
            run = [sentences[current]]
    parts.append(" ".join(run))
    return "\n\n".join(parts)


def _sentences(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", text) if 60 <= len(part.strip()) <= 420]


def heuristic_claims(document: Document, city: str, country: str | None, limit: int) -> list[Claim]:
    """Extractive fallback when no model is available.

    It selects sentences rather than writing them, so it cannot fabricate: the
    claim text *is* the source text.
    """
    scored: list[tuple[float, str]] = []
    for sentence in _sentences(document.text):
        lowered = sentence.lower()
        health_hits = sum(1 for term in HEALTH_TERMS if term in lowered)
        # Without a model to judge relevance, the city name is the only reliable
        # signal that a sentence is about the target rather than about the world.
        # Requiring it keeps the fallback honest instead of filling the brief
        # with globally-scoped filler that happens to mention health.
        if not health_hits or city.lower() not in lowered:
            continue
        score = health_hits + 4
        if re.search(r"\d", sentence):
            score += 2
        scored.append((score, sentence))

    scored.sort(key=lambda item: item[0], reverse=True)
    claims: list[Claim] = []
    for _, sentence in scored[:limit]:
        claims.append(
            Claim(
                claim=sentence,
                dimension=document.dimension,
                evidence_quote=sentence,
                geographic_scope="city",
                source_url=document.url,
                source_title=document.title,
                extractor="heuristic-extractive",
                quote_verified=True,
            )
        )
    return claims


async def extract_claims(
    document: Document, city: str, country: str | None, *, limit: int | None = None, document_chars: int | None = None
) -> tuple[list[Claim], list[str]]:
    """Return (grounded claims, reasons for claims that were dropped)."""
    limit = limit or settings.max_claims_per_source
    location = f"{city}, {country}" if country else city
    dimension_help = "\n".join(f"- {dimension.key}: {dimension.question}" for dimension in DIMENSIONS)

    payload, model = await language_model.json_call_routed(
        SYSTEM,
        (
            f"Target city: {location}\n"
            f"Dimension keys:\n{dimension_help}\n\n"
            f"Source title: {document.title}\nSource URL: {document.url}\n"
            f"Extract at most {limit} claims.\n\n"
            f"--- DOCUMENT EXCERPTS ---\n{relevant_window(document.text, city, country, document_chars or settings.llm_document_chars)}\n--- END ---"
        ),
        models=settings.llm_extraction_models,
        # Headroom for reasoning models, which think before answering; low effort
        # keeps that short. Ignored by models that do not reason.
        max_tokens=1800,
        reasoning_effort="low",
        purpose="claim-extraction",
    )

    if payload is None:
        return heuristic_claims(document, city, country, limit), []

    raw = payload.get("claims")
    if not isinstance(raw, list):
        return [], []

    valid_dimensions = {dimension.key for dimension in DIMENSIONS}
    claims: list[Claim] = []
    dropped: list[str] = []

    for item in raw[: limit + 3]:
        if not isinstance(item, dict):
            continue
        claim_text = _normalise(str(item.get("claim", "")))
        quote = str(item.get("evidence_quote", ""))
        if len(claim_text) < 20:
            continue

        grounded = ground_quote(quote, document.text)
        if not grounded:
            dropped.append(claim_text[:160])
            continue

        dimension = str(item.get("dimension", "")).strip()
        scope = str(item.get("geographic_scope", "unknown")).strip().lower()
        entities = [
            {"name": _normalise(str(entity.get("name", "")))[:120], "type": str(entity.get("type", "")).strip()}
            for entity in (item.get("entities") or [])
            if isinstance(entity, dict) and entity.get("name") and str(entity.get("type", "")).strip() in VALID_TYPES
        ]

        claims.append(
            Claim(
                claim=claim_text[:500],
                dimension=dimension if dimension in valid_dimensions else document.dimension,
                evidence_quote=grounded[:900],
                geographic_scope=scope if scope in VALID_SCOPES else "unknown",
                source_url=document.url,
                source_title=document.title,
                entities=entities[:8],
                extractor=f"llm:{model}",
                quote_verified=True,
            )
        )

    return claims[:limit], dropped
