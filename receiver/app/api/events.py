from fastapi import APIRouter, Depends, HTTPException, status

from app.auth import TokenClaims, verify_jwt
from app.ingest import ingest
from app.schemas import EventIn

router = APIRouter(prefix="/api/v1", tags=["events"])


@router.post("/events", status_code=status.HTTP_201_CREATED)
async def post_event(
    event: EventIn,
    claims: TokenClaims = Depends(verify_jwt),
) -> dict:
    if not event.service:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Field 'service' is required for multi-project routing",
        )
    if event.service != claims.project:
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
