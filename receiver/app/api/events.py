import logging

from fastapi import APIRouter, Depends, HTTPException, status

from app.auth import TokenClaims, verify_jwt
from app.ingest import ingest
from app.schemas import EventIn

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["events"])


@router.post("/events", status_code=status.HTTP_201_CREATED)
async def post_event(
    event: EventIn,
    claims: TokenClaims = Depends(verify_jwt),
) -> dict:
    if not event.service:
        logger.warning(
            "422: missing service field, token project=%r, event.type=%r",
            claims.project, event.type,
        )
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Field 'service' is required for multi-project routing",
        )
    if event.service != claims.project:
        logger.warning(
            "403: project mismatch — token=%r, event.service=%r, event.type=%r",
            claims.project, event.service, event.type,
        )
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"Token issued for project '{claims.project}', "
            f"cannot submit events for '{event.service}'",
        )

    queued = ingest.enqueue(event)
    if not queued:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "ingest queue is full",
        )
    return {"status": "accepted", "event_id": str(event.event_id)}


@router.get("/healthz", include_in_schema=False)
async def healthz() -> dict:
    return {"ok": True}
