"""Language-model boundary.

The system is not tied to OpenAI. This module speaks the OpenAI chat-completions
and embeddings wire format, which Ollama, vLLM, llama.cpp, LM Studio, Groq,
Together, OpenRouter and DeepInfra all implement. Set ``LLM_BASE_URL`` and
everything downstream follows, Graphiti included.

The one real requirement is reliable JSON-mode output: agents exchange structured
objects, not prose. A model that cannot hold the schema degrades to the
deterministic path instead of corrupting the ledger.

Everything that talks to a model goes through here, so that:

1. **Degradation is centralised.** Callers receive ``None`` and take a
   deterministic path. They never treat "no model" as "assume the answer".
2. **Transient and terminal failures are told apart.** A provider throttling
   requests per minute is waited out; an exhausted quota or a rejected key is
   taken out of service. The OpenAI SDK raises the same exception class for
   both, so the distinction is made from the error body.
3. **Chat and embeddings fail independently.** They often run on different
   endpoints — hosted generation, local embeddings — and one being throttled
   must not switch the other off.
4. **One embedding function serves indexing and querying**, and the vector
   collection is named after its signature, so a dimension mismatch cannot
   happen.
"""

import asyncio
import hashlib
import json
import logging
import re
import time
from typing import Any, Literal

from openai import AsyncOpenAI

from .config import settings


logger = logging.getLogger("cardio4cities.llm")

FALLBACK_DIMENSIONS = 384
Tier = Literal["quality", "fast"]

#: Structured error codes meaning "retrying will not help until a human acts".
TERMINAL_CODES = {
    "insufficient_quota", "credit_balance_exhausted", "invalid_api_key",
    "account_deactivated", "organization_restricted", "billing_hard_limit_reached",
}
RATE_LIMIT_CODES = {"rate_limit_exceeded", "tokens", "requests"}
#: The provider rejected the model's own output as invalid JSON. Sampling again
#: usually succeeds; giving up would silently drop that claim to the rules path.
INVALID_OUTPUT_CODES = {"json_validate_failed"}
TERMINAL_CLASSES = ("AuthenticationError", "PermissionDeniedError", "NotFoundError")

#: Providers state how long to wait. Groq's per-minute token window routinely
#: asks for 30-40 seconds; retrying sooner just earns another 429.
MAX_RETRY_WAIT_SECONDS = 65.0
#: Throttling is retried against a time budget, not a fixed count. A throttled
#: call is not a failed call: giving up after three collisions used to drop
#: most fact checks to the rules path in a busy round, silently lowering the
#: quality of verification. Only a call that is still throttled after this long
#: falls back.
RATE_LIMIT_BUDGET_SECONDS = 300.0
MAX_ATTEMPTS = 12


class ModelPacer:
    """Per-model concurrency and a shared cooldown.

    Tokens-per-minute limits are metered per model. Without coordination every
    concurrent caller discovers the limit separately, backs off separately and
    collides again when the window reopens — a thundering herd that once cost 75
    rejected requests to land 16. Here the first 429 sets a cooldown that every
    caller for that model honours before sending, so the window is spent on work.
    """

    def __init__(self, concurrency: int) -> None:
        self._concurrency = max(1, concurrency)
        self._semaphores: dict[str, asyncio.Semaphore] = {}
        self._resume_at: dict[str, float] = {}
        self._in_flight: dict[str, int] = {}

    def semaphore(self, model: str) -> asyncio.Semaphore:
        if model not in self._semaphores:
            self._semaphores[model] = asyncio.Semaphore(self._concurrency)
        return self._semaphores[model]

    async def wait_turn(self, model: str) -> None:
        delay = self._resume_at.get(model, 0.0) - time.monotonic()
        if delay > 0:
            await asyncio.sleep(delay)

    def resume_in(self, model: str) -> float:
        return max(0.0, self._resume_at.get(model, 0.0) - time.monotonic())

    def in_flight(self, model: str) -> int:
        return self._in_flight.get(model, 0)

    def best(self, models: list[str]) -> str:
        """The pool model that can take a call soonest: not cooling down, least busy."""
        return min(models, key=lambda name: (self.resume_in(name), self.in_flight(name), models.index(name)))

    def cool_down(self, model: str, seconds: float) -> None:
        resume = time.monotonic() + seconds
        if resume > self._resume_at.get(model, 0.0):
            self._resume_at[model] = resume


class Circuit:
    """Half-open circuit breaker for one endpoint.

    Opens on a terminal failure. After ``cooldown`` seconds it lets a single
    call through: success closes it, failure re-opens it. So a topped-up quota
    or a fixed key recovers without a restart.
    """

    def __init__(self, name: str, cooldown: float = 120.0) -> None:
        self.name = name
        self.cooldown = cooldown
        self.opened_at: float | None = None
        self.reason: str | None = None

    @property
    def blocking(self) -> bool:
        return self.opened_at is not None and time.monotonic() - self.opened_at < self.cooldown

    def open(self, reason: str) -> None:
        if self.opened_at is None:
            logger.error("circuit_open endpoint=%s reason=%s", self.name, reason)
        self.opened_at = time.monotonic()
        self.reason = reason

    def close(self) -> None:
        if self.opened_at is not None:
            logger.info("circuit_closed endpoint=%s", self.name)
        self.opened_at = None
        self.reason = None


def _error_fields(error: Exception) -> tuple[str, str]:
    """Pull (code, message) out of an SDK error, whatever shape its body has."""
    body = getattr(error, "body", None)
    detail = body.get("error", body) if isinstance(body, dict) else {}
    if not isinstance(detail, dict):
        detail = {}
    code = str(detail.get("code") or detail.get("type") or "").lower()
    message = str(detail.get("message") or body or error)
    return code, message


def classify(error: Exception) -> tuple[str, float | None]:
    """Return (kind, retry_after). Kind is ``terminal``, ``rate_limit`` or ``transient``.

    Decided from the provider's structured error code, never from words in the
    message. Groq's throttling message ends with an upgrade link to its billing
    page; matching the word "billing" once turned every per-minute throttle into
    a terminal outage and silently switched the fact checker off.
    """
    code, message = _error_fields(error)
    status = getattr(error, "status_code", None)
    if code in TERMINAL_CODES or type(error).__name__ in TERMINAL_CLASSES:
        return "terminal", None
    if code in INVALID_OUTPUT_CODES:
        return "invalid_output", None
    if code in RATE_LIMIT_CODES or status == 429 or type(error).__name__ == "RateLimitError":
        return "rate_limit", _retry_after(error, message)
    return "transient", None


def _retry_after(error: Exception, message: str) -> float | None:
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", None) or {}
    for header in ("retry-after", "x-ratelimit-reset-tokens", "x-ratelimit-reset-requests"):
        value = headers.get(header) if hasattr(headers, "get") else None
        seconds = _parse_duration(value) if value else None
        if seconds is not None:
            return seconds
    # Fall back to the prose Groq and OpenAI both include: "try again in 36.825s".
    match = re.search(r"try again in ([\d.]+\s*(?:ms|s|m))", message)
    return _parse_duration(match.group(1).replace(" ", "")) if match else None


def _parse_duration(value: str) -> float | None:
    """Parse ``12``, ``1.5s``, ``250ms`` or Groq's ``1m2.5s`` into seconds."""
    value = value.strip()
    try:
        return float(value)
    except ValueError:
        pass
    total, matched = 0.0, False
    for amount, unit in re.findall(r"([\d.]+)(ms|s|m|h)", value):
        matched = True
        total += float(amount) * {"ms": 0.001, "s": 1, "m": 60, "h": 3600}[unit]
    return total if matched else None


class LanguageModel:
    """Async JSON-mode completions and embeddings with deterministic fallbacks."""

    KNOWN_DIMENSIONS = {
        "text-embedding-3-small": 1536,
        "text-embedding-3-large": 3072,
        "text-embedding-ada-002": 1536,
        "nomic-embed-text": 768,
        "mxbai-embed-large": 1024,
        "all-minilm": 384,
        "bge-m3": 1024,
        "snowflake-arctic-embed": 1024,
    }

    def __init__(self) -> None:
        self._chat_client: AsyncOpenAI | None = None
        self._embed_client: AsyncOpenAI | None = None
        self._local_model = None
        self._local_lock = asyncio.Lock()
        self._model_pacer: ModelPacer | None = None
        #: Calls that ended on the deterministic path despite a configured model,
        #: by purpose. Reported so a degraded brief says so instead of passing
        #: off rule-based checks as model-verified.
        self.fallbacks: dict[str, int] = {}
        self.chat_circuit = Circuit("chat")
        self.embed_circuit = Circuit("embeddings")
        self._observed_dimensions: int | None = None
        self.last_error: str | None = None

    # ------------------------------------------------------------------ #
    # State
    # ------------------------------------------------------------------ #

    @property
    def configured(self) -> bool:
        return settings.llm_configured and not self.chat_circuit.blocking

    @property
    def degraded(self) -> bool:
        """Configured, but currently out of service."""
        return settings.llm_configured and self.chat_circuit.blocking

    @property
    def embeddings_available(self) -> bool:
        return settings.embeddings_configured and not self.embed_circuit.blocking

    def reset_circuit(self) -> None:
        self.chat_circuit.close()
        self.embed_circuit.close()
        self.last_error = None

    # Retained for callers and tests written against the earlier interface.
    @property
    def _circuit_blocking(self) -> bool:
        return self.chat_circuit.blocking

    # ------------------------------------------------------------------ #
    # Clients
    # ------------------------------------------------------------------ #

    def _chat(self) -> AsyncOpenAI:
        if self._chat_client is None:
            self._chat_client = AsyncOpenAI(
                api_key=settings.openai_api_key or "not-required",
                base_url=settings.llm_base_url,
                timeout=120.0,
                max_retries=0,  # retries are owned here, where they can be classified
            )
        return self._chat_client

    def _embeddings(self) -> AsyncOpenAI:
        if settings.embedding_base_url == settings.llm_base_url and settings.llm_configured:
            return self._chat()
        if self._embed_client is None:
            self._embed_client = AsyncOpenAI(
                api_key=settings.embedding_api_key or "not-required",
                base_url=settings.embedding_base_url,
                timeout=60.0,
                max_retries=0,
            )
        return self._embed_client

    def _pacer(self) -> ModelPacer:
        # Created lazily so its semaphores bind to the running event loop.
        if self._model_pacer is None:
            self._model_pacer = ModelPacer(settings.llm_max_concurrency)
        return self._model_pacer

    # ------------------------------------------------------------------ #
    # Chat
    # ------------------------------------------------------------------ #

    def model_for(self, tier: Tier) -> str:
        return settings.llm_fast_model if tier == "fast" else settings.llm_model

    async def json_call(
        self,
        system: str,
        user: str,
        *,
        max_tokens: int = 2000,
        temperature: float = 0.0,
        purpose: str = "generic",
        tier: Tier = "quality",
        schema: dict[str, Any] | None = None,
        reasoning_effort: str | None = None,
        model: str | None = None,
    ) -> dict[str, Any] | None:
        """Run a JSON-mode completion. Returns ``None`` if no usable answer came back."""
        payload, _ = await self.json_call_routed(
            system, user, models=[model or self.model_for(tier)], max_tokens=max_tokens, temperature=temperature,
            purpose=purpose, schema=schema, reasoning_effort=reasoning_effort,
        )
        return payload

    async def json_call_routed(
        self,
        system: str,
        user: str,
        *,
        models: list[str],
        max_tokens: int = 2000,
        temperature: float = 0.0,
        purpose: str = "generic",
        schema: dict[str, Any] | None = None,
        reasoning_effort: str | None = None,
    ) -> tuple[dict[str, Any] | None, str | None]:
        """Like :meth:`json_call`, over a pool of models. Returns (payload, model used).

        Each model has its own tokens-per-minute limit. A call starts on the pool
        model that is free soonest and, when throttled, moves to another rather
        than waiting: a fixed assignment once left sources queued behind one
        throttled model for minutes while the others sat idle.
        """
        if not settings.llm_configured or self.chat_circuit.blocking or not models:
            return None, None
        pacer = self._pacer()
        model = pacer.best(models)
        throttled_since: float | None = None
        rejected_output = False

        for attempt in range(MAX_ATTEMPTS):
            try:
                async with pacer.semaphore(model):
                    await pacer.wait_turn(model)
                    pacer._in_flight[model] = pacer.in_flight(model) + 1
                    try:
                        response = await self._chat().chat.completions.create(
                            model=model,
                            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                            # With a schema, output is constrained to it by the
                            # provider. Loose json_object mode lets a model emit
                            # malformed JSON, which the provider then rejects.
                            response_format=(
                                {"type": "json_schema", "json_schema": {"name": purpose.replace("-", "_"), "strict": True, "schema": schema}}
                                if schema
                                else {"type": "json_object"}
                            ),
                            temperature=temperature,
                            max_tokens=max_tokens,
                            # Only reasoning models accept this; others reject unknown fields.
                            **({"extra_body": {"reasoning_effort": reasoning_effort}}
                               if reasoning_effort and "gpt-oss" in model else {}),
                        )
                    finally:
                        pacer._in_flight[model] = pacer.in_flight(model) - 1
                payload = json.loads(response.choices[0].message.content or "")
                self.chat_circuit.close()
                self.last_error = None
                return (payload if isinstance(payload, dict) else None), model
            except json.JSONDecodeError:
                # The model is up but could not hold the schema. That says
                # something about the model, not the endpoint: no circuit change.
                if not rejected_output:
                    rejected_output = True
                    logger.info("llm_json_invalid_retry purpose=%s model=%s", purpose, model)
                    continue
                logger.warning("llm_json_invalid purpose=%s model=%s", purpose, model)
                self.last_error = f"{model} returned malformed JSON"
                return self._fell_back(purpose), None
            except Exception as error:
                kind, retry_after = classify(error)
                self.last_error = f"{type(error).__name__} ({kind})"
                if kind == "terminal":
                    self.chat_circuit.open(self.last_error)
                    return self._fell_back(purpose), None
                if kind == "invalid_output" and not rejected_output:
                    rejected_output = True
                    logger.info("llm_output_rejected_retry purpose=%s model=%s", purpose, model)
                    continue
                if kind == "rate_limit":
                    throttled_since = throttled_since or time.monotonic()
                    if time.monotonic() - throttled_since < RATE_LIMIT_BUDGET_SECONDS:
                        wait = min((retry_after + 0.5) if retry_after is not None else 2.0 * (2 ** min(attempt, 4)),
                                   MAX_RETRY_WAIT_SECONDS)
                        pacer.cool_down(model, wait)
                        previous, model = model, pacer.best(models)
                        logger.info("llm_rate_limited purpose=%s model=%s wait=%.1fs next=%s", purpose, previous, wait, model)
                        continue
                logger.warning("llm_call_failed purpose=%s type=%s kind=%s", purpose, type(error).__name__, kind)
                return self._fell_back(purpose), None
        return self._fell_back(purpose), None

    def _fell_back(self, purpose: str) -> None:
        self.fallbacks[purpose] = self.fallbacks.get(purpose, 0) + 1
        return None
        return None

    # ------------------------------------------------------------------ #
    # Embeddings
    # ------------------------------------------------------------------ #

    @property
    def _local_model_name(self) -> str:
        # EMBEDDING_MODEL defaults to an OpenAI model name, which fastembed cannot load.
        name = settings.embedding_model
        return "BAAI/bge-small-en-v1.5" if name.startswith("text-embedding-") else name

    async def _embed_local(self, texts: list[str]) -> list[list[float]]:
        """In-process ONNX embeddings. Loaded once; run off the event loop."""
        async with self._local_lock:
            if self._local_model is None:
                from fastembed import TextEmbedding

                self._local_model = await asyncio.to_thread(TextEmbedding, self._local_model_name)
        model = self._local_model
        return await asyncio.to_thread(lambda: [vector.tolist() for vector in model.embed(texts)])

    async def embed(self, texts: list[str]) -> tuple[list[list[float]], str, int]:
        """Embed ``texts``. Returns (vectors, mode, dimensions)."""
        if not texts:
            mode, dimensions = self.embedding_signature()
            return [], mode, dimensions
        if self.embeddings_available:
            try:
                if settings.embedding_provider == "fastembed":
                    vectors = await self._embed_local(texts)
                    self._observed_dimensions = len(vectors[0])
                    self.embed_circuit.close()
                    return vectors, self._local_model_name, self._observed_dimensions
                response = await self._embeddings().embeddings.create(model=settings.embedding_model, input=texts)
                vectors = [item.embedding for item in response.data]
                self._observed_dimensions = len(vectors[0])
                self.embed_circuit.close()
                return vectors, settings.embedding_model, self._observed_dimensions
            except Exception as error:
                kind, _ = classify(error)
                logger.warning("embedding_failed type=%s kind=%s; using lexical vectors", type(error).__name__, kind)
                # Any embedding failure opens this circuit, because silently
                # mixing semantic and lexical vectors would corrupt retrieval.
                self.embed_circuit.open(f"{type(error).__name__} ({kind})")
        return [lexical_embedding(text) for text in texts], "lexical-hash", FALLBACK_DIMENSIONS

    def embedding_signature(self) -> tuple[str, int]:
        """The (mode, dimensions) :meth:`embed` will produce right now."""
        if not self.embeddings_available:
            return "lexical-hash", FALLBACK_DIMENSIONS
        if settings.embedding_provider == "fastembed":
            name = self._local_model_name
            known = {"BAAI/bge-small-en-v1.5": 384, "BAAI/bge-base-en-v1.5": 768, "BAAI/bge-large-en-v1.5": 1024,
                     "nomic-ai/nomic-embed-text-v1.5": 768, "sentence-transformers/all-MiniLM-L6-v2": 384}
            return name, self._observed_dimensions or settings.embedding_dimensions or known.get(name, 384)
        if self._observed_dimensions:
            return settings.embedding_model, self._observed_dimensions
        if settings.embedding_dimensions:
            return settings.embedding_model, settings.embedding_dimensions
        base = settings.embedding_model.split(":")[0].lower()
        return settings.embedding_model, self.KNOWN_DIMENSIONS.get(base, 1536)


def lexical_embedding(text: str) -> list[float]:
    """Deterministic hashed bag-of-words vector.

    Not semantic, but stable and dependency-free: retrieval keeps working as
    keyword similarity when no embedder is available, instead of returning nothing.
    """
    vector = [0.0] * FALLBACK_DIMENSIONS
    for token in re.findall(r"[a-z0-9]{3,}", text.lower()):
        digest = hashlib.sha256(token.encode()).digest()
        vector[int.from_bytes(digest[:4], "big") % FALLBACK_DIMENSIONS] += 1.0 if digest[4] % 2 else -1.0
    magnitude = sum(value * value for value in vector) ** 0.5 or 1.0
    return [value / magnitude for value in vector]


language_model = LanguageModel()
