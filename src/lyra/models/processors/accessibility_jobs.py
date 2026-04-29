import re
from typing import Annotated

from pydantic import AfterValidator
from lyra.models.base import StrictBaseModel
from typing import Literal


def _validate_regex(v: str) -> str:
    try:
        re.compile(v)
    except re.error as e:
        raise ValueError(f"Invalid regex pattern: {e}") from e
    return v


RegexPattern = Annotated[str, AfterValidator(_validate_regex)]


class JobGroupModel(StrictBaseModel):
    pattern: RegexPattern = r"\d{6}"
    edge_weights: Literal["length", "travel_time"]
    max_weight: float
