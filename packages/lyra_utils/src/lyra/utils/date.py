"""Utilities for computing date ranges from calendar month and season inputs."""

import calendar
from typing import Literal


def get_date_range(month: int, year: int) -> tuple[str, str]:
    """Return the start and end date strings for a given month and year.

    Args:
        month: Month number (1-12).
        year: Four-digit year.

    Returns:
        A tuple of (start_date, end_date) as ``"YYYY-MM-DD"`` strings,
        representing the first and last day of the month.
    """
    month_str = str(month).rjust(2, "0")

    start = f"{year}-{month_str}-01"

    _, end_day = calendar.monthrange(year, month)
    end_day_str = str(end_day).rjust(2, "0")
    end = f"{year}-{month_str}-{end_day_str}"

    return start, end


def get_season_date_range(
    season: Literal["winter", "spring", "summer", "autumn"],
    year: int,
) -> tuple[str, str]:
    """Return the start and end date strings for a meteorological season.

    Seasons are defined as:
    - **Winter**: December of the previous year through February.
    - **Spring**: March through May.
    - **Summer**: June through August.
    - **Autumn**: September through November.

    Args:
        season: One of ``"winter"``, ``"spring"``, ``"summer"``, or
            ``"autumn"``.
        year: The year the season ends in (for winter, December belongs to
            ``year - 1``).

    Returns:
        A tuple of (start_date, end_date) as ``"YYYY-MM-DD"`` strings.

    Raises:
        ValueError: If ``season`` is not one of the four valid values.
    """
    if season == "winter":
        start, _ = get_date_range(12, year - 1)
        _, end = get_date_range(2, year)
    elif season == "spring":
        start, _ = get_date_range(3, year)
        _, end = get_date_range(5, year)
    elif season == "summer":
        start, _ = get_date_range(6, year)
        _, end = get_date_range(8, year)
    elif season == "autumn":
        start, _ = get_date_range(9, year)
        _, end = get_date_range(11, year)
    else:
        err = (
            f"Invalid season: {season}. Must be one of 'winter', 'spring', "
            "'summer', or 'autumn'."
        )
        raise ValueError(err)

    return start, end
