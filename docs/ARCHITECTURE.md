# CARDIO4Cities Architecture

A diagrammed version of this overview is served at
[`/architecture.html`](../frontend/architecture.html)
(live: <https://cardio4cities-intelligence-studio.onrender.com/architecture.html>).

## Product boundary

The application creates an evidence-first brief for a city that has not been
researched before. It researches public sources at request time, separates
city-level claims from broader context, withholds unsupported claims, and keeps the
evidence trail for every published fact. What it cannot establish is published as a
knowledge gap.

## Major components

| Component | Technology | Role |
|---|---|---|
| Orchestration | LangGraph `StateGraph` ([`workflow.py`](../backend/app/workflow.py)) | Ten nodes, one conditional edge that loops back to planning |
| API and jobs | FastAPI | Starts research as a background job the UI polls; Q&A, report, graph, diagnostics |
| Frontend | Vanilla JS, no build step | Research progress, evidence, graph, Q&A, runtime tabs |
| Models | Open-weights models on Groq via an OpenAI-compatible API | Planning, claim extraction, fact checking, answers, graph extraction |
| Source discovery | **Tavily** web search; OpenAlex, Europe PMC, Wikipedia | Live sources at request time |
| Datastores | SQLite, Qdrant Cloud, Graphiti on Neo4j Sandbox | Ledger, evidence vectors, knowledge graph |
| Hosting | Docker on Render (free tier) | One URL serves API and frontend |

## Agent architecture

```
intake → plan → discover → crawl_gate → extract → claim_extraction
            ↑                                            ↓
            └────────── sufficiency ←──────────── fact_check
                             ↓
                          index → report
```

| Agent (node) | Responsibility | Consequence |
|---|---|---|
| Planner (`plan`) | Writes search queries per area; in round 2, only for areas still empty | |
| Discovery (`discover`) | Runs every query against every available search provider in parallel, de-duplicates and ranks by publisher authority | A provider returning nothing is recorded as a gap |
| Crawl gate (`crawl_gate`) | Reads `robots.txt` **before** any fetch | Disallowed sources are never read, and are listed with a reason |
| Reader (`extract`) | Fetches permitted HTML, XML and PDF within the depth budget | Unreadable pages become gaps |
| Claim agent (`claim_extraction`) | Proposes claims, each with a quote that must be located in the source text | Unlocatable quotes are dropped |
| Fact checker (`fact_check`) | A different model rules each claim supported, partially supported, scope-mismatched or unsupported; numbers must appear in the quote | Unsupported claims are withheld and become gaps |
| Sufficiency (`sufficiency`) | Needs verified city evidence in 3 of 5 areas | Otherwise routes back to the Planner, bounded by depth |
| Answer agent (`/api/ask`) | Answers from stored evidence only, with citations | Declines when evidence is insufficient |

The trust rule: no agent both writes a claim and approves it.

## Source discovery

Discovery calls search APIs rather than scraping a search engine. Scraped engines
serve captchas to non-browser clients, and scraping sits badly next to a crawl gate
whose job is to respect automated-access rules.

| Provider | Key | What it contributes |
|---|---|---|
| **Tavily** | `TAVILY_API_KEY` | General web search: government, ministry, municipal, NGO and programme pages. Without it, policy and access evidence is thin. |
| OpenAlex | none | Scholarly works and open-access landing pages |
| Europe PMC | none | Biomedical literature, open-access full text through its REST API |
| Wikipedia | none | Background context and links to official bodies |
| Brave / Serper | optional | Alternatives to Tavily |

Tavily runs one `basic` search per planned query (one credit each): 3 for a Quick
run, up to 10 for Balanced and up to 12 for Thorough. The key is sent as an
`Authorization: Bearer` header, never in the request body. Tavily also returns news
and commercial pages; authority ranking decides what is read first, and the fact
checker decides what is published. `GET /api/provider-check` makes one real Tavily
search to confirm the key works.

## Data architecture

| Store | Holds | Why this store |
|---|---|---|
| **Relational** (SQLite) | Runs, sources, crawl decisions, facts, verification outcomes, gaps, entities, guardrails, traces, metrics | The audit record needs exact reads and a schema. It answers "what did the system decide, and when?" |
| **Vector** (Qdrant Cloud) | Verified evidence passages with source URL, claim, quote, scope and verdict | Users ask in their own words, not the source's. Payload-filtered by city |
| **Graph** (Graphiti on Neo4j Sandbox) | `Organization`, `Programme`, `Policy`, `HealthIndicator`, `Place` and their relationships | "Who runs what" is a traversal. Graphiti's bi-temporal model keeps what was previously true when a city is re-researched |

Every Qdrant retrieval is filtered by city, and graph search is scoped to the city's
group, so evidence from one city cannot leak into another city's answer. The Qdrant
collection is named after the embedding model and dimensions, so vectors from one
embedder are never queried with another.

## Retrieval strategy

1. `POST /api/ask` ties the question to one city.
2. Qdrant and Graphiti are searched **concurrently at question time**.
3. Vector and graph hits are merged and de-duplicated (Graphiti reranks its own
   results by embedding similarity). When fewer than three items come back, the
   relational ledger fills in, so Q&A still works if a store is unavailable.
4. The answer agent writes only from the retrieved records, with numbered citations
   that resolve to stored sources. With too little evidence it says so.

## Trust and safety

- No pre-seeded city facts; every run researches live.
- Crawlability is decided before extraction.
- Every published fact has a source URL, a located quote and a retrieval time.
- National or regional evidence is published only with a scope warning.
- Unsupported claims are withheld and recorded as gaps.
- Provider errors, guardrail outcomes, durations and counts are stored for audit, and
  `GET /api/runtime` reports degraded modes, which the UI shows as a banner.

## Key trade-offs

- **Free open-weights models:** no cost or lock-in, paid for with rate limits. A
  throttled call fails over to another model instead of stopping the run.
- **Search APIs, not scraping:** reliable and sanctioned, paid for with a Tavily
  credit budget (1,000 free a month) and noisier general-web results.
- **SQLite on free hosting:** simple and schema-first, but history resets when the
  Render instance restarts. Vectors and graph live externally and persist. Managed
  PostgreSQL is the production path.
- **Neo4j Sandbox:** mandated by the case study; instances expire after 3 days
  (extendable once to 10), so the graph is rebuilt by re-running research.
- **Brief first, graph later:** the graph is written in the background so the user
  never waits for it.
- **Cut:** human review before storage, contradiction resolution, OCR for scanned
  PDFs, authentication.
