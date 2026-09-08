"""FastAPI application."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import router
from app.config import settings
from app.db.session import init_db
from app.observability.logging import configure_logging, get_logger

log = get_logger("api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging(level="INFO")
    init_db()
    log.info(
        "api_started",
        dataforseo_mode=settings.dataforseo_mode,
        model=settings.azure_model,
    )
    yield


app = FastAPI(
    title="Agentic Search Intelligence",
    description="Multi-agent DAG for brand search and AI visibility analysis.",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "dataforseo_mode": settings.dataforseo_mode}
