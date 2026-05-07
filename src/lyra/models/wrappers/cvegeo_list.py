from typing import Annotated, ClassVar, Literal

from pydantic import AfterValidator

from lyra.models.base import StrictBaseModel


def validate_cvegeos(
    value: list[str],
) -> list[str]:
    unique_lens = {len(x) for x in value}
    if len(unique_lens) > 1:
        err = "All CVEGEO strings must have the same length."
        raise ValueError(err)

    allowed_lens = {2, 5, 9, 13, 16}
    found_len = unique_lens.pop()
    if found_len not in allowed_lens:
        err = (
            f"CVEGEO strings must have length in {allowed_lens}, but got "
            f"length {found_len}."
        )
        raise ValueError(err)

    return value


class CVEGEOListWrapper(StrictBaseModel):
    DATA_TYPE_DESCRIPTION: ClassVar[str] = (
        "A list of CVEGEOs. All CVEGEOs must have the same length, which "
        "determines their geographic level."
    )
    data_type: Literal["cvegeo_list"]
    value: Annotated[list[str], AfterValidator(validate_cvegeos)]
