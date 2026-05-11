import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.events import router as events_router
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
app.include_router(events_router)
