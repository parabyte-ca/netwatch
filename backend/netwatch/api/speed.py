"""Speed against your plan: the nightly load test as a share of the plan's speeds."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request, status

router = APIRouter(prefix="/api")


@router.get("/speed-plan")
async def speed_plan(request: Request, days: int = 90) -> dict:
    if not 7 <= days <= 365:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "days must be 7 to 365")
    return await asyncio.to_thread(request.app.state.speed_plan.view, days)
