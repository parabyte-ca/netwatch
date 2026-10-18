"""Ask NetWatch: plain-language questions about the network, answered by Claude."""

from __future__ import annotations

import asyncio
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from ..auth import COOKIE_NAME
from ..services.assistant import AskError

router = APIRouter(prefix="/api")


class Turn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=8000)


class Question(BaseModel):
    model_config = ConfigDict(extra="forbid")
    messages: list[Turn] = Field(min_length=1, max_length=40)


@router.get("/ask")
async def ask_status(request: Request) -> dict:
    a = request.app.state.assistant
    cfg = await asyncio.to_thread(a.config)
    return {"available": await asyncio.to_thread(a.available), "model": cfg["model"]}


@router.post("/ask")
async def ask(body: Question, request: Request) -> dict:
    if body.messages[-1].role != "user":
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "the last message must be a question"
        )
    cookies = {COOKIE_NAME: request.cookies.get(COOKIE_NAME, "")}
    try:
        a = await request.app.state.assistant.ask([m.model_dump() for m in body.messages], cookies)
    except AskError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    return {
        "answer": a.text,
        "tools": a.tools,
        "model": a.model,
        "usage": a.usage,
        "stop_reason": a.stop_reason,
    }
