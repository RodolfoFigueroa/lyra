"""Models for metric and queue lookup responses."""

from lyra.sdk.models.strict import StrictBaseModel
from pydantic import Field


class MetZoneCodeResponse(StrictBaseModel):
    """Metropolitan-zone code and display name."""

    cve_met: str = Field(min_length=1)
    nom_met: str = Field(min_length=1)


__all__ = ["MetZoneCodeResponse"]
