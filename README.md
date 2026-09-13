# CARDIO4Cities Intelligence Studio

An AI research system that prepares a City Lead for a meeting with government and
healthcare stakeholders about cardiovascular health — in a city nobody has
researched before.

It researches the live public internet on request, decides what it is permitted
to crawl before crawling it, extracts claims that are anchored to wording located
in the source, has a **separate** agent decide whether each claim may be
published, and writes what survives into three datastores. What it cannot
establish is published as a knowledge gap rather than filled in.

---

## Quick start

```bash
python -m pip install -r requirements.txt
cp .env.example .env          # then fill in the values you have
uvicorn backend.app.main:app --reload
```

Open <http://127.0.0.1:8000>, type a city, press **Start research**.

No credentials are required to run it. Every provider is optional and the system
reports honestly which ones are active — see *Graceful degradation* below.

---

## What "understanding a city" was defined as

The case study leaves this open. This system commits to five dimensions, and the
commitment is enforced by the workflow rather than described in a document:
planning generates queries per dimension, and the sufficiency stage measures
coverage against them.

| Dimension | Question it answers |
|---|---|
| `burden` | What is the cardiovascular disease burden, and how prevalent are hypertension, type 2 diabetes and dyslipidaemia? |
| `programmes` | Which programmes, screening initiatives or interventions already operate here, and who runs them? |
| `policy` | Which municipal or national policies and strategies shape cardiovascular health? |
| `access` | What does access to primary care, diagnosis, treatment and medication look like? |
| `actors` | Which organisations and institutions are active in public health here? |

A deliberate exclusion: the system never characterises a named individual's
views or intentions. Those are exactly the claims public sources cannot support.

---

## The agent workflow

Ten LangGraph nodes, each with one responsibility. `GET /api/workflow` renders
this structure **from the compiled graph**, so the diagram cannot drift from the
code that runs.

```
intake → plan → discover → crawl_gate → extract → claim_extraction
            ↑                                            ↓
            └────────── sufficiency ←──────────── fact_check
                             ↓
                          index → report
```

| Node | Responsibility |
|---|---|
| `intake` | Validates the target and declares the guardrails the run is held to. |
| `plan` | Expands the city into dimension-scoped search queries. |
| `discover` | Queries public APIs at request time, ranks results by source authority. |
| `crawl_gate` | Reads `robots.txt` and decides what may be fetched — **before** any fetch. |
| `extract` | Retrieves permitted pages, reduces them to clean text with a timestamp. |
| `claim_extraction` | Proposes specific findings, each anchored to a located quote. |
| `fact_check` | Independently decides supported / partially supported / scope-mismatched / withheld. |
| `sufficiency` | Measures dimension coverage, then re-plans or publishes. |
| `index` | Writes to the relational, vector and graph stores. |
| `report` | Assembles findings, gaps and quality metrics. |

**The cycle is the consequence.** When fewer than three of five dimensions have
city-level verified evidence, `sufficiency` routes back to `plan`, which is told
which dimensions came back empty and plans different queries for them. Bounded
by `RESEARCH_MAX_ROUNDS` so a run always terminates.

---

## How fabrication is prevented

Three mechanical guards, none of which depend on a model behaving well:

1. **Quotes must exist.** Whatever the extractor returns as an evidence quote is
   looked up in the source text, and the span that gets stored is the one found
   *in the document*. A quote that cannot be located is discarded and counted.
   A hallucinated citation therefore cannot reach the ledger.
2. **Figures must appear in the evidence.** Any number in a claim that is absent
   from its quote makes the claim `UNSUPPORTED` automatically, with no model
   opinion involved. This is the "never invent statistics" rule, enforced.
3. **Scope is carried and flagged.** Every fact records whether its evidence is
   city, regional, national or unknown in scope. National evidence is published
   only with an explicit warning attached, never as a city figure.

The fact checker is a separate agent with an adversarial prompt that never sees
the extractor's reasoning, and it can conclude `UNSUPPORTED` — in which case the
claim is withheld and becomes a knowledge gap.

---

## Data architecture — three stores, three jobs

| Store | Holds | Why this store | Read at |
|---|---|---|---|
| **Relational** (SQLite → PostgreSQL) | Runs, sources, crawl decisions, facts, gaps, entities, guardrails, traces, metrics | The audit record. Needs exact reads and a schema, not similarity. The only store that can answer *what did the system decide, and when*. | History, report, admin view |
| **Vector** (Qdrant Cloud) | Evidence passages with full provenance | A City Lead asks questions in their own words, not the source's. Payload-filtered by city so evidence cannot leak between cities. | Every question |
| **Graph** (Graphiti → Neo4j Sandbox) | Typed entities — `Organization`, `Programme`, `Policy`, `HealthIndicator`, `Place` — and their relationships | *Who runs what, and how does it connect* is a traversal, not a search. Graphiti's bi-temporal tracking lets a city be re-researched without destroying what was previously true. | Every question, via `graphiti.search()` |

`POST /api/ask` queries the vector store and the graph **concurrently at question
time**, falls back to the relational ledger when it needs more, and returns every
cited item with its source so any sentence can be traced.

Two bugs worth naming, because they are the kind that hide:

- Indexing and querying both embed through one function, and the Qdrant
  collection is **named after the embedding signature** (`..._text_embedding_3_small_1536`
  vs `..._lexical_hash_384`). A vector written with one model can never be
  queried with another.
- Point IDs are `uuid5(city, source, claim)`, so re-running a city updates its
  evidence in place instead of accumulating duplicates across restarts.

---

## Source discovery

Scraping a search engine does not work: the major engines serve captchas or
query-insensitive pages to non-browser clients, and it sits badly beside a system
whose second stage exists to respect automated-access rules. Discovery therefore
runs against providers that publish an API and permit programmatic use.

| Provider | Key | Role |
|---|---|---|
| **OpenAlex** | none | Scholarly works, open-access landing pages |
| **Europe PMC** | none | Biomedical literature; open-access full text via its REST API |
| **Wikipedia** | none | Background context and links to official bodies |

Content types read: **HTML**, **XML** (full-text APIs) and **PDF**. PDF support is
load-bearing rather than incidental — government health strategies and national
statistics reports are overwhelmingly PDFs, so an HTML-only extractor
systematically misses the most authoritative city-level evidence available.
| **Tavily / Brave / Serper** | optional | General web search covering government, ministry and policy pages |

**Configure one keyed provider for a real demonstration.** Without it the brief
leans academic and under-represents municipal policy — and the run says so, in
the gap ledger and in the UI. Each provider is independently failure-tolerant and
retried once on transient upstream errors.

---

## Graceful degradation

Configuration is not capability. `GET /api/runtime` reports what the system can
actually do right now, and the UI shows a banner when it is running reduced.

| Missing | Effect |
|---|---|
| No `OPENAI_API_KEY`, or quota exhausted | Claim extraction becomes **extractive**: it selects sentences that name the city verbatim rather than writing them, so it still cannot fabricate. Verification becomes rule-based. Graph writing is skipped, because Graphiti needs a model to extract entities. |
| No Qdrant | Retrieval falls back to lexical matching over the relational ledger. |
| No Neo4j / Graphiti | Graph view and graph retrieval are empty and say so. |

The first terminal model error (quota, auth) opens a circuit breaker, so a dead
key costs one failed call rather than one per document.

---

## Configuration

```env
# Language model — optional; without it the system runs extractively.
# See "Using open-source models" below — OpenAI is a default, not a dependency.
OPENAI_API_KEY=sk-...
LLM_MODEL=gpt-4.1-mini
EMBEDDING_MODEL=text-embedding-3-small

# Vector store — optional
QDRANT_URL=https://your-cluster.cloud.qdrant.io
QDRANT_API_KEY=...

# Graph store — optional; needs OPENAI_API_KEY too
NEO4J_URI=neo4j+s://your-sandbox.databases.neo4j.io
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=...
GRAPHITI_ENABLED=true

# General web search — optional but strongly recommended, pick one
TAVILY_API_KEY=...
BRAVE_API_KEY=...
SERPER_API_KEY=...

# Research budget (defaults shown)
RESEARCH_MAX_ROUNDS=2
RESEARCH_QUERIES_PER_ROUND=5
RESEARCH_MAX_FETCHES=10
RESEARCH_SUFFICIENCY_DIMENSIONS=3
```

`GET /api/provider-check` tests every provider and returns a sanitised diagnosis
without echoing credentials.

---

## Using open-source models

**Nothing here depends on OpenAI.** Generation speaks the OpenAI chat-completions
wire format, which Groq, Ollama, vLLM, LM Studio, Together and OpenRouter all
implement, and embeddings run on an open model in-process by default.

### Recommended: Groq for generation, local embeddings

```env
LLM_BASE_URL=https://api.groq.com/openai/v1
LLM_API_KEY=gsk_...
LLM_MODEL=llama-3.3-70b-versatile
LLM_FAST_MODEL=llama-3.1-8b-instant
LLM_MAX_CONCURRENCY=2
EMBEDDING_PROVIDER=fastembed
```

Free key at <https://console.groq.com/keys>. No install beyond `requirements.txt`.

- **Two models, split by task.** Claim extraction reads documents and gets the 70B
  model. Planning, verification and answer synthesis are short structured tasks
  and run on the 8B model, which is faster and has far higher rate limits.
- **Embeddings never leave the machine.** `BAAI/bge-small-en-v1.5` runs on ONNX in
  the API process: 384 dimensions, roughly 0.1 s for 64 passages on an Apple M2,
  no server, no key, no GPU.

### Why not a fully local model?

It depends on the machine. Claim extraction needs a model that holds a JSON schema
over a long document, and ~14B parameters is the practical floor for that — about
9 GB of weights. On a 16 GB+ machine, Ollama with `qwen2.5:14b` works
(`LLM_BASE_URL=http://localhost:11434/v1`). On 8 GB it does not fit, and a 7B
model is both slow (15–25 minutes per city) and unreliable at the schema.

### What had to change to make this real

Pointing a base URL at another provider is not enough on its own. Graphiti's
defaults reach OpenAI in three separate places regardless of configuration, and
each was replaced:

| Graphiti default | Problem | Replacement |
|---|---|---|
| `OpenAIClient` | Uses OpenAI's Responses API, which Groq and Ollama do not serve | `OpenAIGenericClient` in `json_object` mode |
| `OpenAIEmbedder` | Calls OpenAI embeddings | `SystemEmbedder` — the same embedder as the vector store |
| `OpenAIRerankerClient` | Calls OpenAI directly and needs token `logprobs`, which open-weights servers do not return | `EmbeddingReranker` — cosine similarity on local embeddings |

The model boundary also had to learn that **a rate limit is not an outage**. The
OpenAI SDK raises the same `RateLimitError` for an exhausted quota and for a
per-minute throttle. The first is terminal and opens a circuit breaker; the
second is waited out using the provider's `retry-after`. Chat and embeddings have
independent circuits, so a throttled generation provider never switches off
healthy local embeddings.

Two efficiency changes serve every provider, but make free tiers viable:

- **Relevance windowing.** The extractor is shown the ~6,000 characters most likely
  to hold claims about the city — scored by city mention, health vocabulary and
  figures — rather than a document's first 14,000, which for journal articles is
  mostly front matter. Quotes are still grounded against the full document.
- **Bounded concurrency** (`LLM_MAX_CONCURRENCY`), so a research round does not
  burst straight into a tokens-per-minute limit.

`GET /api/runtime` reports which models and embedder are actually in use.

## API

| Endpoint | Purpose |
|---|---|
| `POST /api/research` | Run the workflow for a city |
| `GET /api/progress/{city}` | Plain-language live progress |
| `POST /api/ask` | Question answered from stored evidence, with citations |
| `GET /api/report/{city}` | Downloadable HTML brief |
| `GET /api/workflow` | The compiled graph structure |
| `GET /api/runtime` | What the system can actually do right now |
| `GET /api/graph/{city}` | City subgraph from Neo4j |
| `GET /api/stores`, `/api/configuration`, `/api/provider-check` | Store state and diagnostics |
| `GET /api/history`, `/api/history/{city}`, `/api/run/{city}` | Institutional memory |

---

## Deployment

```bash
docker build -t cardio4cities .
docker run --env-file .env -p 8000:8000 cardio4cities
```

Render reads `render.yaml` as a Blueprint and serves the API and frontend from
one URL, binding `$PORT` with `/health` as the health check. The free instance
sleeps when idle and its local disk is ephemeral, so external stores are required
for anything durable.

---

## Tests

```bash
python -m pip install -r requirements-dev.txt
python -m pytest
```

The suite targets the non-negotiable guarantees: a quote must be locatable in its
source, an invented figure must be rejected, the crawl gate must decide before
any network call, and the compiled graph must actually contain the stages and the
cycle the documentation claims.

---

## Documentation and examples

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — system design and trade-offs
- [frontend/architecture.html](frontend/architecture.html) — architecture overview with diagrams (served at `/architecture.html`)
- [frontend/presentation.html](frontend/presentation.html) — 8-slide demonstration deck (served at `/presentation.html`)
- [docs/examples/nairobi-cardio4cities-brief.html](docs/examples/nairobi-cardio4cities-brief.html) — a completed city brief
- [docs/examples/nairobi-research-run.json](docs/examples/nairobi-research-run.json) — the full run record behind it

---

## What was cut, and why

- **OCR for scanned PDFs.** PDFs are read (capped at 60 pages / 12 MB), which
  matters because municipal health strategies and NCD action plans are published
  that way. A scan with no text layer is recorded as needing OCR rather than
  skipped silently.
- **Human review before storage.** The fact-check gate plus a visible gap ledger
  buys most of the safety for a fraction of the time.
- **Conflict resolution.** Two sources that disagree both appear with their
  quotes. Detecting and adjudicating contradiction is real work, deferred.
- **Authentication and multi-tenancy.** Out of scope for a case study; required
  before real use.
