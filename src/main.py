import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.staticfiles import StaticFiles

from src.config import settings
from src.db import Database
from src.models import HealthResponse
from src.web.routes import check_auth, get_db, router, set_db

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

db = Database(settings.db_path)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Initializing database at %s", settings.db_path)
    db.init_schema()
    set_db(db)
    logger.info("Earmark started on port %s", settings.port)
    yield
    logger.info("Earmark shutting down")


app = FastAPI(title="Earmark", version="0.1.0", lifespan=lifespan)

# Include API routes with auth dependency
app.include_router(router, dependencies=[Depends(check_auth)])


@app.get("/api/health")
async def health() -> HealthResponse:
    last_log = db.get_last_sync_log()
    return HealthResponse(
        last_sync=last_log["created_at"] if last_log else None,
    )


# Serve static files last (SPA frontend)
static_dir = Path(__file__).parent / "web" / "static"
if static_dir.exists():
    app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")
