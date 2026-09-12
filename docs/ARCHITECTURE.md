# CARDIO4Cities Architecture

## Product boundary

The application creates an evidence-first briefing for a city that has not been researched before. It researches public sources at request time, separates city-level claims from broader context, withholds unsupported facts, and preserves the evidence trail.

## Runtime flow

1. Intake guardrails validate the city and country and declare geographic-scope rules.
2. The research planner creates topic queries for cardiovascular health, healthcare access, policy, and prevention.
3. Live search discovers public sources.
4. Crawlability detection checks `robots.txt` before extraction.
5. Extraction stores clean page text, URL, title, and retrieval time.
6. Independent fact checking accepts supported or partially supported claims and converts rejected claims into knowledge gaps.
7. PostgreSQL-compatible relational storage is represented locally by SQLite for the demo ledger; Qdrant stores evidence vectors; Graphiti writes entity episodes to Neo4j Sandbox.
8. The report, graph view, Q&A, admin metrics, traces, and downloadable brief expose the result.

## Data responsibilities

- **Relational ledger:** research runs, source metadata, facts, verification outcomes, gaps, metrics, and traces.
- **Qdrant:** semantic evidence passages with `research_city`, source URL, claim, quote, and verification metadata.
- **Graphiti + Neo4j:** entities and relationships among cities, sources, facts, programs, policies, and organizations.

Every Qdrant retrieval is filtered by `research_city`. Neo4j graph retrieval is filtered by city. This prevents evidence from another city's run leaking into an answer.

## Trust and safety

- No pre-seeded city facts are used for live research.
- A source is checked for crawlability before extraction.
- Every published fact has a source URL and evidence quote.
- City scope is explicit; national or regional context is not silently presented as city data.
- Unsupported or conflicting claims are withheld and recorded as gaps.
- Q&A returns insufficient evidence rather than inventing an answer.
- Provider errors, guardrail outcomes, durations, and counts are persisted for audit.

## Known trade-offs

- SQLite is suitable for the local and demo deployment but should be replaced with managed PostgreSQL for multi-user production use.
- The system uses deterministic fallback embeddings when an OpenAI embedding request is rate-limited; the Runtime tab records provider errors.
- Graphiti entity extraction needs a functioning LLM quota. Existing Neo4j graph data remains queryable if a new episode cannot be ingested.
- Search and extraction are intentionally narrow to fit the two-to-three-day case-study timebox.