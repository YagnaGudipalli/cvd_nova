# Assignment Requirements Checklist

| Assignment requirement | Implementation | Verification / remaining action |
|---|---|---|
| Live internet research | DuckDuckGo search at request time | Run an unseen city during demo |
| Reusable intelligence asset | Persisted research runs, source ledger, history, reports | Managed PostgreSQL recommended for production |
| Explore information | Overview, Evidence, Knowledge graph, History, and Ask tabs | Available locally |
| Evidence behind every fact | Source URL, title, quote, retrieval timestamp, verification status | Expand Evidence rows during demo |
| Uncertainty and missing information | Knowledge gaps, scope labels, insufficient-evidence Q&A | Test an unsupported question |
| LangGraph orchestration | Compiled LangGraph research/report workflow | Next hardening: expose every agent as a separate graph node |
| Crawlability detection before crawling | `robots.txt` check before extraction | Click Crawlability detection step |
| Independent fact checking | Separate `_fact_check` gate; rejected claims become gaps | Test another-city source and unsupported quote |
| Graphiti + Neo4j Sandbox | Graphiti episode ingestion and Neo4j query endpoint | Requires OpenAI quota for new Graphiti extraction |
| Relational datastore | SQLite relational ledger for demo | Replace with managed PostgreSQL for production deployment |
| Vector datastore | Qdrant collection with city-filtered evidence vectors | Verify collection `cardio4cities_evidence_v2` |
| No fabrication | City filters, source quotes, scope caveats, insufficient-evidence fallback | Test Chennai after Pune |
| Deployed URL | Dockerfile and Render Blueprint | Push to GitHub and create Render Blueprint |
| Downloadable research report | HTML download endpoint | `GET /api/report/{city}` |
| Architecture overview | `docs/ARCHITECTURE.md` | Include in repository |
| Example output | Research history and downloadable reports | Generate one after deployment |
| Presentation deck | `docs/PRESENTATION.md`, 8-slide content | Convert to slides for presentation |
| Setup and reproducibility | README, `.env.example`, Dockerfile, Render config | Fill deployment secrets in Render |
| Evaluation and communication | Admin metrics, traces, guardrail audit, provider status | Show Runtime tab during demo |

## Current external blockers

- A public URL requires the user's GitHub/Render account authorization.
- Graphiti episode extraction requires an OpenAI key with available quota; the app records quota errors instead of hiding them.
- SQLite is intentionally used for the local/demo relational ledger. Managed PostgreSQL should be selected for a durable multi-user deployment.
