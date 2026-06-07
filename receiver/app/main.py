import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.events import router as events_router
from app.api.stats import router as stats_router
from app.ingest import ingest

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await ingest.start()
    try:
        yield
    finally:
        await ingest.stop()


app = FastAPI(title="da-analytics receiver", lifespan=lifespan)

# CORS for browser-side telemetry clients (e.g. SPA frontends posting
# directly to /api/v1/events). Allow-list comes from CORS_ORIGINS env
# var (comma-separated). Defaults to "*" which is fine for local-dev;
# tighten in production via env.
_origins_raw = os.environ.get("CORS_ORIGINS", "*")
_origins = [o.strip() for o in _origins_raw.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=False,
    allow_methods=["POST", "GET", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

app.include_router(events_router)
app.include_router(stats_router)
