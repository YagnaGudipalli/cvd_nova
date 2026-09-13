"""What "understanding a city" is decomposed into, and how it is modelled.

Two things live here:

* The **research dimensions** — the committed answer to the open question "what
  does understanding a city actually consist of?". Planning, sufficiency and
  coverage reporting are all expressed against this list, so the definition is
  enforced by the workflow rather than described in a document.
* The **graph ontology** — typed entities handed to Graphiti so that a city's
  memory is a structured network of organisations, programmes, policies and
  indicators, not an undifferentiated blob of extracted nouns.
"""

from typing import Literal

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------- #
# Research dimensions
# --------------------------------------------------------------------------- #

class Dimension(BaseModel):
    key: str
    label: str
    question: str
    seed_terms: list[str]


DIMENSIONS: list[Dimension] = [
    Dimension(
        key="burden",
        label="Disease burden and risk factors",
        question="What is the cardiovascular disease burden in this city, and how prevalent are hypertension, type 2 diabetes and dyslipidaemia?",
        seed_terms=["cardiovascular disease prevalence", "hypertension prevalence", "diabetes prevalence statistics"],
    ),
    Dimension(
        key="programmes",
        label="Health programmes and interventions",
        question="Which health programmes, screening initiatives or interventions already operate in this city, and who runs them?",
        seed_terms=["hypertension screening programme", "NCD prevention initiative", "public health programme"],
    ),
    Dimension(
        key="policy",
        label="Policy and governance",
        question="Which municipal or national policies, strategies and regulations shape cardiovascular health here?",
        seed_terms=["health policy strategy", "noncommunicable disease action plan", "municipal health department"],
    ),
    Dimension(
        key="access",
        label="Healthcare access and delivery",
        question="What does access to primary care, diagnosis, treatment and medication look like in this city?",
        seed_terms=["primary healthcare access", "hospitals clinics health facilities", "health workforce"],
    ),
    Dimension(
        key="actors",
        label="Institutions and stakeholders",
        question="Which organisations, institutions and agencies are active in cardiovascular or public health in this city?",
        seed_terms=["health department ministry", "university research hospital", "NGO public health partnership"],
    ),
]

DIMENSION_KEYS = [dimension.key for dimension in DIMENSIONS]
DIMENSION_LABELS = {dimension.key: dimension.label for dimension in DIMENSIONS}


# --------------------------------------------------------------------------- #
# Graph ontology handed to Graphiti
# --------------------------------------------------------------------------- #

# These types deliberately carry no fields. Graphiti uses each type's name and
# docstring to classify entities, and the name becomes a Neo4j label — which is
# what makes "who runs what" a traversal. Typed attribute fields were tried and
# removed: this Graphiti release writes them as a nested map property, which
# Neo4j rejects ("Property values can only be of primitive types"), so every
# episode containing a typed entity failed to write.

class Organization(BaseModel):
    """A government body, ministry, municipal or county department, hospital, clinic network, university, NGO, insurer or company."""


class Programme(BaseModel):
    """A named health programme, screening drive, campaign, clinic service or intervention, whether announced, running or completed."""


class Policy(BaseModel):
    """A law, regulation, strategy, action plan or clinical guideline at municipal, regional, national or international level."""


class HealthIndicator(BaseModel):
    """A measured health statistic such as a prevalence, incidence, mortality or control rate, including its population and year when stated."""


class Place(BaseModel):
    """A city, county, district, region or country."""


GRAPH_ENTITY_TYPES = {
    "Organization": Organization,
    "Programme": Programme,
    "Policy": Policy,
    "HealthIndicator": HealthIndicator,
    "Place": Place,
}


# --------------------------------------------------------------------------- #
# Workflow payloads
# --------------------------------------------------------------------------- #

GeographicScope = Literal["city", "regional", "national", "international", "unknown"]
Verdict = Literal["SUPPORTED", "PARTIALLY_SUPPORTED", "SCOPE_MISMATCH", "UNSUPPORTED"]


class PlannedQuery(BaseModel):
    dimension: str
    query: str
    rationale: str = ""
    round: int = 1


class Source(BaseModel):
    url: str
    title: str
    snippet: str = ""
    #: Where the content is actually retrieved from, when a provider offers a
    #: sanctioned programmatic endpoint (for example Europe PMC's full-text API)
    #: that differs from the human-readable citation URL.
    fetch_url: str | None = None
    dimension: str = "burden"
    discovered_via: str = ""
    crawl_allowed: bool | None = None
    crawlability_reason: str = ""
    crawl_rule: str = ""
    crawl_delay: float | None = None
    fetched: bool = False
    retrieved_at: str | None = None
    content_type: str | None = None  # html | pdf, set when the source is read


class Document(BaseModel):
    url: str
    title: str
    dimension: str
    text: str
    retrieved_at: str
    processed: bool = False


class Claim(BaseModel):
    """A candidate statement, before any independent check has been applied."""

    claim: str
    dimension: str
    evidence_quote: str
    geographic_scope: GeographicScope = "unknown"
    source_url: str
    source_title: str
    entities: list[dict[str, str]] = Field(default_factory=list)
    extractor: str = "llm"
    quote_verified: bool = False
    checked: bool = False


class VerifiedFact(BaseModel):
    """A claim that survived the independent evidence gate."""

    claim: str
    dimension: str
    dimension_label: str = ""
    evidence_quote: str
    geographic_scope: GeographicScope = "unknown"
    scope_flag: str | None = None
    source_url: str
    source_title: str
    entities: list[dict[str, str]] = Field(default_factory=list)
    verification_status: Verdict = "SUPPORTED"
    verification_note: str = ""
    verified_by: str = "rules"
    confidence: float = 0.5
    research_city: str = ""
    retrieved_at: str | None = None


class Gap(BaseModel):
    """Something the system does not know, and says so."""

    topic: str
    dimension: str = ""
    reason: str
    source_url: str | None = None
    kind: str = "missing"  # missing | withheld | blocked | unreachable
