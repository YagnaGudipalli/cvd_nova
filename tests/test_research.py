"""Tests for the guarantees the case study calls non-negotiable.

These target the properties that must hold regardless of which providers are
configured: a quote must be locatable in its source, an invented figure must be
rejected, the crawl gate must run before any fetch, and the workflow must
actually contain the cycle it claims to.
"""

import pytest

from backend.app.agents.claims import ground_quote, heuristic_claims, relevant_window
from backend.app.agents.crawlability import CrawlabilityAgent
from backend.app.agents.extraction import clean_pdf
from backend.app.agents.factcheck import numeric_guard
from backend.app.ontology import DIMENSION_KEYS, Document, Source
from backend.app.workflow import workflow_structure


DOCUMENT = (
    "Nairobi City County reported that hypertension prevalence among adults reached 24.5 percent in 2019. "
    "The county health department runs a screening programme across 12 clinics. "
    "Globally, cardiovascular disease remains the leading cause of death."
)


# --------------------------------------------------------------------------- #
# Anti-fabrication: a quote must exist in the source
# --------------------------------------------------------------------------- #

def test_ground_quote_returns_the_span_from_the_document():
    quote = "hypertension prevalence among adults reached 24.5 percent in 2019"
    assert ground_quote(quote, DOCUMENT) is not None


def test_ground_quote_rejects_wording_absent_from_the_document():
    assert ground_quote("hypertension prevalence reached 61.2 percent in Mombasa in 2019", DOCUMENT) is None


def test_ground_quote_returns_a_verbatim_span_for_an_exact_substring():
    """An exact substring is returned as found, not expanded."""
    recovered = ground_quote("The county health department runs a screening", DOCUMENT)
    assert recovered and recovered in DOCUMENT


def test_ground_quote_recovers_the_real_sentence_when_the_tail_was_altered():
    """A model that paraphrases the end of its quote is anchored on the opening
    words, and what gets stored is the sentence actually in the document."""
    recovered = ground_quote(
        "The county health department runs a screening initiative across twelve health centres",
        DOCUMENT,
    )
    assert recovered is not None
    assert recovered in DOCUMENT
    assert "12 clinics" in recovered


def test_ground_quote_rejects_a_span_too_short_to_support_anything():
    assert ground_quote("health", DOCUMENT) is None


# --------------------------------------------------------------------------- #
# Anti-fabrication: a figure must appear in the evidence
# --------------------------------------------------------------------------- #

def test_numeric_guard_accepts_a_figure_present_in_the_quote():
    assert numeric_guard("Hypertension prevalence was 24.5 percent.", "prevalence ... reached 24.5 percent in 2019") is None


def test_numeric_guard_rejects_an_invented_figure():
    reason = numeric_guard("Hypertension prevalence was 38.0 percent.", "prevalence ... reached 24.5 percent in 2019")
    assert reason and "38.0" in reason


def test_numeric_guard_ignores_claims_without_figures():
    assert numeric_guard("The county runs a screening programme.", "The county health department runs a screening programme.") is None


# --------------------------------------------------------------------------- #
# The extractive fallback selects rather than writes
# --------------------------------------------------------------------------- #

def test_heuristic_claims_are_verbatim_source_text():
    document = Document(url="https://example.org/a", title="Report", dimension="burden", text=DOCUMENT, retrieved_at="now")
    claims = heuristic_claims(document, "Nairobi", "Kenya", limit=5)
    assert claims, "expected at least one city-specific claim"
    for claim in claims:
        assert claim.claim in DOCUMENT
        assert claim.evidence_quote == claim.claim


def test_heuristic_claims_skip_sentences_that_do_not_name_the_city():
    document = Document(url="https://example.org/a", title="Report", dimension="burden", text=DOCUMENT, retrieved_at="now")
    claims = heuristic_claims(document, "Nairobi", "Kenya", limit=5)
    assert all("Globally" not in claim.claim for claim in claims)


# --------------------------------------------------------------------------- #
# The crawl gate decides before anything is fetched
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_crawl_gate_refuses_formats_we_cannot_read_without_a_network_call():
    source = Source(url="https://example.org/tables.xlsx", title="Tables", dimension="policy")
    assessed = await CrawlabilityAgent().assess(client=None, source=source)
    assert assessed.crawl_allowed is False
    assert assessed.crawl_rule == "content-type"


class _StubResponse:
    def __init__(self, status_code: int, text: str = "") -> None:
        self.status_code = status_code
        self.text = text


class _StubClient:
    """Serves one canned robots.txt so the gate can be tested without a network."""

    def __init__(self, status_code: int = 404, body: str = "") -> None:
        self._response = _StubResponse(status_code, body)

    async def get(self, *args, **kwargs):
        return self._response


@pytest.mark.asyncio
async def test_crawl_gate_no_longer_rejects_pdfs_out_of_hand():
    """Government health strategies are published as PDFs. Refusing them by
    extension discarded the most authoritative city-level evidence available;
    robots.txt, not the file extension, decides whether one may be fetched."""
    source = Source(url="https://health.example.gov/ncd-strategy.pdf", title="NCD strategy", dimension="policy")
    assessed = await CrawlabilityAgent().assess(_StubClient(status_code=404), source)
    assert assessed.crawl_rule == "robots.txt"
    assert assessed.crawl_allowed is True


@pytest.mark.asyncio
async def test_crawl_gate_obeys_a_disallow_rule():
    robots = "User-agent: *\nDisallow: /reports/"
    source = Source(url="https://health.example.gov/reports/ncd.pdf", title="NCD", dimension="policy")
    assessed = await CrawlabilityAgent().assess(_StubClient(200, robots), source)
    assert assessed.crawl_allowed is False


@pytest.mark.asyncio
async def test_crawl_gate_refuses_unsupported_schemes():
    source = Source(url="ftp://example.org/data", title="Data", dimension="policy")
    assessed = await CrawlabilityAgent().assess(client=None, source=source)
    assert assessed.crawl_allowed is False


def test_every_source_starts_undecided():
    """crawl_allowed must be None until the gate runs, so 'not yet checked'
    can never be confused with 'permitted'."""
    assert Source(url="https://example.org", title="x", dimension="burden").crawl_allowed is None


# --------------------------------------------------------------------------- #
# The workflow really is the graph we describe
# --------------------------------------------------------------------------- #

def test_workflow_exposes_every_stage():
    node_ids = {node["id"] for node in workflow_structure()["nodes"]}
    assert node_ids == {
        "intake", "plan", "discover", "crawl_gate", "extract",
        "claim_extraction", "fact_check", "sufficiency", "index", "report",
    }


def test_crawl_gate_precedes_extraction_in_the_graph():
    edges = {(edge["source"], edge["target"]) for edge in workflow_structure()["edges"]}
    assert ("crawl_gate", "extract") in edges
    assert ("discover", "crawl_gate") in edges


def test_fact_check_is_a_separate_stage_from_claim_extraction():
    edges = {(edge["source"], edge["target"]) for edge in workflow_structure()["edges"]}
    assert ("claim_extraction", "fact_check") in edges


def test_sufficiency_can_send_the_run_back_for_another_round():
    structure = workflow_structure()
    conditional = {(edge["source"], edge["target"]) for edge in structure["edges"] if edge["conditional"]}
    assert ("sufficiency", "plan") in conditional
    assert ("sufficiency", "index") in conditional
    assert structure["cycle"]["bounded_by"].startswith("RESEARCH_MAX_ROUNDS")


def test_dimensions_are_the_committed_definition_of_understanding_a_city():
    assert DIMENSION_KEYS == ["burden", "programmes", "policy", "access", "actors"]


# --------------------------------------------------------------------------- #
# PDF extraction
# --------------------------------------------------------------------------- #

def _build_pdf(text: str) -> bytes:
    """Assemble a minimal but structurally valid PDF, xref included."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>",
        b"<</Length %d>>stream\n%s\nendstream" % (len(stream), stream),
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref_at = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer<</Size %d/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref_at)
    return bytes(out)


def test_clean_pdf_extracts_a_text_layer():
    assert "Hypertension prevalence" in clean_pdf(_build_pdf("Hypertension prevalence in the city"))


def test_clean_pdf_returns_empty_when_there_is_no_text_layer():
    """A scanned report has no text layer. Returning empty lets the extractor
    record it as a gap needing OCR rather than inventing content."""
    assert clean_pdf(_build_pdf("")).strip() == ""


# --------------------------------------------------------------------------- #
# What the model is shown
# --------------------------------------------------------------------------- #

FRONT_MATTER = "Volume 12 issue 4 published by the society. Copyright notice applies to this article. " * 40
FINDING = "Nairobi City County reported hypertension prevalence among adults of 24.5 percent in 2019. "
BACK_MATTER = "Acknowledgements and funding statements follow. The reference list continues below. " * 40


def test_relevant_window_keeps_a_finding_that_truncation_would_lose():
    document = FRONT_MATTER + FINDING + BACK_MATTER
    assert "hypertension prevalence" not in document[:1500], "fixture must defeat first-N truncation"
    assert "hypertension prevalence" in relevant_window(document, "Nairobi", "Kenya", 1500)


def test_relevant_window_returns_short_documents_unchanged():
    assert relevant_window(FINDING, "Nairobi", "Kenya", 6000) == FINDING


def test_relevant_window_only_ever_removes_text():
    """Every sentence in the window must come from the document, so windowing
    cannot introduce anything for grounding to miss."""
    document = FRONT_MATTER + FINDING + BACK_MATTER
    for part in relevant_window(document, "Nairobi", "Kenya", 1500).split("\n\n"):
        assert part in document


# --------------------------------------------------------------------------- #
# Credentials
# --------------------------------------------------------------------------- #

def test_openai_key_is_never_sent_to_another_provider(monkeypatch):
    from backend.app.config import Settings

    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-secret")
    monkeypatch.setenv("LLM_BASE_URL", "https://api.groq.com/openai/v1")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    assert Settings().openai_api_key is None


def test_openai_key_is_used_when_talking_to_openai(monkeypatch):
    from backend.app.config import Settings

    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-secret")
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    assert Settings().openai_api_key == "sk-openai-secret"


def test_rate_limits_are_retried_but_exhausted_quota_is_not():
    from backend.app.llm import classify

    class RateLimitError(Exception):
        def __init__(self, body, headers=None):
            self.body = body
            self.status_code = 429
            self.response = type("Response", (), {"headers": headers or {}})()

    assert classify(RateLimitError({"error": {"code": "insufficient_quota"}}))[0] == "terminal"
    kind, wait = classify(RateLimitError({"error": {"code": "rate_limit_exceeded"}}, {"retry-after": "7"}))
    assert (kind, wait) == ("rate_limit", 7.0)


def test_groq_throttle_with_upgrade_link_is_not_mistaken_for_exhausted_credit():
    """Regression: Groq's per-minute throttle message ends with a link to its
    billing page. Matching on that word opened the circuit and silently switched
    the fact checker to rules for the rest of the run."""
    from backend.app.llm import classify

    class RateLimitError(Exception):
        status_code = 429

        def __init__(self):
            self.body = {"error": {
                "code": "rate_limit_exceeded",
                "type": "tokens",
                "message": "Rate limit reached for model `openai/gpt-oss-20b` on tokens per minute (TPM): "
                           "Limit 8000, Used 6455, Requested 6455. Please try again in 36.825s. "
                           "Need more tokens? Upgrade to Dev Tier today at https://console.groq.com/settings/billing",
            }}
            self.response = type("Response", (), {"headers": {}})()

    kind, wait = classify(RateLimitError())
    assert kind == "rate_limit"
    assert wait == pytest.approx(36.825)


def test_provider_rejected_json_is_retried_rather_than_treated_as_an_outage():
    from backend.app.llm import classify

    class BadRequestError(Exception):
        status_code = 400

        def __init__(self):
            self.body = {"error": {"code": "json_validate_failed", "message": "Failed to validate JSON."}}

    assert classify(BadRequestError())[0] == "invalid_output"


@pytest.mark.asyncio
async def test_a_throttle_seen_by_one_caller_pauses_every_caller_for_that_model():
    """Regression: uncoordinated backoff let concurrent calls collide with the
    rate limit over and over. One 429 must hold back all calls to that model,
    and only that model."""
    import time
    from backend.app.llm import ModelPacer

    pacer = ModelPacer(concurrency=2)
    pacer.cool_down("model-a", 0.3)
    started = time.monotonic()
    await pacer.wait_turn("model-b")
    assert time.monotonic() - started < 0.05, "an unrelated model must not wait"
    await pacer.wait_turn("model-a")
    assert time.monotonic() - started >= 0.28


def test_progress_never_moves_backwards_when_a_second_round_starts():
    """Regression: a follow-up round re-entered stages with lower percentages
    and the bar jumped from 66% back to 12%."""
    from backend.app.workflow import ACTIVE_PROGRESS, _progress

    _progress("Progressville", "Checking the request", "", 5)
    _progress("Progressville", "Pulling out findings", "", 66)
    _progress("Progressville", "Planning the research", "", 12, round_number=2)
    assert ACTIVE_PROGRESS["progressville"]["percent"] >= 66
    assert ACTIVE_PROGRESS["progressville"]["stage"].startswith("Round 2")
    _progress("Progressville", "Reading sources", "", 52, round_number=2)
    assert ACTIVE_PROGRESS["progressville"]["percent"] >= 70
    ACTIVE_PROGRESS.pop("progressville")


def test_research_returns_immediately_and_reports_the_outcome_by_polling(monkeypatch):
    """Regression: the browser held one request open for a multi-minute run, so
    any interruption lost the result. Starting research must return at once."""
    import asyncio
    from fastapi.testclient import TestClient
    from backend.app import research as research_api
    from backend.app.main import app

    release = asyncio.Event()

    async def slow_run(city, country, depth=None):
        await release.wait()
        raise ValueError("City name failed target validation")

    monkeypatch.setattr(research_api, "run_research", slow_run)
    research_api.RESEARCH_JOBS.clear()
    with TestClient(app) as client:
        started = client.post("/api/research", json={"city": "Polltown", "country": "Testland"})
        assert started.status_code == 202
        assert started.json()["status"] == "running"
        again = client.post("/api/research", json={"city": "Polltown"})
        assert again.json()["started_at"] == started.json()["started_at"], "a second click joins the running job"
        client.portal.call(release.set)
        for _ in range(50):
            status = client.get("/api/research/Polltown").json()
            if status["status"] != "running":
                break
            client.portal.call(asyncio.sleep, 0.02)
        assert status["status"] == "failed"
        assert "validation" in status["error"]
    research_api.RESEARCH_JOBS.clear()


def test_research_rejects_an_invalid_city_before_starting_anything():
    from fastapi.testclient import TestClient
    from backend.app.main import app

    with TestClient(app) as client:
        assert client.post("/api/research", json={"city": "<script>"}).status_code == 422


def test_a_throttled_call_moves_to_the_pool_model_that_is_free_soonest():
    from backend.app.llm import ModelPacer

    pacer = ModelPacer(concurrency=2)
    pool = ["model-a", "model-b", "model-c"]
    assert pacer.best(pool) == "model-a"
    pacer.cool_down("model-a", 30)
    pacer.cool_down("model-b", 5)
    assert pacer.best(pool) == "model-c", "an idle model beats waiting on a throttled one"
    pacer.cool_down("model-c", 60)
    assert pacer.best(pool) == "model-b", "when all are throttled, take the one that frees first"


@pytest.mark.asyncio
async def test_a_claim_is_never_checked_by_the_model_that_extracted_it(monkeypatch):
    from backend.app.agents import factcheck
    from backend.app.ontology import Claim

    seen = {}

    async def fake_routed(system, user, *, models, **kwargs):
        seen["models"] = models
        return {"verdict": "SUPPORTED", "confidence": 0.9, "reason": "ok", "actual_scope": "city"}, models[0]

    monkeypatch.setattr(factcheck.language_model, "json_call_routed", fake_routed)
    monkeypatch.setattr(factcheck.settings, "llm_fast_model", "checker-x")
    monkeypatch.setattr(factcheck.settings, "graph_model", "graph-y")
    monkeypatch.setattr(factcheck.settings, "llm_extraction_models", ["checker-x", "graph-y", "extractor-z"])
    claim = Claim(claim="Nairobi County runs screening in clinics.", dimension="programmes",
                  evidence_quote="Nairobi County runs screening in clinics across the city.",
                  source_url="https://example.org", source_title="Report", extractor="llm:checker-x")
    fact, _ = await factcheck.check_claim(claim, "Nairobi", "Kenya", claim.evidence_quote)
    assert "checker-x" not in seen["models"]
    assert fact.verified_by != claim.extractor


def test_quick_depth_reads_fewer_sources_and_never_starts_a_second_round():
    """Quick trades thoroughness for time. Reading fewer sources makes a
    shortfall in coverage more likely, so it must also forbid the second round
    that would otherwise add the time straight back."""
    from backend.app.profiles import PROFILES, get_profile

    quick, balanced, thorough = PROFILES["quick"], PROFILES["balanced"], PROFILES["thorough"]
    assert quick.max_rounds == 1
    assert quick.max_fetches_per_round < balanced.max_fetches_per_round < thorough.max_fetches_per_round
    assert get_profile("nonsense").key == "balanced"
    assert get_profile(None).key == "balanced"


def test_the_generation_key_is_never_sent_to_a_different_embedding_provider(monkeypatch):
    from backend.app.config import Settings

    monkeypatch.setenv("LLM_BASE_URL", "https://api.groq.com/openai/v1")
    monkeypatch.setenv("LLM_API_KEY", "gsk-groq-secret")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "api")
    monkeypatch.setenv("EMBEDDING_BASE_URL", "https://embeddings.example.com/v1")
    monkeypatch.delenv("EMBEDDING_API_KEY", raising=False)
    settings = Settings()
    assert settings.embedding_api_key is None
    assert settings.embeddings_configured is False, "without its own key the hosted embedder is not used"

    monkeypatch.setenv("EMBEDDING_API_KEY", "embed-secret")
    assert Settings().embedding_api_key == "embed-secret"


def test_frontend_and_backend_release_versions_match():
    """Regression: a cached old app.js ran against a new server and failed with
    "Cannot read properties of undefined (reading 'length')"."""
    import re
    from pathlib import Path
    from backend.app.version import APP_VERSION

    root = Path(__file__).resolve().parents[1]
    index = (root / "frontend/index.html").read_text()
    script = (root / "frontend/app.js").read_text()
    assert f'app.js?v={APP_VERSION}' in index
    assert f'styles.css?v={APP_VERSION}' in index
    assert re.search(r'const APP_VERSION = "([^"]+)"', script).group(1) == APP_VERSION


def test_app_shell_is_revalidated_on_every_load():
    from fastapi.testclient import TestClient
    from backend.app.main import app

    with TestClient(app) as client:
        for path in ("/", "/app.js", "/styles.css"):
            assert client.get(path).headers.get("cache-control") == "no-cache", path
        assert client.get("/health").json()["version"]
