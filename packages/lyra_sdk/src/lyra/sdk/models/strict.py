"""Strict base models and validation conventions for SDK schemas."""

from pydantic import BaseModel, ConfigDict


class StrictBaseModel(BaseModel):
    """Pydantic base model that rejects undeclared input fields."""

    model_config = ConfigDict(extra="forbid")
