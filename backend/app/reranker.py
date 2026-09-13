"""Provider-neutral reranker for Graphiti.

Graphiti ranks search candidates with a cross-encoder, and its default is
``OpenAIRerankerClient`` — which calls api.openai.com directly regardless of how
the rest of Graphiti is configured, and which depends on token ``logprobs`` that
Groq and most open-weights servers do not return. Left as the default, every
query-time graph search fails the moment OpenAI is unavailable.

This implementation ranks by cosine similarity between the query embedding and
each passage embedding, using the same embedder as the rest of the system. A
bi-encoder is less precise than a true cross-encoder, but it runs on any
embedding endpoint — including a local Ollama — and needs no GPU or torch.
"""

import math

from graphiti_core.cross_encoder.client import CrossEncoderClient

from .llm import language_model


def _cosine(left: list[float], right: list[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right))
    norm = math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right))
    return dot / norm if norm else 0.0


class EmbeddingReranker(CrossEncoderClient):
    async def rank(self, query: str, passages: list[str]) -> list[tuple[str, float]]:
        if not passages:
            return []
        vectors, _, _ = await language_model.embed([query, *passages])
        query_vector, passage_vectors = vectors[0], vectors[1:]
        scored = [(passage, _cosine(query_vector, vector)) for passage, vector in zip(passages, passage_vectors)]
        return sorted(scored, key=lambda item: item[1], reverse=True)
