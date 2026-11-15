"""Who's home: people, their devices, presence now and the last days."""

from __future__ import annotations

import asyncio
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from ..services.device_groups import COLOURS
from ..services.discovery.inventory import device_label
from ..services.people import MAX_DEVICES, PeopleError
from .auth import security_event

router = APIRouter(prefix="/api")

Colour = Literal[COLOURS]  # type: ignore[valid-type]


class PersonIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=40)
    colour: Colour
    device_ids: list[int] = Field(default=[], max_length=MAX_DEVICES)


class PersonPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=40)
    colour: Colour | None = None
    device_ids: list[int] | None = Field(default=None, max_length=MAX_DEVICES)


class SettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    away_after_min: int = Field(ge=5, le=240)


def _presence(request: Request):
    return request.app.state.presence


async def _run(fn, *args, **kw):
    try:
        return await asyncio.to_thread(fn, *args, **kw)
    except PeopleError as exc:
        code = 404 if str(exc) == "no such person" else status.HTTP_422_UNPROCESSABLE_ENTITY
        raise HTTPException(code, str(exc)) from None


async def _refresh(request: Request) -> None:
    await asyncio.to_thread(_presence(request).tick)


@router.get("/people")
async def list_people(request: Request) -> dict:
    p = _presence(request)
    return {
        "people": await asyncio.to_thread(p.status),
        "settings": await asyncio.to_thread(p.settings),
        "colours": list(COLOURS),
    }


@router.post("/people", status_code=status.HTTP_201_CREATED)
async def create_person(body: PersonIn, request: Request) -> dict:
    person = await _run(_presence(request).people.create, body.name, body.colour, body.device_ids)
    await _refresh(request)
    await security_event(request, "info", f"Person added to who's home: {person['name']}",
                         {"action": "person_created", "person_id": person["id"]})  # fmt: skip
    return person


@router.patch("/people/{person_id}")
async def edit_person(person_id: int, body: PersonPatch, request: Request) -> dict:
    store = _presence(request).people
    person = await _run(store.update, person_id, **body.model_dump(exclude_unset=True))
    await _refresh(request)
    return person


@router.delete("/people/{person_id}")
async def delete_person(person_id: int, request: Request) -> dict:
    store = _presence(request).people
    person = await asyncio.to_thread(store.get, person_id)
    if person is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such person")
    gone = await asyncio.to_thread(store.delete, person_id)
    await security_event(
        request,
        "info",
        f"Person removed from who's home: {person['name']} "
        f"({gone} arrivals and departures deleted)",
        {"action": "person_deleted", "person_id": person_id},
    )
    return {"events_deleted": gone}


@router.get("/people/{person_id}/history")
async def person_history(person_id: int, request: Request, days: int = 7) -> dict:
    if not 1 <= days <= 31:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "days must be 1 to 31")
    p = _presence(request)
    person = next((x for x in await asyncio.to_thread(p.status) if x["id"] == person_id), None)
    if person is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such person")
    store = request.app.state.discovery.inventory.store

    def labels() -> list[dict]:
        out = []
        for did in person["device_ids"]:
            d = store.get(did)
            if d is not None:
                out.append({"id": did, "label": device_label(d)})
        return out

    hist = await asyncio.to_thread(p.history, person_id, days)
    return {"person": person, "devices": await asyncio.to_thread(labels), **hist}


@router.put("/people-settings")
async def save_settings(body: SettingsIn, request: Request) -> dict:
    return await _run(_presence(request).set_settings, body.away_after_min)
