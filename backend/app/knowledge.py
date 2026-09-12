import os
import re
import hashlib
import logging
from datetime import datetime, timezone
from typing import Any

from graphiti_core import Graphiti
from graphiti_core.embedder.openai import OpenAIEmbedder
from graphiti_core.llm_client import OpenAIClient
from neo4j import GraphDatabase
from openai import OpenAI
from qdrant_client import QdrantClient, models


logger = logging.getLogger("cardio4cities.providers")


def _terms(value: str) -> set[str]:
    return {term for term in re.findall(r"[a-z0-9]{3,}", value.lower())}


class KnowledgeLayer:
    """Provider boundary for vector evidence and Graphiti/Neo4j relationships."""

    def __init__(self) -> None:
        self.qdrant_url = os.getenv("QDRANT_URL")
        self.qdrant_api_key = os.getenv("QDRANT_API_KEY")
        self.neo4j_uri = os.getenv("NEO4J_URI")
        self.neo4j_username = os.getenv("NEO4J_USERNAME")
        self.neo4j_password = os.getenv("NEO4J_PASSWORD")
        self.neo4j_database = os.getenv("NEO4J_DATABASE", "neo4j")
        self.openai_api_key = os.getenv("OPENAI_API_KEY")
        self.graphiti_enabled = os.getenv("GRAPHITI_ENABLED", "false").lower() == "true"
        self.qdrant_collection = os.getenv("QDRANT_COLLECTION", "cardio4cities_evidence_v2")

    def configuration(self) -> dict[str, Any]:
        qdrant_complete = bool(self.qdrant_url and self.qdrant_api_key)
        graph_complete = bool(
            self.graphiti_enabled
            and self.neo4j_uri
            and self.neo4j_username
            and self.neo4j_password
            and self.openai_api_key
        )
        return {
            "qdrant": {
                "configured": qdrant_complete,
                "missing": [name for name, value in (("QDRANT_URL", self.qdrant_url), ("QDRANT_API_KEY", self.qdrant_api_key)) if not value],
            },
            "graphiti": {
                "configured": graph_complete,
                "missing": [
                    name
                    for name, value in (
                        ("GRAPHITI_ENABLED=true", self.graphiti_enabled),
                        ("NEO4J_URI", self.neo4j_uri),
                        ("NEO4J_USERNAME", self.neo4j_username),
                        ("NEO4J_PASSWORD", self.neo4j_password),
                        ("NEO4J_DATABASE", self.neo4j_database),
                        ("OPENAI_API_KEY", self.openai_api_key),
                    )
                    if not value
                ],
            },
        }

    def status(self) -> dict[str, dict[str, Any]]:
        return {
            "relational": {"provider": "SQLite ledger", "status": "ready", "queryable": True},
            "vector": {
                "provider": "Qdrant Cloud",
                "status": "configured" if self.configuration()["qdrant"]["configured"] else "not_configured",
                "queryable": self.configuration()["qdrant"]["configured"],
            },
            "graph": {
                "provider": "Graphiti + Neo4j Sandbox",
                "status": "configured" if self.configuration()["graphiti"]["configured"] else "not_configured",
                "queryable": self.configuration()["graphiti"]["configured"],
            },
        }

    async def index_run(self, run: dict[str, Any]) -> dict[str, dict[str, Any]]:
        """Index verified evidence in configured vector and graph providers."""
        statuses = self.status()
        statuses["relational"]["indexed_records"] = len(run.get("sources", [])) + len(run.get("facts", []))
        statuses["vector"]["indexed_records"] = 0
        statuses["graph"]["indexed_records"] = 0
        if statuses["vector"]["queryable"]:
            try:
                count = await self._index_qdrant(run)
                statuses["vector"]["indexed_records"] = count
                statuses["vector"]["detail"] = f"Indexed {count} evidence passages in Qdrant."
            except Exception as error:
                logger.exception("qdrant_indexing_failed type=%s", type(error).__name__)
                statuses["vector"]["status"] = "error"
                statuses["vector"]["queryable"] = False
                statuses["vector"]["detail"] = f"Qdrant indexing failed: {type(error).__name__}"
        else:
            statuses["vector"]["detail"] = "Add QDRANT_URL and QDRANT_API_KEY to .env."
        if statuses["graph"]["queryable"]:
            try:
                count = await self._index_graph(run)
                statuses["graph"]["indexed_records"] = count
                statuses["graph"]["detail"] = f"Indexed {count} verified facts through Graphiti into Neo4j."
            except Exception as error:
                logger.exception("graphiti_indexing_failed type=%s", type(error).__name__)
                statuses["graph"]["status"] = "error"
                statuses["graph"]["queryable"] = False
                statuses["graph"]["detail"] = f"Graphiti indexing failed: {type(error).__name__}"
        else:
            statuses["graph"]["detail"] = "Add Neo4j credentials, OPENAI_API_KEY, and GRAPHITI_ENABLED=true to .env."
        return statuses

    async def _index_qdrant(self, run: dict[str, Any]) -> int:
        facts = run.get("facts", [])
        if not facts:
            return 0
        client = QdrantClient(url=self.qdrant_url, api_key=self.qdrant_api_key)
        texts = [f"{fact['claim']} Evidence: {fact['evidence_quote']}" for fact in facts]
        embedding_mode = "openai"
        try:
            embedder = OpenAI(api_key=self.openai_api_key)
            response = embedder.embeddings.create(model="text-embedding-3-small", input=texts)
            vectors = [item.embedding for item in response.data]
        except Exception:
            embedding_mode = "deterministic-fallback"
            vectors = [self._fallback_embedding(text) for text in texts]
        if not client.collection_exists(self.qdrant_collection):
            client.create_collection(
                collection_name=self.qdrant_collection,
                vectors_config=models.VectorParams(size=len(vectors[0]), distance=models.Distance.COSINE),
            )
        client.upsert(
            collection_name=self.qdrant_collection,
            points=[
                models.PointStruct(
                    id=abs(hash(run["city"].lower() + fact["source_url"] + fact["claim"])) % (2**63),
                    vector=vector,
                    payload={**fact, "research_city": run["city"]},
                )
                for fact, vector in zip(facts, vectors)
            ],
        )
        return len(facts)

    @staticmethod
    def _fallback_embedding(text: str) -> list[float]:
        dimensions = 384
        vector = [0.0] * dimensions
        for token in re.findall(r"[a-z0-9]{3,}", text.lower()):
            digest = hashlib.sha256(token.encode()).digest()
            index = int.from_bytes(digest[:4], "big") % dimensions
            vector[index] += 1.0 if digest[4] % 2 else -1.0
        magnitude = sum(value * value for value in vector) ** 0.5 or 1.0
        return [value / magnitude for value in vector]

    async def _index_graph(self, run: dict[str, Any]) -> int:
        graphiti = Graphiti(
            uri=self.neo4j_uri,
            user=self.neo4j_username,
            password=self.neo4j_password,
            llm_client=OpenAIClient(),
            embedder=OpenAIEmbedder(),
        )
        facts = run.get("facts", [])
        for index, fact in enumerate(facts):
            await graphiti.add_episode(
                name=f"{run['city']} evidence {index + 1}",
                episode_body=f"City research target: {run['city']}. Claim: {fact['claim']} Evidence: {fact['evidence_quote']} Source: {fact['source_url']}",
                source_description=fact.get("source_title", "Public source"),
                reference_time=datetime.now(timezone.utc),
                group_id=f"cardio4cities-{re.sub(r'[^a-z0-9-]', '-', run['city'].lower())}",
            )
        await graphiti.close()
        return len(facts)

    def graph(self, city: str) -> dict[str, list[dict[str, Any]]]:
        if not self.configuration()["graphiti"]["configured"]:
            return {"nodes": [], "edges": []}
        driver = GraphDatabase.driver(self.neo4j_uri, auth=(self.neo4j_username, self.neo4j_password))
        try:
            with driver.session(database=self.neo4j_database) as session:
                records = session.run(
                    "MATCH (a)-[r]->(b) WHERE toLower(coalesce(a.name, '')) CONTAINS toLower($city) OR toLower(coalesce(b.name, '')) CONTAINS toLower($city) RETURN a, r, b LIMIT 80",
                    city=city,
                )
                nodes: dict[str, dict[str, Any]] = {}
                edges = []
                for record in records:
                    left, right = record["a"], record["b"]
                    for node in (left, right):
                        nodes[node.element_id] = {"id": node.element_id, "label": node.get("name", "Entity"), "type": next(iter(node.labels), "Entity")}
                    edges.append({"source": left.element_id, "target": right.element_id, "label": record["r"].type})
                return {"nodes": list(nodes.values()), "edges": edges}
        finally:
            driver.close()

    def provider_check(self) -> dict[str, Any]:
        result: dict[str, Any] = {"qdrant": {}, "neo4j": {}, "graphiti": {}}
        if self.configuration()["qdrant"]["configured"]:
            try:
                QdrantClient(url=self.qdrant_url, api_key=self.qdrant_api_key).get_collections()
                result["qdrant"] = {"status": "ok", "detail": "Qdrant authentication and API access succeeded."}
            except Exception as error:
                result["qdrant"] = {"status": "error", "error_type": type(error).__name__, "detail": self._safe_provider_detail(error)}
        else:
            result["qdrant"] = {"status": "not_configured"}
        if self.configuration()["graphiti"]["configured"]:
            try:
                driver = GraphDatabase.driver(self.neo4j_uri, auth=(self.neo4j_username, self.neo4j_password))
                driver.verify_connectivity()
                with driver.session(database=self.neo4j_database) as session:
                    session.run("RETURN 1").consume()
                driver.close()
                result["neo4j"] = {"status": "ok", "database": self.neo4j_database, "detail": "Neo4j authentication, connectivity, and database access succeeded."}
            except Exception as error:
                result["neo4j"] = {"status": "error", "error_type": type(error).__name__, "detail": self._safe_provider_detail(error)}
            try:
                OpenAI(api_key=self.openai_api_key).models.list()
                result["graphiti"] = {"status": "ok", "detail": "OpenAI authentication succeeded; Graphiti can attempt episode extraction."}
            except Exception as error:
                result["graphiti"] = {"status": "error", "error_type": type(error).__name__, "detail": self._safe_provider_detail(error)}
        else:
            result["graphiti"] = {"status": "not_configured"}
        return result

    @staticmethod
    def _safe_provider_detail(error: Exception) -> str:
        message = str(error).replace("\n", " ")
        for secret_name in ("sk-", "neo4j", "QDRANT"):
            if secret_name in message:
                return "Provider returned an error; inspect the deployment logs for the sanitized exception category."
        return message[:240]

    def _search_qdrant(self, question: str, city: str) -> list[dict[str, Any]]:
        if not self.status()["vector"]["queryable"]:
            return []
        client = QdrantClient(url=self.qdrant_url, api_key=self.qdrant_api_key)
        vector = self._fallback_embedding(question)
        try:
            hits = client.search(
                collection_name=self.qdrant_collection,
                query_vector=vector,
                query_filter=models.Filter(
                    must=[models.FieldCondition(key="research_city", match=models.MatchValue(value=city))]
                ),
                limit=5,
            )
        except Exception:
            return []
        return [hit.payload for hit in hits if hit.payload]

    def answer_from_run(self, question: str, run: dict[str, Any]) -> dict[str, Any]:
        """Retrieve evidence from Qdrant first, with the relational ledger as fallback."""
        vector_evidence = self._search_qdrant(question, run["city"])
        if vector_evidence:
            return {
                "answer": "The evidence store contains passages relevant to this question.",
                "confidence": "medium",
                "evidence": vector_evidence,
                "caveat": "Retrieved from Qdrant; verify scope in the cited source.",
            }
        query_terms = _terms(question)
        matches = []
        scoped_facts = [
            fact for fact in run.get("facts", [])
            if fact.get("research_city", run["city"]).lower() == run["city"].lower()
            and not re.search(r"^([A-Z][a-z]+)\s*[:,-]", fact.get("source_title", ""))
        ]
        for fact in scoped_facts:
            searchable = _terms(f"{fact.get('claim', '')} {fact.get('evidence_quote', '')} {fact.get('category', '')}")
            overlap = query_terms & searchable
            if overlap:
                matches.append((len(overlap), fact))
        matches.sort(key=lambda item: item[0], reverse=True)
        evidence = [fact for _, fact in matches[:5]]
        if not evidence:
            return {
                "answer": "Insufficient verified evidence was found for this question.",
                "confidence": "low",
                "evidence": [],
                "caveat": "The system will not infer an answer from unverified or missing facts.",
            }
        return {
            "answer": "The verified research ledger contains evidence relevant to this question.",
            "confidence": "medium",
            "evidence": evidence,
            "caveat": "Answer generated only from independently verified facts.",
        }


knowledge_layer = KnowledgeLayer()