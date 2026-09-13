"""The vector and graph halves of the knowledge layer.

Three stores hold three different shapes of knowledge:

* **Relational** (``storage.py``) — the audit record of what the system did.
* **Vector** (Qdrant, here) — evidence passages, so a question asked in the
  user's words can find a source written in someone else's words.
* **Graph** (Graphiti over the Neo4j Sandbox, here) — typed entities and the
  relationships between them, with Graphiti's bi-temporal tracking so a city can
  be re-researched later without destroying what was previously true.

Two decisions in this file matter more than the rest:

1. Indexing and querying both embed through :func:`llm.LanguageModel.embed`, and
   the collection is named after the embedding signature. A vector written with
   one model can therefore never be queried with another.
2. Point identifiers are UUIDv5 of the city, source and claim. Re-running a city
   updates its evidence in place instead of accumulating duplicate copies.
"""

import asyncio
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx
from neo4j import GraphDatabase
from openai import AsyncOpenAI
from qdrant_client import AsyncQdrantClient, models

from .config import settings
from .llm import language_model
from .ontology import GRAPH_ENTITY_TYPES


logger = logging.getLogger("cardio4cities.knowledge")

POINT_NAMESPACE = uuid.UUID("6f9b1f5c-1f4a-4a6e-9f2e-2b7c6d1a8e30")


def city_group_id(city: str) -> str:
    """Graphiti partition key for one city's memory."""
    return f"cardio4cities-{re.sub(r'[^a-z0-9]+', '-', city.lower()).strip('-')}"


def _safe_detail(error: Exception) -> str:
    message = str(error).replace("\n", " ")
    if any(marker in message for marker in ("sk-", "Bearer", "password", "api_key")):
        return "Provider returned an error containing credentials; see the sanitised server logs."
    return message[:240]


class KnowledgeLayer:
    """Provider boundary for the vector and graph stores."""

    def __init__(self) -> None:
        self._graph_jobs: dict[str, dict[str, Any]] = {}
        self._graph_tasks: dict[str, asyncio.Task] = {}
        self._graphiti = None
        self._graphiti_lock = asyncio.Lock()
        self._indices_ready = False

    # ------------------------------------------------------------------ #
    # Configuration and status
    # ------------------------------------------------------------------ #

    def collection_name(self) -> str:
        mode, dimensions = language_model.embedding_signature()
        suffix = re.sub(r"[^a-z0-9]+", "_", mode.lower()).strip("_")
        return f"{settings.qdrant_collection_prefix}_{suffix}_{dimensions}"

    def configuration(self) -> dict[str, Any]:
        return {
            "llm": {
                "configured": settings.llm_configured,
                "missing": settings.missing("llm"),
                "model": settings.llm_model,
                "provider": settings.llm_provider,
                "base_url": settings.llm_base_url or "https://api.openai.com/v1",
            },
            "qdrant": {"configured": settings.qdrant_configured, "missing": settings.missing("qdrant"), "collection": self.collection_name()},
            "graphiti": {"configured": settings.graph_configured, "missing": settings.missing("graph"), "database": settings.neo4j_database},
        }

    def runtime(self) -> dict[str, Any]:
        """What the system can actually do right now, as opposed to what is configured.

        A configured key that has run out of quota is not a working model, and a
        brief produced without one is a different product. The UI reads this so
        it can say so rather than implying capability it does not have.
        """
        from .agents.discovery import configured_providers

        mode, dimensions = language_model.embedding_signature()
        if not settings.llm_configured:
            reasoning = {
                "mode": "deterministic",
                "detail": "No model is configured (set OPENAI_API_KEY, or LLM_BASE_URL for a local or open-weights model). "
                          "Claims are selected verbatim from sources and checked by rules.",
            }
        elif language_model.degraded:
            reasoning = {
                "mode": "deterministic-fallback",
                "detail": f"{settings.llm_model} via {settings.llm_provider} is configured but unavailable "
                          f"({language_model.chat_circuit.reason or language_model.last_error}). "
                          "The run continued with extractive claim selection and rule-based verification.",
            }
        else:
            split = settings.llm_fast_model != settings.llm_model
            reasoning = {
                "mode": "llm",
                "provider": settings.llm_provider,
                "quality_model": settings.llm_model,
                "fast_model": settings.llm_fast_model,
                "detail": (
                    f"Claim extraction uses {settings.llm_model}; planning, verification and answers use "
                    f"{settings.llm_fast_model}, via {settings.llm_provider}."
                    if split
                    else f"Claim extraction, verification and answers use {settings.llm_model} via {settings.llm_provider}."
                ),
            }
        return {
            "reasoning": reasoning,
            "embeddings": {
                "mode": mode,
                "dimensions": dimensions,
                "collection": self.collection_name(),
                "endpoint": settings.embedding_base_url or "openai",
                "status": "degraded" if language_model.embed_circuit.blocking else "ok",
                "reason": language_model.embed_circuit.reason,
            },
            "discovery": configured_providers(),
        }

    def status(self) -> dict[str, dict[str, Any]]:
        mode, dimensions = language_model.embedding_signature()
        return {
            "relational": {
                "provider": "SQLite run ledger",
                "role": "Audit record: runs, sources, crawl decisions, facts, gaps, entities, metrics",
                "status": "ready",
                "queryable": True,
            },
            "vector": {
                "provider": "Qdrant Cloud",
                "role": f"Evidence passages for semantic retrieval ({mode}, {dimensions}d)",
                "status": "configured" if settings.qdrant_configured else "not_configured",
                "queryable": settings.qdrant_configured,
                "collection": self.collection_name(),
            },
            "graph": {
                "provider": "Graphiti + Neo4j Sandbox",
                "role": "Typed entities and relationships, queried live when a question is asked",
                "status": "configured" if settings.graph_configured else "not_configured",
                "queryable": settings.graph_configured,
            },
        }

    # ------------------------------------------------------------------ #
    # Vector store
    # ------------------------------------------------------------------ #

    def _qdrant(self) -> AsyncQdrantClient:
        return AsyncQdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key, timeout=30)

    @staticmethod
    def _point_id(city: str, source_url: str, claim: str) -> str:
        return str(uuid.uuid5(POINT_NAMESPACE, f"{city.lower()}|{source_url}|{claim}"))

    async def index_vectors(self, city: str, facts: list[dict[str, Any]]) -> int:
        if not facts:
            return 0
        texts = [f"{fact['claim']} Evidence: {fact['evidence_quote']}" for fact in facts]
        vectors, mode, dimensions = await language_model.embed(texts)
        collection = self.collection_name()
        client = self._qdrant()
        try:
            if not await client.collection_exists(collection):
                await client.create_collection(
                    collection_name=collection,
                    vectors_config=models.VectorParams(size=dimensions, distance=models.Distance.COSINE),
                )
                await client.create_payload_index(
                    collection_name=collection,
                    field_name="research_city_key",
                    field_schema=models.PayloadSchemaType.KEYWORD,
                )
            await client.upsert(
                collection_name=collection,
                points=[
                    models.PointStruct(
                        id=self._point_id(city, fact["source_url"], fact["claim"]),
                        vector=vector,
                        payload={**fact, "research_city": city, "research_city_key": city.lower(), "embedding_mode": mode},
                    )
                    for fact, vector in zip(facts, vectors)
                ],
                wait=True,
            )
            return len(facts)
        finally:
            await client.close()

    async def search_vectors(self, question: str, city: str, limit: int = 6) -> list[dict[str, Any]]:
        if not settings.qdrant_configured:
            return []
        vectors, _, _ = await language_model.embed([question])
        client = self._qdrant()
        try:
            response = await client.query_points(
                collection_name=self.collection_name(),
                query=vectors[0],
                query_filter=models.Filter(
                    must=[models.FieldCondition(key="research_city_key", match=models.MatchValue(value=city.lower()))]
                ),
                limit=limit,
                with_payload=True,
            )
            return [
                {**point.payload, "origin": "vector", "similarity": round(point.score, 3)}
                for point in response.points
                if point.payload
            ]
        except Exception as error:
            logger.warning("qdrant_search_failed type=%s", type(error).__name__)
            return []
        finally:
            await client.close()

    # ------------------------------------------------------------------ #
    # Graph store
    # ------------------------------------------------------------------ #

    async def _graphiti_client(self):
        if not settings.graph_configured:
            return None
        async with self._graphiti_lock:
            if self._graphiti is None:
                from graphiti_core import Graphiti
                from graphiti_core.llm_client import LLMConfig
                from graphiti_core.llm_client.openai_generic_client import OpenAIGenericClient

                from .embedder import SystemEmbedder
                from .reranker import EmbeddingReranker

                # Three deliberate substitutions for Graphiti's defaults, each of
                # which otherwise calls OpenAI directly whatever base_url says:
                #  - OpenAIGenericClient instead of OpenAIClient: the latter uses
                #    OpenAI's Responses API, which Groq and Ollama do not serve.
                #  - SystemEmbedder, so the graph and the vector store share one
                #    embedding model and one failure policy.
                #  - EmbeddingReranker instead of OpenAIRerankerClient, which
                #    needs token logprobs that open-weights servers do not return.
                api_key = settings.openai_api_key or "not-required"
                self._graphiti = Graphiti(
                    uri=settings.neo4j_uri,
                    user=settings.neo4j_username,
                    password=settings.neo4j_password,
                    llm_client=OpenAIGenericClient(
                        # Graphiti's default client uses SDK defaults, and its own
                        # retry policy does not cover timeouts. Hosted free tiers
                        # queue requests under load, so give it room and let the
                        # SDK retry timeouts and 429s with the provider's backoff.
                        client=AsyncOpenAI(
                            api_key=api_key,
                            base_url=settings.llm_base_url,
                            timeout=httpx.Timeout(180.0, connect=15.0),
                            max_retries=3,
                        ),
                        config=LLMConfig(
                            api_key=api_key,
                            model=settings.graph_model,
                            small_model=settings.graph_fast_model,
                            base_url=settings.llm_base_url,
                            temperature=0.0,
                        ),
                        # Strict schema-constrained output. Loose json_object mode
                        # let models echo the schema back instead of filling it,
                        # which failed Graphiti's deduplication step.
                        structured_output_mode="json_schema",
                    ),
                    embedder=SystemEmbedder(),
                    cross_encoder=EmbeddingReranker(),
                    # Graphiti fans out up to 20 model calls at once by default,
                    # which empties a tokens-per-minute budget in one burst.
                    max_coroutines=max(1, settings.llm_max_concurrency),
                )
            if not self._indices_ready:
                try:
                    await self._graphiti.build_indices_and_constraints()
                except Exception as error:
                    logger.warning("graphiti_index_setup_failed type=%s", type(error).__name__)
                self._indices_ready = True
            return self._graphiti

    @staticmethod
    def _graph_episodes(city: str, country: str | None, facts: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
        """Group facts by source into episodes.

        One episode per source rather than per fact. Graphiti's cost is per
        episode — entity extraction, deduplication and relationship extraction
        each run once — so grouping cuts model calls several-fold, and it gives
        the extractor the related facts together, which yields better edges.
        """
        location = f"{city}, {country}" if country else city
        by_source: dict[str, list[dict[str, Any]]] = {}
        for fact in facts:
            by_source.setdefault(fact["source_url"], []).append(fact)
        ranked = sorted(by_source.values(), key=lambda group: max(item.get("confidence", 0) for item in group), reverse=True)

        episodes = []
        for group in ranked[:limit]:
            lines = []
            for fact in group:
                scope = (
                    f" [evidence is {fact['geographic_scope']}-level, not specific to {city}]"
                    if fact.get("scope_flag")
                    else ""
                )
                lines.append(f"- {fact['claim']}{scope}")
            head = group[0]
            episodes.append(
                {
                    "name": f"{city}: {head['source_title'][:80]}",
                    "body": (
                        f"Verified findings about {location}, published by {head['source_title']} ({head['source_url']}):\n"
                        + "\n".join(lines)
                    ),
                    "source_description": f"{head['source_title']} — independently verified CARDIO4Cities evidence",
                    "facts": len(group),
                }
            )
        return episodes

    def graph_job(self, city: str) -> dict[str, Any] | None:
        return self._graph_jobs.get(city.strip().lower())

    def schedule_graph(self, city: str, country: str | None, facts: list[dict[str, Any]], on_finish=None) -> dict[str, Any]:
        """Start writing a run's facts into the graph without holding the run up.

        Graph construction is the slowest stage by far — several structured model
        calls per episode, all inside a tokens-per-minute budget — and nothing in
        the brief depends on it. The City Lead gets the brief immediately; the
        graph fills in behind it and its progress is reported by the API.
        """
        key = city.strip().lower()
        episodes = self._graph_episodes(city, country, facts, settings.graph_max_episodes)
        job = {
            "city": city,
            "status": "queued",
            "episodes_total": len(episodes),
            "episodes_written": 0,
            "episodes_failed": 0,
            "facts_total": len(facts),
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None,
            "errors": [],
        }
        previous = self._graph_tasks.get(key)
        if previous and not previous.done():
            previous.cancel()  # a newer run for the same city supersedes it
        self._graph_jobs[key] = job
        task = asyncio.create_task(self._run_graph_job(city, episodes, job, on_finish))
        self._graph_tasks[key] = task  # strong reference: an unreferenced task can be collected
        return job

    async def wait_for_graph(self, city: str) -> dict[str, Any] | None:
        task = self._graph_tasks.get(city.strip().lower())
        if task:
            await asyncio.shield(task)
        return self.graph_job(city)

    async def _run_graph_job(self, city: str, episodes: list[dict[str, Any]], job: dict[str, Any], on_finish) -> None:
        job["status"] = "running"
        try:
            client = await self._graphiti_client()
            if client is None:
                job["status"] = "skipped"
                job["errors"].append("Graph provider is not configured.")
                return
            group_id = city_group_id(city)
            for index, episode in enumerate(episodes):
                for attempt in (1, 2):
                    try:
                        await client.add_episode(
                            name=episode["name"],
                            episode_body=episode["body"],
                            source_description=episode["source_description"],
                            reference_time=datetime.now(timezone.utc),
                            group_id=group_id,
                            entity_types=GRAPH_ENTITY_TYPES,
                        )
                        job["episodes_written"] += 1
                        break
                    except asyncio.CancelledError:
                        raise
                    except Exception as error:
                        detail = f"episode {index + 1}: {type(error).__name__}: {_safe_detail(error)[:160]}"
                        transient = type(error).__name__ in {
                            "APITimeoutError", "APIConnectionError", "RateLimitError", "ServiceUnavailable",
                            "SessionExpired", "InternalServerError",
                        }
                        if attempt == 1 and transient:
                            logger.warning("graph_episode_retry %s", detail)
                            await asyncio.sleep(30)
                            continue
                        logger.warning("graph_episode_failed %s", detail)
                        job["episodes_failed"] += 1
                        job["errors"].append(detail)
                        break
            written, total = job["episodes_written"], job["episodes_total"]
            job["status"] = "completed" if written == total else ("partial" if written else "failed")
        except asyncio.CancelledError:
            job["status"] = "superseded"
            raise
        except Exception as error:
            job["status"] = "failed"
            job["errors"].append(f"{type(error).__name__}: {_safe_detail(error)[:160]}")
            logger.exception("graph_job_failed city=%s", city)
        finally:
            job["finished_at"] = datetime.now(timezone.utc).isoformat()
            if on_finish:
                try:
                    on_finish(city, job)
                except Exception:
                    logger.exception("graph_job_callback_failed city=%s", city)

    async def search_graph(self, question: str, city: str, limit: int = 6) -> list[dict[str, Any]]:
        """Query the knowledge graph at question time.

        This is a Graphiti hybrid search over the city's subgraph, returning the
        relationship facts it holds — not a cached copy of the run.
        """
        if language_model.degraded:
            return []
        client = await self._graphiti_client()
        if client is None:
            return []
        try:
            edges = await client.search(question, group_ids=[city_group_id(city)], num_results=limit)
        except Exception as error:
            logger.warning("graphiti_search_failed type=%s", type(error).__name__)
            return []
        return [
            {
                "claim": edge.fact,
                "evidence_quote": edge.fact,
                "relationship": edge.name,
                "source_title": "Knowledge graph relationship",
                "source_url": "",
                "geographic_scope": "unknown",
                "verification_status": "GRAPH_RELATIONSHIP",
                "valid_at": edge.valid_at.isoformat() if edge.valid_at else None,
                "origin": "graph",
            }
            for edge in edges
            if getattr(edge, "fact", None)
        ]

    def graph_view(self, city: str) -> dict[str, list[dict[str, Any]]]:
        """Read the city subgraph directly from Neo4j for visualisation."""
        if not settings.graph_configured:
            return {"nodes": [], "edges": [], "detail": "Graph provider is not configured."}
        driver = GraphDatabase.driver(settings.neo4j_uri, auth=(settings.neo4j_username, settings.neo4j_password))
        try:
            with driver.session(database=settings.neo4j_database) as session:
                records = session.run(
                    """
                    MATCH (a:Entity)-[r:RELATES_TO]->(b:Entity)
                    WHERE a.group_id = $group_id AND b.group_id = $group_id
                    RETURN a, r, b
                    LIMIT 120
                    """,
                    group_id=city_group_id(city),
                )
                nodes: dict[str, dict[str, Any]] = {}
                edges: list[dict[str, Any]] = []
                for record in records:
                    left, right, relation = record["a"], record["b"], record["r"]
                    for node in (left, right):
                        labels = [label for label in node.labels if label != "Entity"]
                        nodes[node.element_id] = {
                            "id": node.element_id,
                            "label": node.get("name", "Entity"),
                            "type": labels[0] if labels else "Entity",
                            "summary": (node.get("summary") or "")[:220],
                        }
                    edges.append(
                        {
                            "source": left.element_id,
                            "target": right.element_id,
                            "label": relation.get("name") or relation.type,
                            "fact": (relation.get("fact") or "")[:300],
                        }
                    )
                return {"nodes": list(nodes.values()), "edges": edges}
        except Exception as error:
            logger.warning("neo4j_view_failed type=%s", type(error).__name__)
            return {"nodes": [], "edges": [], "detail": _safe_detail(error)}
        finally:
            driver.close()

    # ------------------------------------------------------------------ #
    # Orchestrated indexing and diagnostics
    # ------------------------------------------------------------------ #

    async def index_run(
        self, city: str, country: str | None, facts: list[dict[str, Any]], source_count: int, on_graph_finish=None
    ) -> dict[str, dict[str, Any]]:
        statuses = self.status()
        statuses["relational"]["indexed_records"] = source_count + len(facts)
        statuses["relational"]["detail"] = f"{source_count} sources and {len(facts)} verified facts written to the ledger."

        vector = statuses["vector"]
        if not vector["queryable"]:
            vector.update(indexed_records=0, detail=f"Not configured. Missing: {', '.join(settings.missing('qdrant'))}.")
        else:
            try:
                count = await self.index_vectors(city, facts)
                vector.update(indexed_records=count, detail=f"Indexed {count} verified facts into Qdrant.")
            except Exception as error:
                logger.exception("vector_indexing_failed type=%s", type(error).__name__)
                vector.update(status="error", queryable=False, indexed_records=0,
                              detail=f"Qdrant indexing failed: {type(error).__name__}")

        graph = statuses["graph"]
        if not graph["queryable"]:
            graph.update(indexed_records=0, detail=f"Not configured. Missing: {', '.join(settings.missing('graph'))}.")
        elif language_model.degraded:
            graph.update(status="degraded", indexed_records=0,
                         detail="Skipped: the language model is unavailable, and Graphiti needs one to extract entities.")
        elif not facts:
            graph.update(indexed_records=0, detail="No verified facts to add to the graph.")
        else:
            job = self.schedule_graph(city, country, facts, on_graph_finish)
            graph.update(
                status="building",
                indexed_records=0,
                episodes_total=job["episodes_total"],
                detail=(
                    f"Building in the background: {len(facts)} facts grouped into {job['episodes_total']} episodes. "
                    "The brief does not wait for it."
                ),
            )
        return statuses

    @staticmethod
    async def _check_tavily() -> dict[str, Any]:
        """One real search, so a revoked or mistyped key shows up before a demo does."""
        from .agents.discovery import TAVILY_SEARCH_URL, tavily_key

        key = tavily_key()
        if not key:
            return {"status": "not_configured", "detail": "Missing: TAVILY_API_KEY. Discovery runs on scholarly sources only."}
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.post(
                    TAVILY_SEARCH_URL,
                    json={"query": "cardiovascular health programme", "max_results": 1, "search_depth": "basic"},
                    headers={"Authorization": f"Bearer {key}"},
                )
            if response.status_code in (401, 403):
                return {"status": "error", "detail": "Tavily rejected the key. Check TAVILY_API_KEY for typos or stray spaces."}
            if response.status_code == 432:
                return {"status": "error", "detail": "Tavily plan credit limit reached."}
            response.raise_for_status()
            return {"status": "ok", "detail": "Authenticated. Web search is returning results (1 credit used)."}
        except Exception as error:
            return {"status": "error", "error_type": type(error).__name__, "detail": _safe_detail(error)}

    async def provider_check(self) -> dict[str, Any]:
        result: dict[str, Any] = {}

        if settings.qdrant_configured:
            client = self._qdrant()
            try:
                await client.get_collections()
                result["qdrant"] = {"status": "ok", "detail": f"Authenticated. Active collection: {self.collection_name()}."}
            except Exception as error:
                result["qdrant"] = {"status": "error", "error_type": type(error).__name__, "detail": _safe_detail(error)}
            finally:
                await client.close()
        else:
            result["qdrant"] = {"status": "not_configured", "detail": f"Missing: {', '.join(settings.missing('qdrant'))}"}

        if settings.neo4j_configured:
            try:
                driver = GraphDatabase.driver(settings.neo4j_uri, auth=(settings.neo4j_username, settings.neo4j_password))
                driver.verify_connectivity()
                with driver.session(database=settings.neo4j_database) as session:
                    count = session.run("MATCH (n:Entity) RETURN count(n) AS total").single()["total"]
                driver.close()
                result["neo4j"] = {"status": "ok", "database": settings.neo4j_database, "detail": f"Connected. {count} entity nodes stored."}
            except Exception as error:
                result["neo4j"] = {"status": "error", "error_type": type(error).__name__, "detail": _safe_detail(error)}
        else:
            result["neo4j"] = {"status": "not_configured", "detail": f"Missing: {', '.join(settings.missing('graph'))}"}

        if settings.llm_configured:
            probe = await language_model.json_call(
                # Reasoning models spend tokens before answering; 20 made a healthy
                # model report as broken.
                "Reply with JSON only.", 'Return {"ok": true}', max_tokens=300, purpose="provider-check", tier="fast"
            )
            result["llm"] = (
                {"status": "ok", "model": settings.llm_model, "provider": settings.llm_provider,
                 "detail": f"{settings.llm_model} reachable via {settings.llm_provider} and returning valid JSON."}
                if probe
                else {"status": "error", "model": settings.llm_model, "detail": language_model.last_error or "Model call failed."}
            )
        else:
            result["llm"] = {
                "status": "not_configured",
                "detail": "No model configured. Set OPENAI_API_KEY, or LLM_BASE_URL for a local/open-weights model. "
                          "Until then the system runs in extractive mode.",
            }

        result["tavily"] = await self._check_tavily()

        result["graphiti"] = (
            {"status": "ok", "detail": "Graph writes and query-time search are enabled."}
            if settings.graph_configured
            else {"status": "not_configured", "detail": f"Missing: {', '.join(settings.missing('graph'))}"}
        )
        return result


knowledge_layer = KnowledgeLayer()
