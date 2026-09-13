"""Single source of truth for runtime configuration.

Every provider is optional. The system is designed to run with none of them
configured, with a visible and honest reduction in capability rather than a
crash, because a demonstration that dies on a missing key teaches nobody
anything about the architecture.
"""

import os
from dataclasses import dataclass, field


def _flag(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


@dataclass
class Settings:
    """Configuration read once at import time."""

    # Language model.
    #
    # Nothing here is OpenAI-specific beyond the wire format. Any server that
    # speaks the OpenAI chat-completions and embeddings API works: Ollama or
    # vLLM running an open-weights model locally, or a hosted gateway such as
    # Groq, Together, OpenRouter or DeepInfra. Point LLM_BASE_URL at it and name
    # the model. The only hard requirement is JSON-mode output, because every
    # agent in this system exchanges structured JSON rather than prose.
    #: The OpenAI key is only ever sent to OpenAI. When LLM_BASE_URL points at
    #: another provider, only LLM_API_KEY is used — otherwise an OpenAI secret
    #: would be transmitted to a third party's servers as a bearer token.
    openai_api_key: str | None = field(
        default_factory=lambda: (
            os.getenv("LLM_API_KEY") or None
            if os.getenv("LLM_BASE_URL")
            else os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY") or None
        )
    )
    llm_base_url: str | None = field(default_factory=lambda: os.getenv("LLM_BASE_URL") or None)
    llm_model: str = field(default_factory=lambda: os.getenv("LLM_MODEL", "gpt-4.1-mini"))
    #: A cheaper, faster model for the short structured tasks — planning,
    #: verification and answer synthesis. Claim extraction reads whole
    #: documents and keeps the quality model. Defaults to the same model.
    #: Claim extraction is the throughput bottleneck: each call requests ~3,500
    #: tokens, and a free-tier model allows 8,000 per minute, so one model clears
    #: about two sources a minute. Limits are metered per model, and during
    #: extraction the checker and graph models are idle, so extraction rotates
    #: across this pool. Comma-separated; defaults to LLM_MODEL alone.
    llm_extraction_models: list[str] = field(
        default_factory=lambda: [
            name.strip()
            for name in (os.getenv("LLM_EXTRACTION_MODELS") or os.getenv("LLM_MODEL", "gpt-4.1-mini")).split(",")
            if name.strip()
        ]
    )
    llm_fast_model: str = field(default_factory=lambda: os.getenv("LLM_FAST_MODEL") or os.getenv("LLM_MODEL", "gpt-4.1-mini"))
    #: Graphiti makes several structured calls per episode (entity extraction,
    #: deduplication, relationship extraction). A separate model keeps that work
    #: in its own tokens-per-minute budget instead of starving claim extraction.
    graph_model: str = field(default_factory=lambda: os.getenv("GRAPH_MODEL") or os.getenv("LLM_MODEL", "gpt-4.1-mini"))
    graph_fast_model: str = field(
        default_factory=lambda: os.getenv("GRAPH_FAST_MODEL") or os.getenv("LLM_FAST_MODEL") or os.getenv("LLM_MODEL", "gpt-4.1-mini")
    )
    #: Upper bound on episodes written per run; facts are grouped by source.
    graph_max_episodes: int = field(default_factory=lambda: _int("GRAPH_MAX_EPISODES", 8))
    #: Hosted free tiers limit tokens per minute, not just requests. Bounding
    #: concurrency keeps a research round from bursting straight into a 429.
    llm_max_concurrency: int = field(default_factory=lambda: _int("LLM_MAX_CONCURRENCY", 4))
    #: Reasoning models (gpt-oss) spend output tokens thinking before answering.
    #: For a four-field verdict, "low" measured 3x fewer tokens and 3.4x faster
    #: with no invalid outputs; at the default effort, thinking sometimes used the
    #: whole budget and returned nothing. Raise it if verdicts look too lenient.
    checker_reasoning_effort: str = field(default_factory=lambda: os.getenv("CHECKER_REASONING_EFFORT", "low"))
    embedding_model: str = field(default_factory=lambda: os.getenv("EMBEDDING_MODEL", "text-embedding-3-small"))
    embedding_base_url: str | None = field(
        default_factory=lambda: os.getenv("EMBEDDING_BASE_URL") or os.getenv("LLM_BASE_URL") or None
    )
    #: ``fastembed`` runs a small open embedding model in-process on ONNX: no
    #: server, no key, no GPU, and it works on older macOS where Ollama does not.
    #: ``api`` sends embeddings to EMBEDDING_BASE_URL (or OpenAI).
    #: Credential for a hosted embedding provider. Kept separate from the chat
    #: key: when embeddings and generation are different providers, one
    #: provider's key must never be sent to the other.
    embedding_api_key_env: str | None = field(default_factory=lambda: os.getenv("EMBEDDING_API_KEY") or None)
    openai_platform_key: str | None = field(default_factory=lambda: os.getenv("OPENAI_API_KEY") or None)
    embedding_provider: str = field(default_factory=lambda: os.getenv("EMBEDDING_PROVIDER", "fastembed").strip().lower())
    #: Open-weights embedders differ in width (nomic-embed-text is 768,
    #: mxbai-embed-large is 1024). Declaring it keeps the Qdrant collection name
    #: correct; if it is wrong the first index call corrects it from the response.
    embedding_dimensions: int = field(default_factory=lambda: _int("EMBEDDING_DIMENSIONS", 0))

    # Vector store
    qdrant_url: str | None = field(default_factory=lambda: os.getenv("QDRANT_URL") or None)
    qdrant_api_key: str | None = field(default_factory=lambda: os.getenv("QDRANT_API_KEY") or None)
    qdrant_collection_prefix: str = field(default_factory=lambda: os.getenv("QDRANT_COLLECTION", "cardio4cities_evidence"))

    # Graph store
    neo4j_uri: str | None = field(default_factory=lambda: os.getenv("NEO4J_URI") or None)
    neo4j_username: str | None = field(default_factory=lambda: os.getenv("NEO4J_USERNAME") or None)
    neo4j_password: str | None = field(default_factory=lambda: os.getenv("NEO4J_PASSWORD") or None)
    neo4j_database: str = field(default_factory=lambda: os.getenv("NEO4J_DATABASE", "neo4j") or "neo4j")
    graphiti_enabled: bool = field(default_factory=lambda: _flag("GRAPHITI_ENABLED"))

    # Research budget. These are the knobs that trade depth against demo latency.
    max_rounds: int = field(default_factory=lambda: _int("RESEARCH_MAX_ROUNDS", 2))
    queries_per_round: int = field(default_factory=lambda: _int("RESEARCH_QUERIES_PER_ROUND", 5))
    results_per_query: int = field(default_factory=lambda: _int("RESEARCH_RESULTS_PER_QUERY", 5))
    max_fetches_per_round: int = field(default_factory=lambda: _int("RESEARCH_MAX_FETCHES", 10))
    #: Concurrent fetches to one host when it publishes no crawl-delay.
    per_host_concurrency: int = field(default_factory=lambda: _int("RESEARCH_PER_HOST_CONCURRENCY", 3))
    max_claims_per_source: int = field(default_factory=lambda: _int("RESEARCH_MAX_CLAIMS_PER_SOURCE", 5))
    document_char_budget: int = field(default_factory=lambda: _int("RESEARCH_DOC_CHARS", 14000))
    #: How much of a document is shown to the claim extractor. Hosted free tiers
    #: meter tokens per minute, and the opening of a document is usually its
    #: least useful part — journal front matter, navigation, author affiliations.
    llm_document_chars: int = field(default_factory=lambda: _int("LLM_DOC_CHARS", 6000))
    http_timeout_seconds: float = field(default_factory=lambda: float(_int("RESEARCH_HTTP_TIMEOUT", 20)))
    sufficiency_threshold: int = field(default_factory=lambda: _int("RESEARCH_SUFFICIENCY_DIMENSIONS", 3))

    user_agent: str = field(
        default_factory=lambda: os.getenv(
            "RESEARCH_USER_AGENT",
            "CARDIO4CitiesResearchBot/1.0 (+https://www.cardio4cities.org/; public-health research; respects robots.txt)",
        )
    )

    @property
    def llm_configured(self) -> bool:
        """A local server needs no key, so a base URL is sufficient on its own."""
        return bool(self.openai_api_key or self.llm_base_url)

    @property
    def embedding_api_key(self) -> str | None:
        """The key sent to the embedding endpoint, and only ever its own."""
        if self.embedding_api_key_env:
            return self.embedding_api_key_env
        if not self.embedding_base_url:
            return self.openai_platform_key  # OpenAI itself
        if self.embedding_base_url == self.llm_base_url:
            return self.openai_api_key  # same provider as generation
        return None  # a different provider never receives the generation key

    @property
    def embeddings_configured(self) -> bool:
        """Embeddings run independently of the chat model: in-process, or on their own endpoint."""
        if self.embedding_provider == "fastembed":
            return True
        local = bool(self.embedding_base_url and any(host in self.embedding_base_url for host in ("localhost", "127.0.0.1")))
        return bool(self.embedding_api_key) or local

    @property
    def llm_provider(self) -> str:
        if not self.llm_base_url:
            return "openai"
        host = self.llm_base_url.lower()
        for marker, name in (
            ("localhost", "local"), ("127.0.0.1", "local"), ("11434", "ollama"),
            ("groq", "groq"), ("together", "together"), ("openrouter", "openrouter"),
            ("deepinfra", "deepinfra"), ("fireworks", "fireworks"),
        ):
            if marker in host:
                return name
        return "openai-compatible"

    @property
    def qdrant_configured(self) -> bool:
        return bool(self.qdrant_url and self.qdrant_api_key)

    @property
    def neo4j_configured(self) -> bool:
        return bool(self.neo4j_uri and self.neo4j_username and self.neo4j_password)

    @property
    def graph_configured(self) -> bool:
        """Graphiti needs both a graph to write to and a model to extract with."""
        return bool(self.graphiti_enabled and self.neo4j_configured and self.llm_configured)

    def missing(self, provider: str) -> list[str]:
        checks = {
            "llm": (("OPENAI_API_KEY or LLM_BASE_URL", self.openai_api_key or self.llm_base_url),),
            "qdrant": (("QDRANT_URL", self.qdrant_url), ("QDRANT_API_KEY", self.qdrant_api_key)),
            "graph": (
                ("GRAPHITI_ENABLED=true", self.graphiti_enabled),
                ("NEO4J_URI", self.neo4j_uri),
                ("NEO4J_USERNAME", self.neo4j_username),
                ("NEO4J_PASSWORD", self.neo4j_password),
                ("OPENAI_API_KEY or LLM_BASE_URL", self.openai_api_key or self.llm_base_url),
            ),
        }
        return [name for name, value in checks.get(provider, ()) if not value]


settings = Settings()
