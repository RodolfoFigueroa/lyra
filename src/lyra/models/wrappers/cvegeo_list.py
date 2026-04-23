from typing import Literal, Annotated
from pydantic import AfterValidator
from lyra.models.base import StrictBaseModel


def validate_cvegeos(
    value: list[str],
):
    unique_lens = set(len(x) for x in value)
    if len(unique_lens) > 1:
        err = "All CVEGEO strings must have the same length."
        raise ValueError(err)

    allowed_lens = {2, 5, 9, 13, 16}
    found_len = unique_lens.pop()
    if found_len not in allowed_lens:
        err = f"CVEGEO strings must have length in {allowed_lens}, but got length {found_len}."
        raise ValueError(err)

    return value


class CVEGEOListWrapper(StrictBaseModel):
    data_type: Literal["cvegeo_list"]
    value: Annotated[list[str], AfterValidator(validate_cvegeos)]
