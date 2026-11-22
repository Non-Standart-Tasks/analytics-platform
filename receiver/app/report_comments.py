"""Refresh Grafana's copy of public AMA report discussions."""

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import httpx
from sqlalchemy import text

from app.db import SessionLocal

API = "https://digital-assistant.a-m-a.com/api/v1/"
CREDENTIALS = Path("/run/secrets/ama_report_reader")
INTERVAL = 600
PAGE_SIZE = 100
log = logging.getLogger(__name__)

UPSERT = text("""
    INSERT INTO ama_report_discussions (report_id, title, created_at, comments)
    VALUES (:id, :title, :created_at, CAST(:comments AS jsonb))
    ON CONFLICT (report_id) DO UPDATE SET
        title = excluded.title, comments = excluded.comments
    WHERE (ama_report_discussions.title, ama_report_discussions.comments)
          IS DISTINCT FROM (excluded.title, excluded.comments)
""")


def utc_timestamp(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


async def read_threads(api: httpx.AsyncClient) -> list[dict]:
    credentials = json.loads(CREDENTIALS.read_text())
    login = await api.post(
        "login/access-token",
        data={
            "username": credentials["username"],
            "password": credentials["password"],
        },
    )
    login.raise_for_status()
    api.headers["Authorization"] = "Bearer " + login.json()["access_token"]

    threads = {}
    offset = 0
    expected = None
    while True:
        page = await api.get(
            "bug-reports/", params={"skip": offset, "limit": PAGE_SIZE}
        )
        page.raise_for_status()
        body = page.json()
        if expected is None:
            expected = body["count"]
        reports = body["data"]
        if expected != body["count"] or (not reports and offset < expected):
            raise ValueError("report list changed during pagination")
        for report in reports:
            report_id = UUID(report["id"])
            if report_id in threads:
                raise ValueError("duplicate report during pagination")
            response = await api.get(
                f"bug-reports/{report_id}/comments",
                params={"include_internal": "false"},
            )
            response.raise_for_status()
            comments = [
                {
                    "id": str(UUID(c["id"])),
                    "at": utc_timestamp(c["created_at"]).isoformat(),
                    "role": c["author_role"],
                    "message": c["message"],
                    "attachments": len(c["attachments"]),
                }
                for c in response.json()
                if c["is_internal"] is False
            ]
            threads[report_id] = {
                "id": report_id,
                "title": report["title"],
                "created_at": utc_timestamp(report["created_at"]),
                "comments": json.dumps(comments, ensure_ascii=False),
            }
        offset += len(reports)
        if offset >= expected:
            return list(threads.values())


async def refresh() -> None:
    async with SessionLocal.begin() as db:
        # A second worker must not replace a newer snapshot with an older one.
        lock = await db.scalar(text("SELECT pg_try_advisory_xact_lock(620207401)"))
        if not lock:
            return
        async with httpx.AsyncClient(
            base_url=API, timeout=15, follow_redirects=False
        ) as api:
            threads = await read_threads(api)
        if threads:
            await db.execute(UPSERT, threads)
        await db.execute(
            text(
                "DELETE FROM ama_report_discussions WHERE NOT (report_id = ANY(CAST(:ids AS uuid[])))"
            ),
            {"ids": [thread["id"] for thread in threads]},
        )
    log.info("Synced %d AMA report threads", len(threads))


async def main() -> None:
    while True:
        try:
            await refresh()
        except Exception as exc:
            # Do not log credentials, comment text or SQL parameter dumps.
            status = (
                exc.response.status_code
                if isinstance(exc, httpx.HTTPStatusError)
                else ""
            )
            log.error("Report sync failed: %s %s", type(exc).__name__, status)
        await asyncio.sleep(INTERVAL)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
