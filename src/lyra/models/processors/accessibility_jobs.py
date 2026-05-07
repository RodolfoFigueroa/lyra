import re
from typing import Annotated, Literal

from pydantic import AfterValidator

from lyra.models.base import StrictBaseModel


def _validate_regex(v: str) -> str:
    try:
        re.compile(v)
    except re.error as e:
        err = f"Invalid regex pattern: {e}"
        raise ValueError(err) from e
    return v


RegexPattern = Annotated[str, AfterValidator(_validate_regex)]


class JobGroupModel(StrictBaseModel):
    pattern: RegexPattern = r"\d{6}"
    edge_weights: Literal["length", "travel_time"]
    max_weight: float
    network_type: Literal["drive", "walk"]
