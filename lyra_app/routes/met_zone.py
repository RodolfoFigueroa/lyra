import asyncio

from fastapi import APIRouter, HTTPException
from lyra.sdk.models.strict import StrictBaseModel

from lyra_app.db.connection import engine
from lyra_app.loaders.db import get_met_zone_code_from_name

router = APIRouter()


class MetZoneCodeResponse(StrictBaseModel):
    cve_met: str
    nom_met: str


@router.get("/lookups/met-zones")
async def get_met_zone_code(name: str) -> MetZoneCodeResponse:
    with engine.connect() as conn:
        result = await asyncio.to_thread(get_met_zone_code_from_name, name, conn=conn)

    if result is None:
        raise HTTPException(
            status_code=404,
            detail="No metropolitan zone matched the given name.",
        )

    cve_met, nom_met = result
    return MetZoneCodeResponse(cve_met=cve_met, nom_met=nom_met)
