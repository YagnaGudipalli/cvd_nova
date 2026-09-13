"""Independent fact-checking agent.

This agent never sees the extractor's reasoning and never produces claims. It
receives a claim, the evidence span that was grounded in the source, and the
surrounding context, and it answers one question: does this evidence actually
support this statement about this city?

It can return UNSUPPORTED, and that has a consequence in the workflow — the
claim is withheld from the brief and converted into a knowledge gap. It can also
return SCOPE_MISMATCH, which is the specific failure the case study calls out:
national data presented as city data. Scope-mismatched claims are published only
with an explicit flag attached.

Two deterministic guards run before the model and can veto on their own:

* **Numeric guard** — any figure in the claim that is absent from the evidence
  span means the statistic was invented or altered. That is an automatic
  UNSUPPORTED, with no model opinion involved.
* **Grounding guard** — a claim whose quote was never located in the source
  cannot reach this stage at all.
"""

import re

from ..config import settings
from ..llm import language_model
from ..ontology import DIMENSION_LABELS, Claim, VerifiedFact


SYSTEM = """You are an adversarial fact checker for a public-health research system. You did not write the claim and you have no stake in it being published.

You are given a claim, a verbatim quote from a source document, and the context around that quote. Decide whether the quote genuinely supports the claim for the named target city.

Verdicts:
- SUPPORTED: the quote states the claim for the target city.
- PARTIALLY_SUPPORTED: the quote is relevant and consistent, but weaker, older or less specific than the claim.
- SCOPE_MISMATCH: the evidence is real but describes a country, region or different place rather than the target city.
- UNSUPPORTED: the quote does not support the claim, contradicts it, or is about something else.

Be strict. If the claim adds any specificity the quote does not contain — a number, a date, a place, an attribution — that is at best PARTIALLY_SUPPORTED. Unsupported is a useful, expected answer.

Return strict JSON: {"verdict": "...", "confidence": 0.0-1.0, "reason": "<one sentence a non-technical reader can follow>", "actual_scope": "city|regional|national|international|unknown"}"""

VERDICTS = {"SUPPORTED", "PARTIALLY_SUPPORTED", "SCOPE_MISMATCH", "UNSUPPORTED"}

#: The verdict is constrained to this schema by the provider. In loose JSON mode
#: the checker model produced output the provider rejected as invalid on roughly
#: one call in six, and each rejection dropped that claim to the weaker rules path.
VERDICT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "verdict": {"type": "string", "enum": sorted(VERDICTS)},
        "confidence": {"type": "number"},
        "reason": {"type": "string"},
        "actual_scope": {"type": "string", "enum": ["city", "regional", "national", "international", "unknown"]},
    },
    "required": ["verdict", "confidence", "reason", "actual_scope"],
}
CONFIDENCE_CAP = {"SUPPORTED": 0.92, "PARTIALLY_SUPPORTED": 0.7, "SCOPE_MISMATCH": 0.55, "UNSUPPORTED": 0.0}


def _numbers(text: str) -> set[str]:
    return {token.replace(",", "").replace(" ", "") for token in re.findall(r"\d[\d,. ]*\d|\d", text)}


def numeric_guard(claim: str, quote: str) -> str | None:
    """Return a rejection reason when the claim cites a figure the quote lacks."""
    quote_numbers = _numbers(quote)
    quote_digits = re.sub(r"[^\d]", "", quote)
    missing = []
    for number in _numbers(claim):
        stripped = number.rstrip(".")
        if stripped in quote_numbers or (stripped and stripped.replace(".", "") in quote_digits):
            continue
        missing.append(number)
    if missing:
        return f"The claim cites {', '.join(sorted(missing)[:3])}, which does not appear in the quoted evidence."
    return None


def _context(quote: str, document_text: str, window: int = 700) -> str:
    index = document_text.find(quote[:60])
    if index < 0:
        return quote
    start = max(0, index - window // 2)
    return document_text[start : index + len(quote) + window // 2]


def _rule_verdict(claim: Claim, city: str, country: str | None) -> tuple[str, float, str, str]:
    """Deterministic checker used when no model is configured."""
    quote = claim.evidence_quote.lower()
    if len(quote) < 40:
        return "UNSUPPORTED", 0.0, "The evidence span is too short to support a specific statement.", "unknown"
    if not any(term in quote for term in ("health", "cardio", "heart", "hyperten", "diabet", "disease", "hospital", "clinic", "mortality", "policy", "screening")):
        return "UNSUPPORTED", 0.0, "The quoted evidence carries no health or policy content.", "unknown"
    if city.lower() in quote:
        return "SUPPORTED", 0.7, "The quoted evidence names the target city and carries health content.", "city"
    if country and country.lower() in quote:
        return "SCOPE_MISMATCH", 0.5, "The quoted evidence is country-level rather than specific to the city.", "national"
    return "PARTIALLY_SUPPORTED", 0.45, "The evidence is topically relevant but does not name the target city.", "unknown"


async def check_claim(claim: Claim, city: str, country: str | None, document_text: str) -> tuple[VerifiedFact | None, dict | None]:
    """Verify one claim.

    Returns ``(fact, None)`` when the claim may be published, or
    ``(None, gap)`` when it is withheld.
    """
    reject = numeric_guard(claim.claim, claim.evidence_quote)
    if reject:
        return None, {
            "topic": f"Unverifiable figure in {DIMENSION_LABELS.get(claim.dimension, claim.dimension)}",
            "dimension": claim.dimension,
            "reason": f"{reject} The claim was withheld rather than published.",
            "source_url": claim.source_url,
            "kind": "withheld",
        }

    # Independence is structural, not just a separate prompt: a claim is never
    # checked by the model that extracted it, so the two cannot share a blind spot.
    extractor_model = claim.extractor.removeprefix("llm:")
    checkers = [
        name for name in dict.fromkeys([settings.llm_fast_model, settings.graph_model, *settings.llm_extraction_models])
        if name != extractor_model
    ] or [settings.llm_fast_model]
    payload, checker_model = await language_model.json_call_routed(
        SYSTEM,
        (
            f"Target city: {city}{f', {country}' if country else ''}\n"
            f"Source: {claim.source_title} ({claim.source_url})\n\n"
            f"CLAIM:\n{claim.claim}\n\n"
            f"EVIDENCE QUOTE:\n\"{claim.evidence_quote}\"\n\n"
            f"CONTEXT AROUND THE QUOTE:\n{_context(claim.evidence_quote, document_text)}"
        ),
        # Headroom for the model's reasoning. At 300 the thinking alone used the
        # budget on 5 of 16 claims and nothing was written; low reasoning effort
        # keeps typical output near 100 tokens, so this is rarely consumed.
        max_tokens=600,
        reasoning_effort=settings.checker_reasoning_effort,
        purpose="fact-check",
        models=checkers,
        schema=VERDICT_SCHEMA,
    )

    if payload is None:
        verdict, confidence, reason, scope = _rule_verdict(claim, city, country)
        verified_by = "rules"
    else:
        verdict = str(payload.get("verdict", "")).strip().upper()
        verdict = verdict if verdict in VERDICTS else "UNSUPPORTED"
        try:
            confidence = max(0.0, min(1.0, float(payload.get("confidence", 0.5))))
        except (TypeError, ValueError):
            confidence = 0.5
        reason = str(payload.get("reason", ""))[:400] or "No reason supplied by the checker."
        scope = str(payload.get("actual_scope", claim.geographic_scope)).strip().lower()
        verified_by = f"llm:{checker_model}"

    if verdict == "UNSUPPORTED":
        return None, {
            "topic": f"Unsupported claim in {DIMENSION_LABELS.get(claim.dimension, claim.dimension)}",
            "dimension": claim.dimension,
            "reason": f"{reason} The claim was withheld from the brief.",
            "source_url": claim.source_url,
            "kind": "withheld",
        }

    scope_flag = None
    if verdict == "SCOPE_MISMATCH" or scope in {"national", "regional", "international"}:
        scope_flag = (
            f"This evidence is {scope if scope in {'national', 'regional', 'international'} else 'not city-level'} "
            f"and is NOT specific to {city}. Do not present it as a {city} figure."
        )

    fact = VerifiedFact(
        claim=claim.claim,
        dimension=claim.dimension,
        dimension_label=DIMENSION_LABELS.get(claim.dimension, claim.dimension),
        evidence_quote=claim.evidence_quote,
        geographic_scope=scope if scope in {"city", "regional", "national", "international", "unknown"} else "unknown",
        scope_flag=scope_flag,
        source_url=claim.source_url,
        source_title=claim.source_title,
        entities=claim.entities,
        verification_status=verdict,
        verification_note=reason,
        verified_by=verified_by,
        confidence=round(min(confidence, CONFIDENCE_CAP[verdict]), 2),
        research_city=city,
    )
    return fact, None
