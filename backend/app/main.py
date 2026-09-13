import logging
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

from .config import settings  # noqa: E402
from .version import APP_VERSION  # noqa: E402  (import after load_dotenv so env is populated)
from .research import router as research_router  # noqa: E402


BASE_DIR = Path(__file__).resolve().parents[2]
STATIC_DIR = BASE_DIR / "frontend"

app = FastAPI(
    title="CARDIO4Cities Intelligence Studio",
    description="Evidence-first city health research: live research, independent verification, three datastores.",
    version=APP_VERSION,
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.include_router(research_router, prefix="/api")


@app.on_event("startup")
async def warm_embeddings() -> None:
    """Load the local embedding model before the first request needs it.

    Loading takes ~14 s; paid inside a research run, it showed up as a slow
    indexing stage on the first city after every restart.
    """
    import asyncio

    from .llm import language_model

    async def warm() -> None:
        try:
            await language_model.embed(["warm-up"])
        except Exception:
            logging.getLogger("cardio4cities").warning("embedding_warmup_failed")

    asyncio.create_task(warm())


@app.get("/health")
def health() -> dict[str, object]:
    return {
        "status": "ok",
        "service": "cardio4cities-api",
        "version": APP_VERSION,
        "llm": settings.llm_configured,
        "vector": settings.qdrant_configured,
        "graph": settings.graph_configured,
    }


def _static(name: str, media_type: str) -> FileResponse:
    # Revalidate on every load. Without an explicit policy browsers guess a
    # freshness lifetime from Last-Modified and kept an old app.js for hours
    # after a deploy. With the ETag, an unchanged file still costs only a 304.
    return FileResponse(STATIC_DIR / name, media_type=media_type, headers={"Cache-Control": "no-cache"})


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return _static("index.html", "text/html")


@app.get("/app.js", include_in_schema=False)
def javascript() -> FileResponse:
    return _static("app.js", "text/javascript")


@app.get("/styles.css", include_in_schema=False)
def styles() -> FileResponse:
    return _static("styles.css", "text/css")


@app.get("/runtime-config.js", include_in_schema=False)
def runtime_config() -> FileResponse:
    return _static("runtime-config.js", "text/javascript")


@app.get("/architecture.html", include_in_schema=False)
def architecture() -> FileResponse:
    return _static("architecture.html", "text/html")


@app.get("/presentation.html", include_in_schema=False)
def presentation() -> FileResponse:
    return _static("presentation.html", "text/html")
