"""Graphiti embedder that routes through the system's own embedding boundary.

Graphiti embeds entity names and relationship facts. Its default embedder calls
OpenAI; this one calls :meth:`llm.LanguageModel.embed` instead, so the graph and
the vector store share one embedding model, one set of dimensions and one failure
policy — whether that model is running in-process or behind an API.
"""

from collections.abc import Iterable

from graphiti_core.embedder.client import EmbedderClient

from .llm import language_model


class SystemEmbedder(EmbedderClient):
    async def create(self, input_data: str | list[str] | Iterable[int] | Iterable[Iterable[int]]) -> list[float]:
        text = input_data if isinstance(input_data, str) else " ".join(str(item) for item in input_data)
        vectors, _, _ = await language_model.embed([text])
        return vectors[0]

    async def create_batch(self, input_data_list: list[str]) -> list[list[float]]:
        vectors, _, _ = await language_model.embed(input_data_list)
        return vectors
