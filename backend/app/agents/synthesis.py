"""Answer synthesis agent.

Writes the answer a City Lead reads, from retrieved evidence only. It is given
numbered evidence and must cite the numbers it used; anything it cannot support
it must decline to answer. The citations are then resolved back to real source
records by the API, so the "where did this come from?" question is answered with
the same objects the research run stored, not with a model's recollection of
them.

With no model configured the agent degrades to an extractive answer that lists
the strongest matching evidence verbatim. Less fluent, equally traceable.
"""

from ..config import settings
from ..llm import language_model


SYSTEM = """You answer questions for a City Lead preparing to meet government and healthcare stakeholders about cardiovascular health.

You may use ONLY the numbered evidence provided. Rules:
- Never add knowledge from outside the evidence. Never estimate or infer a number.
- Cite every statement with the evidence numbers you used, like [1] or [2][3].
- If evidence is flagged as national or regional, say so in the sentence that uses it. Never present it as a city figure.
- If the evidence does not answer the question, set "sufficient" to false and say plainly what is missing. That is a correct answer, not a failure.
- Be concise and concrete: 2-5 sentences, plain language, no preamble.

Return strict JSON: {"answer": "<text with [n] citations>", "cited": [<evidence numbers used>], "sufficient": true|false, "confidence": "high|medium|low", "caveat": "<one sentence on what would strengthen this answer>"}"""


def _format_evidence(evidence: list[dict]) -> str:
    lines = []
    for index, item in enumerate(evidence, start=1):
        origin = item.get("origin", "ledger")
        scope = item.get("geographic_scope", "unknown")
        flag = f" !! {item['scope_flag']}" if item.get("scope_flag") else ""
        status = item.get("verification_status", "")
        lines.append(
            f"[{index}] ({origin}, scope={scope}, check={status}){flag}\n"
            f"    Claim: {item.get('claim', '')}\n"
            f"    Quote: \"{item.get('evidence_quote', '')}\"\n"
            f"    Source: {item.get('source_title', '')} — {item.get('source_url', '')}"
        )
    return "\n".join(lines)


def _extractive(question: str, evidence: list[dict]) -> dict:
    if not evidence:
        return {
            "answer": "The verified evidence for this city does not answer that question.",
            "cited": [],
            "sufficient": False,
            "confidence": "low",
            "caveat": "Nothing in the ledger matched. Run research again or narrow the question.",
            "synthesiser": "extractive",
        }
    top = evidence[:3]
    sentences = [f"{item.get('claim', '').rstrip('.')} [{index}]." for index, item in enumerate(top, start=1)]
    flagged = any(item.get("scope_flag") for item in top)
    return {
        "answer": " ".join(sentences),
        "cited": list(range(1, len(top) + 1)),
        "sufficient": True,
        "confidence": "medium",
        "caveat": (
            "Assembled without a language model, so these are the closest stored findings rather than a written answer."
            + (" Some cited evidence is not city-specific; check the scope flags." if flagged else "")
        ),
        "synthesiser": "extractive",
    }


async def synthesise(question: str, city: str, evidence: list[dict]) -> dict:
    """Return an answer dict with resolved citation indices."""
    if not evidence:
        return {
            "answer": f"No verified evidence in the {city} brief supports an answer to that question.",
            "cited": [],
            "sufficient": False,
            "confidence": "low",
            "caveat": "The system will not infer an answer from evidence it does not have.",
            "synthesiser": "refusal",
        }

    payload = await language_model.json_call(
        SYSTEM,
        f"City: {city}\nQuestion: {question}\n\nEVIDENCE:\n{_format_evidence(evidence)}",
        max_tokens=700,
        purpose="synthesis",
        tier="fast",
    )
    if payload is None or not str(payload.get("answer", "")).strip():
        return _extractive(question, evidence)

    cited = [
        number
        for number in payload.get("cited", [])
        if isinstance(number, int) and 1 <= number <= len(evidence)
    ]
    confidence = str(payload.get("confidence", "medium")).lower()
    return {
        "answer": str(payload["answer"])[:1800],
        "cited": cited,
        "sufficient": bool(payload.get("sufficient", True)),
        "confidence": confidence if confidence in {"high", "medium", "low"} else "medium",
        "caveat": str(payload.get("caveat", ""))[:400],
        "synthesiser": f"llm:{language_model.model_for('fast')}",
    }
