# CARDIO4Cities Intelligence Studio

Evidence-first city research for the CARDIO4Cities case study.

## Run locally

```bash
python -m pip install -r requirements.txt
uvicorn backend.app.main:app --reload
```

Open http://127.0.0.1:8000 and enter a city. The current vertical slice performs live DuckDuckGo discovery, checks `robots.txt` before extraction, runs through a compiled LangGraph workflow, preserves source evidence, and visibly records knowledge gaps.

## Free deployment

### Render

1. Create a GitHub repository and push this folder.
2. In Render, choose **New + -> Blueprint** and select the repository.
3. Render reads `render.yaml`, builds the Docker image, and provides a public `onrender.com` URL.
4. In the Render service environment, add the values listed in `.env.example`: Qdrant, Neo4j Sandbox, and OpenAI credentials.
5. Deploy and open `/health` on the generated URL.

The service binds to Render's `$PORT`, uses `/health` as its health check, and serves the frontend and API from one URL. The free instance may sleep when idle and local SQLite data is ephemeral, so use managed PostgreSQL before treating it as production storage.

### GitHub Pages frontend

GitHub Pages can host the static frontend at `https://yagnagudipalli.github.io/cvd_nova/`. It cannot host the FastAPI backend, so deploy the backend with the Render Blueprint first.

Then add a GitHub Actions repository secret named `BACKEND_API_URL` containing the Render API URL, for example `https://cardio4cities-intelligence-studio.onrender.com`. The workflow in `.github/workflows/pages.yml` publishes the frontend and injects that backend URL at deploy time.

### Local production-shaped run

```bash
docker build -t cardio4cities .
docker run --env-file .env -p 8000:8000 cardio4cities
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the system design and [docs/PRESENTATION.md](docs/PRESENTATION.md) for the 8-slide demonstration deck.

## Provider configuration

The project reads provider settings from the local `.env` file. That file is ignored by git.

1. Create a free Qdrant Cloud cluster, open **API Keys**, create a key, and copy the cluster URL and key into `QDRANT_URL` and `QDRANT_API_KEY`.
2. Open the Neo4j Graph Database Sandbox, create or open a project, and copy its Bolt URI, username, and password into `NEO4J_URI`, `NEO4J_USERNAME`, and `NEO4J_PASSWORD`. The URI normally starts with `neo4j+s://`.
3. Add an LLM key to `OPENAI_API_KEY`. Graphiti uses the LLM for entity extraction and relationship resolution.
4. Set `GRAPHITI_ENABLED=true`.
5. Restart the server and open `GET /api/configuration` to see whether anything is still missing. Then open the Runtime status tab in the UI.

Example shape only, never commit real values:

```env
QDRANT_URL=https://your-cluster.cloud.qdrant.io
QDRANT_API_KEY=your-qdrant-key
NEO4J_URI=neo4j+s://your-sandbox.databases.neo4j.io
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=your-sandbox-password
OPENAI_API_KEY=your-llm-key
GRAPHITI_ENABLED=true
```

The SDKs installed for the integration are `qdrant-client`, `neo4j`, and `graphiti-core`.

## Planned integrations

- LangGraph for durable agent orchestration and retry/quarantine transitions
- PostgreSQL for research runs, sources, facts, and reports
- Qdrant for evidence passage retrieval
- Graphiti and Neo4j Sandbox for entity relationships and graph retrieval

The first slice deliberately keeps unsupported findings out of the published fact ledger. See `.env.example` for integration configuration.

## Knowledge layer

`GET /api/stores` reports the live readiness of the relational, Qdrant, and Graphiti/Neo4j stores. `POST /api/ask` retrieves only verified facts from the latest city run and returns an explicit insufficiency response when the ledger cannot support the question. Set `QDRANT_URL`, `NEO4J_URI`, and `GRAPHITI_ENABLED=true` for deployment integrations; the local experience remains honest when those providers are not configured.

`GET /api/history` returns recent persisted research runs. `GET /api/report/{city}` downloads an evidence-first HTML brief containing verified findings, source links, crawl decisions, knowledge gaps, and methodology.