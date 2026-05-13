import asyncio

from fastapi import APIRouter, HTTPException
from lyra.sdk.models import StrictBaseModel

from lyra_app.functions.load.db import get_met_zone_code_from_name

router = APIRouter()


class MetZoneCodeResponse(StrictBaseModel):
    cve_met: str
    nom_met: str


@router.get("/met_zone_code")
async def get_met_zone_code(name: str) -> MetZoneCodeResponse:
    result = await asyncio.to_thread(get_met_zone_code_from_name, name)

    if result is None:
        raise HTTPException(
            status_code=404,
            detail="No metropolitan zone matched the given name.",
        )

    cve_met, nom_met = result
    return MetZoneCodeResponse(cve_met=cve_met, nom_met=nom_met)
