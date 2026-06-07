"""Read-side aggregates for project admin panels.

A single JSON endpoint that runs the same kind of rollups the Grafana
dashboards use, scoped to the project in the JWT (multi-tenant safe — a
citysignal token can only read citysignal rows). Powers lightweight
in-app admin dashboards without exposing Postgres or Grafana.
"""

import logging

from fastapi import APIRouter, Depends
from sqlalchemy import text

from app.auth import TokenClaims, verify_jwt
from app.db import SessionLocal

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["stats"])


@router.get("/stats")
async def get_stats(claims: TokenClaims = Depends(verify_jwt)) -> dict:
    svc = claims.project

    async def scalar(sql: str) -> int:
        async with SessionLocal() as s:
            r = await s.execute(text(sql), {"svc": svc})
            v = r.scalar()
            return int(v or 0)

    async def rows(sql: str) -> list[dict]:
        async with SessionLocal() as s:
            r = await s.execute(text(sql), {"svc": svc})
            return [dict(m) for m in r.mappings().all()]

    base = "FROM events WHERE service = :svc"

    events_24h = await scalar(f"SELECT count(*) {base} AND received_at > NOW() - INTERVAL '24 hours'")
    events_7d = await scalar(f"SELECT count(*) {base} AND received_at > NOW() - INTERVAL '7 days'")
    events_total = await scalar(f"SELECT count(*) {base}")

    user_expr = "payload->'scenario'->>'user_id'"
    dev_expr = "payload->'scenario'->>'device_id'"
    users_24h = await scalar(f"SELECT count(DISTINCT {user_expr}) {base} AND {user_expr} IS NOT NULL AND received_at > NOW() - INTERVAL '24 hours'")
    users_7d = await scalar(f"SELECT count(DISTINCT {user_expr}) {base} AND {user_expr} IS NOT NULL AND received_at > NOW() - INTERVAL '7 days'")
    users_total = await scalar(f"SELECT count(DISTINCT {user_expr}) {base} AND {user_expr} IS NOT NULL")
    # devices = unique browsers/sessions incl. non-Telegram (web) visits
    devices_total = await scalar(f"SELECT count(DISTINCT {dev_expr}) {base} AND {dev_expr} IS NOT NULL")

    sessions_24h = await scalar(f"SELECT count(DISTINCT request_id) {base} AND received_at > NOW() - INTERVAL '24 hours'")
    errors_24h = await scalar(f"SELECT count(*) {base} AND received_at > NOW() - INTERVAL '24 hours' AND (type LIKE 'error.%' OR status = 'error')")

    top_types = await rows(
        f"SELECT type, count(*) AS n {base} AND received_at > NOW() - INTERVAL '7 days' "
        "GROUP BY type ORDER BY n DESC LIMIT 12"
    )

    per_day = await rows(
        f"SELECT to_char(date_trunc('day', received_at), 'YYYY-MM-DD') AS day, count(*) AS n {base} "
        "AND received_at > NOW() - INTERVAL '14 days' GROUP BY 1 ORDER BY 1"
    )

    # Journey funnel — distinct sessions reaching each step (7d window).
    async def step(where: str) -> int:
        return await scalar(
            f"SELECT count(DISTINCT request_id) {base} AND received_at > NOW() - INTERVAL '7 days' AND {where}"
        )

    funnel = [
        {"step": "Старт", "n": await step("type = 'session.start'")},
        {"step": "Лендинг", "n": await step("type = 'cs.landing.enter'")},
        {"step": "Имя", "n": await step("type = 'cs.name.submit'")},
        {"step": "Лента", "n": await step("type = 'page.view' AND payload->'scenario'->>'route' = '/cs/feed'")},
        {"step": "Профиль", "n": await step("payload->'scenario'->>'route' = '/cs/profile'")},
    ]

    return {
        "service": svc,
        "events": {"d1": events_24h, "d7": events_7d, "total": events_total},
        "users": {"d1": users_24h, "d7": users_7d, "total": users_total, "devices": devices_total},
        "sessions_24h": sessions_24h,
        "errors_24h": errors_24h,
        "top_types": top_types,
        "per_day": per_day,
        "funnel": funnel,
    }
