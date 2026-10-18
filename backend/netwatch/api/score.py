"""Network health score and weekly health reports."""

from __future__ import annotations

import asyncio
import re
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api")

_WEEK = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@router.get("/score")
async def score(request: Request, days: int = Query(7, ge=1, le=90)) -> dict:
    return await asyncio.to_thread(request.app.state.health.score, days)


@router.get("/weekly-reports")
async def weekly_reports(request: Request) -> dict:
    h = request.app.state.health
    return {
        "reports": await asyncio.to_thread(h.list),
        "last_full_week": await asyncio.to_thread(h.last_full_week),
    }


@router.get("/weekly-reports/{week}", response_model=None)
async def weekly_report(
    week: str, request: Request, format: str = Query("json", pattern="^(json|md)$")
) -> dict | PlainTextResponse:
    if not _WEEK.match(week):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "week must be YYYY-MM-DD")
    r = await asyncio.to_thread(request.app.state.health.get, week)
    if r is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no report for that week")
    if format == "md":
        return PlainTextResponse(
            r["markdown"],
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="netwatch-week-{week}.md"'},
        )
    return r


class Generate(BaseModel):
    week_start: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")


@router.post("/weekly-reports")
async def generate_weekly_report(body: Generate, request: Request) -> dict:
    """Write (or rewrite) the report for a week; default: the last full week."""
    h = request.app.state.health
    week = body.week_start or await asyncio.to_thread(h.last_full_week)
    try:
        monday = datetime.fromisoformat(week).weekday() == 0
    except ValueError:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "not a valid date") from None
    if not monday:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "weeks start on a Monday")
    return await asyncio.to_thread(h.generate, week)
