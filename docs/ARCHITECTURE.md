# Architecture overview

A visual version of this document, with diagrams, is served at
[`/architecture.html`](../frontend/architecture.html).

## The problem, as framed

A City Lead needs to walk into a meeting with health officials understanding a
city they have never researched. The failure mode that matters is not *slow* —
it is *confidently wrong in the room*: a national statistic quoted as a city one,
a programme that ended two years ago, a figure nobody can source.

So this is not a summariser. It is a research system whose every output can be
interrogated, and whose unknowns are part of the product.

## Major components

```
Browser ── FastAPI ── LangGraph StateGraph (10 nodes) ── SQLite ledger
                                │                     ── Qdrant vectors
                                │                     ── Graphiti → Neo4j
                                └── public APIs, source websites, LLM provider
```

- **Frontend** (`frontend/`) — static workspace: workflow trace, evidence ledger,
  gap ledger, graph view, Q&A, admin telemetry. No build step.
- **API** (`backend/app/main.py`, `research.py`) — one FastAPI app serving both
  the API and the frontend, so there is a single origin to deploy.
- **Orchestration** (`backend/app/workflow.py`) — the LangGraph graph. Every
  stage writes a trace event with a status and a duration.
- **Agents** (`backend/app/agents/`) — one module per responsibility: planner,
  discovery, crawlability, extraction, claims, factcheck, synthesis.
- **Knowledge** (`backend/app/storage.py`, `knowledge.py`) — the three stores.
- **Ontology** (`backend/app/ontology.py`) — the five research dimensions and the
  typed graph entities. This is the file that defines what the system believes
  "understanding a city" means.

## AI and agent architecture

Responsibility is split so that **no agent both produces a claim and approves
it**, and two stages are gates rather than steps — they remove work from the
pipeline, and what they remove is recorded.

- `claim_extraction` is the only agent allowed to phrase a claim. It is never
  allowed to decide whether that claim may be published.
- `fact_check` is the only agent allowed to publish. It never writes claims, has
  an adversarial prompt, does not see the extractor's reasoning, and can return
  `UNSUPPORTED`.
- `sufficiency` measures dimension coverage and owns the decision to spend more
  research budget or to publish with declared gaps.

Model failure is centralised in `llm.py`. Callers receive `None` and take a
deterministic path — they never treat "no model" as "assume the answer". A
terminal provider error opens a circuit breaker for the rest of the run.

## Data architecture

Three stores because three questions have genuinely different shapes.

| | Question | Store |
|---|---|---|
| Relational | *What did the system decide, and when?* | SQLite → PostgreSQL |
| Vector | *What evidence is near this question, in my words?* | Qdrant |
| Graph | *Who is connected to what, and since when?* | Graphiti → Neo4j |

Forcing all three into one store makes two of them slow and one of them lossy.
The relational store is the only one that is always available, which is what
keeps the system inspectable when external providers are not configured.

## Retrieval strategy

1. **Scope first.** A question is bound to one researched city before anything is
   retrieved, so evidence cannot leak between cities.
2. **Vector and graph concurrently.** Qdrant for semantic recall over evidence
   passages, `graphiti.search()` for relationship facts in the city's subgraph.
3. **Ledger fallback** when fewer than three items come back, so retrieval
   degrades instead of disappearing.
4. **Synthesis from evidence only**, citing numbered items which the API resolves
   back to the stored records.
5. **Refuse cleanly.** No verified match returns an explicit insufficiency
   response, not an inference.

## Key design decisions

1. **Crawlability is a gate, not a filter.** `robots.txt` is read before the first
   byte is fetched. Refused sources stay visible with their reason so a human can
   follow them manually.
2. **Gaps are a first-class output.** Four kinds — withheld, blocked, unreachable,
   missing — each with a reason and a URL. They are the questions to ask in the room.
3. **Scope is carried on every fact.** National data is the most likely way this
   system could mislead, so scope is a field, and national evidence is published
   only with an explicit warning.
4. **Discovery uses APIs, not scraped search.** Search engines serve captchas to
   non-browser clients, and scraping them sits badly beside a crawl-permission gate.
5. **Provider failure is surfaced, not swallowed.** `/api/runtime` reports real
   capability; `/api/provider-check` gives a sanitised diagnosis without echoing
   credentials.

## Trade-offs

- **SQLite over managed PostgreSQL** — zero setup, portable schema, explicitly not
  production storage on an ephemeral disk.
- **Capped research budget** — few queries and fetches per round keeps the
  end-to-end loop demonstrable. Depth is a tuning problem; the pipeline shape is
  the hard part.
- **No human review before storage** — the fact-check gate plus a visible gap
  ledger buys most of the safety for a fraction of the time.
- **No PDF extraction** — the largest coverage gap, since many government health
  reports are PDFs. They are recorded as sources and left for manual review.
- **No conflict resolution** — disagreeing sources both appear with their quotes.
- **Free hosting sleeps** — first request after idle is slow.

## Evaluation

The admin view is part of the argument, not a debug panel: source discovery and
crawl-approval counts, extraction success, evidence coverage, city-scope quality,
verified versus withheld, dimension coverage, mean confidence, provider errors,
guardrail outcomes and per-stage durations. A claim about trustworthiness can
therefore be checked rather than believed.
