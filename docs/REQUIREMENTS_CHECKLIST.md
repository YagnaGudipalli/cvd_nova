# Assignment Requirements Checklist

Hosted application: <https://cardio4cities-intelligence-studio.onrender.com>

## Non-negotiables

| # | Requirement | Implementation | How to show it |
|---|---|---|---|
| 1 | Live internet research | Tavily web search plus OpenAlex, Europe PMC and Wikipedia, queried at request time. No city data is stored in code | Research a city the panel names |
| 2 | Orchestrated agentic workflow | LangGraph `StateGraph` with ten nodes and a sufficiency loop ([`workflow.py`](../backend/app/workflow.py)); `GET /api/workflow` renders it from the compiled graph | Workflow panel while a run is in progress |
| 3 | Crawlability detection before crawling | `crawl_gate` node reads `robots.txt` before any fetch ([`crawlability.py`](../backend/app/agents/crawlability.py)); tested | Open the crawl gate step: permitted and refused sources with reasons |
| 4 | Independent fact checking with consequences | Separate agent and model ([`factcheck.py`](../backend/app/agents/factcheck.py)); unsupported claims are withheld as gaps and low coverage sends the run back to planning | Withheld claims and the gap ledger in a run |
| 5 | Graphiti knowledge graph in Neo4j Sandbox, used at query time | Graphiti writes episodes to a Neo4j Sandbox; `/api/ask` searches the graph concurrently with Qdrant ([`research.py`](../backend/app/research.py)) | Knowledge graph tab; citations from graph hits in Q&A |
| 6 | Relational, vector and graph datastores | SQLite ledger, Qdrant Cloud, Graphiti on Neo4j ([`ARCHITECTURE.md`](ARCHITECTURE.md#data-architecture)) | Runtime tab store status |
| 7 | Evidence on every fact | Source URL, title, located quote, retrieval time and verdict per fact | Expand any finding |
| 8 | No fabrication; national data flagged | Quotes must be located in the source; numbers must appear in the quote; scope mismatch published only with a warning; Q&A declines without evidence | Ask a question the evidence cannot answer |
| 9 | Deployed and reachable | Docker on Render: <https://cardio4cities-intelligence-studio.onrender.com> | Open the URL |

## Functional expectations

| Expectation | Implementation |
|---|---|
| Research a previously unseen city | Any city name; nothing is pre-seeded |
| Collect information from external sources | Tavily web search and three scholarly or reference APIs; HTML, XML and PDF reader |
| Organise findings into a structured form | Five areas (burden, programmes, policy, access, actors), typed entities, verdicts |
| Preserve references to supporting evidence | Relational ledger and Qdrant payloads keep source and quote |
| Support exploration and discovery | Overview, Evidence, Knowledge graph, History and Ask tabs |
| Answer questions about the city | `POST /api/ask` with citations |
| Surface uncertainty and gaps | Gap ledger, scope warnings, confidence, insufficient-evidence answers |
| Downloadable research report | `GET /api/report/{city}` (HTML) |

## Deliverables

| Deliverable | Location |
|---|---|
| Working application | <https://cardio4cities-intelligence-studio.onrender.com> |
| Source code and setup instructions | [`README.md`](../README.md), [`.env.example`](../.env.example), [`Dockerfile`](../Dockerfile), [`render.yaml`](../render.yaml) |
| Architecture overview | [`ARCHITECTURE.md`](ARCHITECTURE.md), `/architecture.html` |
| Example output | [`examples/hyderabad-cardio4cities-brief.html`](examples/hyderabad-cardio4cities-brief.html) and its run record |
| Presentation deck (5–8 slides) | 8 slides at `/presentation.html`; notes in [`PRESENTATION.md`](PRESENTATION.md) |
| What was cut and why | [`README.md`](../README.md#what-was-cut-and-why) |

## Before the demonstration

- [ ] Neo4j Sandbox is running and extended; `/health` shows `"graph": true`.
- [ ] `GET /api/provider-check` shows `tavily`, `qdrant`, `neo4j` and `llm` as `ok`.
- [ ] Open the site a few minutes early: the free Render instance sleeps when idle.
- [ ] Run one test city on the live site. Research history is on local disk and resets
      when the instance restarts.
