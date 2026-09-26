"""Canonical output units shared by plugin declarations and result contracts."""

from enum import StrEnum


class Unit(StrEnum):
    """Supported output units; values declare meaning without converting data.

    YEAR measures a duration, while CALENDAR_YEAR identifies a calendar year.
    RATIO expresses a quotient and PERCENT expresses a value per hundred.
    SCORE uses a workflow-defined scale explained in the column description.
    DIMENSIONLESS denotes a known unitless quantity. Use explicit None when a
    unit does not apply, never to stand for an unknown unit.
    """

    MILLIMETRE = "mm"
    METRE = "m"
    KILOMETRE = "km"
    SQUARE_METRE = "m2"
    SQUARE_KILOMETRE = "km2"
    HECTARE = "ha"
    DEGREE_CELSIUS = "degC"
    KELVIN = "K"
    SECOND = "s"
    DAY = "day"
    YEAR = "year"
    CALENDAR_YEAR = "calendar_year"
    COUNT = "count"
    RATIO = "ratio"
    PERCENT = "percent"
    SCORE = "score"
    DIMENSIONLESS = "dimensionless"
